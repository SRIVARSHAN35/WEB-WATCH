import logging
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, password_validation
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

from .serializers import RegistrationSerializer
from .auth_utils import unique_username_for_email
from .models import DeletedAccountMarker, GoogleIdentity, record_account_activity


logger = logging.getLogger(__name__)
User = get_user_model()


class _PasswordResetIPThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PasswordResetRequestThrottle(_PasswordResetIPThrottle):
    scope = "password_reset_request"


class PasswordResetConfirmThrottle(_PasswordResetIPThrottle):
    scope = "password_reset_confirm"


class AccountDeletionThrottle(UserRateThrottle):
    scope = "account_deletion"


def public_user(user):
    return {
        "id": user.pk,
        "email": user.email,
        "name": user.get_full_name().strip() or user.email,
    }


def token_response(user, response_status=status.HTTP_200_OK):
    token, _ = Token.objects.get_or_create(user=user)
    return Response(
        {"token": token.key, "user": public_user(user)},
        status=response_status,
    )


def create_google_user(email, name):
    first_name, _, last_name = (name or "").partition(" ")
    return User.objects.create_user(
        username=unique_username_for_email(email),
        email=email,
        password=None,
        first_name=first_name,
        last_name=last_name,
    )


def _google_verification_failure(error):
    if isinstance(error, TransportError):
        return (
            "google_verifier_unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Google sign-in verification is temporarily unavailable.",
            "verifier_unavailable",
        )

    error_summary = str(error).lower()
    if any(marker in error_summary for marker in ("audience", "recipient", "aud claim")):
        return (
            "google_audience_mismatch",
            status.HTTP_400_BAD_REQUEST,
            "Google returned a token for a different OAuth client. Check that the frontend and backend use the same Web client ID.",
            "audience_mismatch",
        )

    if isinstance(error, GoogleAuthError) and not isinstance(error, ValueError):
        return (
            "google_issuer_invalid",
            status.HTTP_400_BAD_REQUEST,
            "Google returned a token with an invalid issuer.",
            "issuer_rejected",
        )
    return (
        "google_token_invalid",
        status.HTTP_400_BAD_REQUEST,
        "Google could not verify this sign-in. Please try again.",
        "invalid_token",
    )


def _google_verification_reason(error):
    """Return a safe diagnostic category without logging token/error contents."""
    message = str(error).lower()
    if any(marker in message for marker in ("audience", "recipient", "aud claim")):
        return "audience_mismatch"
    if "issuer" in message:
        return "issuer_rejected"
    if any(marker in message for marker in ("expired", "expiry", "exp claim")):
        return "token_expired"
    if any(
        marker in message
        for marker in (
            "too early",
            "issued in the future",
            "clock skew",
            "not yet valid",
            "nbf",
        )
    ):
        return "token_time_invalid"
    if any(marker in message for marker in ("signature", "certificate", "certificates")):
        return "signature_verification_failed"
    if any(marker in message for marker in ("segment", "malformed", "decode", "jwt")):
        return "token_malformed"
    return "unclassified_verification_failure"


def verified_google_claims(request):
    if not settings.GOOGLE_CLIENT_ID:
        return None, Response(
            {
                "code": "google_not_configured",
                "detail": "Google sign-in is not configured on the server.",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    credential = request.data.get("credential")
    if not isinstance(credential, str) or not credential:
        return None, Response(
            {"detail": "A Google credential is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        claims = id_token.verify_oauth2_token(
            credential,
            GoogleRequest(),
            settings.GOOGLE_CLIENT_ID,
        )
    except (ValueError, GoogleAuthError) as error:
        code, response_status, detail, category = _google_verification_failure(error)
        reason = _google_verification_reason(error)
        logger.warning(
            "Google ID token verification failed "
            "(category=%s, reason=%s, exception_type=%s)",
            category,
            reason,
            type(error).__name__,
        )
        return None, Response(
            {"code": code, "detail": detail, "diagnostic": reason},
            status=response_status,
        )

    email_claim = claims.get("email")
    if not isinstance(email_claim, str):
        return None, Response(
            {"detail": "A verified Google email is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    email = email_claim.strip().lower()
    email_verified = claims.get("email_verified") in (True, "true")
    if not email or not email_verified:
        return None, Response(
            {
                "code": "google_email_unverified",
                "detail": "A verified Google email is required.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return {**claims, "email": email}, None


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    serializer = RegistrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        with transaction.atomic():
            user = serializer.save()
    except IntegrityError as error:
        email = serializer.validated_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            return Response(
                {
                    "email": [
                        "An account with this email already exists. Please log in."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        logger.warning(
            "Registration failed on a database uniqueness constraint "
            "(exception_type=%s)",
            type(error).__name__,
        )
        return Response(
            {"detail": "Unable to create the account. Please try again."},
            status=status.HTTP_409_CONFLICT,
        )
    record_account_activity(user, "account_created", "WebWatch account created")
    return token_response(user, status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    email = str(request.data.get("email", "")).strip().lower()
    password = request.data.get("password", "")
    user_record = User.objects.filter(email__iexact=email).first()
    user = None
    if user_record is not None and isinstance(password, str):
        user = authenticate(request, username=user_record.username, password=password)
    if user is None or not user.is_active:
        return Response(
            {"detail": "Invalid email or password."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    record_account_activity(user, "login", "Signed in to WebWatch")
    return token_response(user)


@api_view(["POST"])
@permission_classes([AllowAny])
def google_login(request):
    claims, error_response = verified_google_claims(request)
    if error_response is not None:
        return error_response

    email = claims["email"]
    subject = claims.get("sub")
    identity = None
    created_account = False

    try:
        with transaction.atomic():
            if isinstance(subject, str) and subject:
                identity = GoogleIdentity.objects.select_related("user").filter(
                    subject=subject
                ).first()
            user = identity.user if identity else None
            if user is None:
                user = User.objects.select_for_update().filter(email__iexact=email).first()
            if user is None:
                user = create_google_user(email, claims.get("name", ""))
                created_account = True
    except IntegrityError:
        user = User.objects.filter(email__iexact=email).first()

    if user is None or not user.is_active:
        return Response(
            {"detail": "This account cannot sign in."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if user.has_usable_password() and identity is None:
        return Response(
            {
                "detail": (
                    "An account with this email already exists. "
                    "Sign in using its existing method."
                )
            },
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(subject, str) and subject:
        existing_identity = GoogleIdentity.objects.filter(user=user).first()
        if existing_identity and existing_identity.subject != subject:
            return Response(
                {"detail": "This account is linked to a different Google account."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            GoogleIdentity.objects.get_or_create(user=user, defaults={"subject": subject})
        except IntegrityError:
            identity = GoogleIdentity.objects.filter(subject=subject).first()
            if identity is None or identity.user_id != user.pk:
                return Response(
                    {"detail": "This Google account is already linked to another account."},
                    status=status.HTTP_409_CONFLICT,
                )
    if created_account:
        record_account_activity(user, "account_created", "WebWatch account created with Google")
    record_account_activity(user, "login", "Signed in to WebWatch with Google")
    return token_response(user)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def link_google(request):
    claims, error_response = verified_google_claims(request)
    if error_response is not None:
        return error_response

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return Response(
            {"detail": "Google did not provide a valid account identifier."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account_email = request.user.email.strip()
    if not account_email or claims["email"].casefold() != account_email.casefold():
        return Response(
            {
                "code": "google_email_mismatch",
                "detail": "Sign in to Google with the same email address as your WebWatch account.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    existing_identity = GoogleIdentity.objects.filter(subject=subject).first()
    if existing_identity and existing_identity.user_id != request.user.pk:
        return Response(
            {"detail": "This Google account is already linked to another WebWatch account."},
            status=status.HTTP_409_CONFLICT,
        )
    linked_identity = GoogleIdentity.objects.filter(user=request.user).first()
    if linked_identity and linked_identity.subject != subject:
        return Response(
            {"detail": "A different Google account is already linked to this WebWatch account."},
            status=status.HTTP_409_CONFLICT,
        )

    try:
        GoogleIdentity.objects.get_or_create(
            user=request.user,
            defaults={"subject": subject},
        )
    except IntegrityError:
        existing_identity = GoogleIdentity.objects.filter(subject=subject).first()
        if existing_identity is None or existing_identity.user_id != request.user.pk:
            return Response(
                {"detail": "This Google account is already linked to another WebWatch account."},
                status=status.HTTP_409_CONFLICT,
            )

    return Response({"google_linked": True}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request):
    record_account_activity(request.user, "logout", "Signed out of WebWatch")
    Token.objects.filter(user=request.user).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    user_data = public_user(request.user)
    user_data["google_linked"] = GoogleIdentity.objects.filter(user=request.user).exists()
    return Response(user_data)


def _configured_reset_url(uid, token):
    configured_url = str(
        settings.WEBWATCH_PASSWORD_RESET_URL
        or settings.WEBWATCH_DASHBOARD_URL
        or ("http://localhost:5173" if settings.DEBUG else "")
    ).strip()
    try:
        parsed = urlsplit(configured_url)
        hostname = parsed.hostname
    except ValueError:
        return None
    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme != "https" and hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, f"/reset-password/{uid}/", "", f"token={token}"))


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetRequestThrottle])
def password_reset_request(request):
    if not isinstance(request.data, dict):
        return Response({
            "detail": "If an account matches that email, password reset instructions will be sent.",
        }, status=status.HTTP_200_OK)
    email_value = request.data.get("email", "")
    email = email_value.strip().casefold() if isinstance(email_value, str) else ""
    user = User.objects.filter(email__iexact=email).first() if email else None
    if user is not None and user.has_usable_password():
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_url = _configured_reset_url(uid, token)
        if reset_url:
            try:
                sent_count = send_mail(
                    "Reset your WebWatch password",
                    "We received a request to reset your WebWatch password.\n\n"
                    f"Use this secure link to choose a new password:\n{reset_url}\n\n"
                    "If you did not request a reset, you can ignore this email.",
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
                if sent_count == 1:
                    logger.info("Password reset email accepted by the configured email backend")
                else:
                    logger.error("Password reset email backend reported no message sent")
            except Exception as error:
                logger.error(
                    "Password reset email submission failed (exception_type=%s)",
                    type(error).__name__,
                )
        else:
            logger.error("Password reset email skipped because the configured frontend URL is invalid")
    return Response({
        "detail": "If an account matches that email, password reset instructions will be sent.",
    }, status=status.HTTP_200_OK)


def _reset_user_from_uid(uid_value):
    try:
        decoded_uid = force_str(urlsafe_base64_decode(uid_value))
        user_id = User._meta.pk.to_python(decoded_uid)
    except (ValueError, TypeError, OverflowError, UnicodeDecodeError):
        return None
    return User.objects.filter(pk=user_id).first()


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetConfirmThrottle])
def password_reset_validate(request):
    if not isinstance(request.data, dict):
        return Response({"detail": "This reset link is invalid or has expired. Request a new one."}, status=status.HTTP_400_BAD_REQUEST)
    uid = request.data.get("uid", "")
    token = request.data.get("token", "")
    if not isinstance(uid, str) or not isinstance(token, str):
        return Response({"detail": "This reset link is invalid or has expired. Request a new one."}, status=status.HTTP_400_BAD_REQUEST)
    user = _reset_user_from_uid(uid)
    if user is None or not default_token_generator.check_token(user, token):
        return Response({"detail": "This reset link is invalid or has expired. Request a new one."}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"valid": True})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetConfirmThrottle])
def password_reset_confirm(request):
    if not isinstance(request.data, dict):
        return Response({"detail": "Enter a valid reset link and password."}, status=status.HTTP_400_BAD_REQUEST)
    uid_value = request.data.get("uid", "")
    token = request.data.get("token", "")
    new_password = request.data.get("new_password", "")
    confirm_password = request.data.get("confirm_password", "")
    if not all(isinstance(value, str) for value in (uid_value, token, new_password, confirm_password)):
        return Response({"detail": "Enter a valid reset link and password."}, status=status.HTTP_400_BAD_REQUEST)
    if new_password != confirm_password:
        return Response({"confirm_password": ["Passwords do not match."]}, status=status.HTTP_400_BAD_REQUEST)
    user = _reset_user_from_uid(uid_value)
    if user is None:
        return Response({"detail": "This reset link is invalid or has expired. Request a new one."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=user.pk).first()
        if user is None or not default_token_generator.check_token(user, token):
            return Response({"detail": "This reset link is invalid or has expired. Request a new one."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            password_validation.validate_password(new_password, user)
        except ValidationError as error:
            return Response({"new_password": error.messages}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        Token.objects.filter(user=user).delete()
        record_account_activity(user, "password_changed", "WebWatch password reset")
    return Response({"detail": "Password updated. Please sign in with your new password."})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    if not isinstance(request.data, dict):
        return Response({"detail": "Enter valid password values."}, status=status.HTTP_400_BAD_REQUEST)
    current_password = request.data.get("current_password", "")
    new_password = request.data.get("new_password", "")
    confirm_password = request.data.get("confirm_password", "")
    if not all(isinstance(value, str) for value in (current_password, new_password, confirm_password)):
        return Response({"detail": "Enter valid password values."}, status=status.HTTP_400_BAD_REQUEST)
    if not request.user.has_usable_password():
        return Response({"detail": "This account uses Google Sign-In and has no WebWatch password."}, status=status.HTTP_400_BAD_REQUEST)
    if not request.user.check_password(current_password):
        return Response({"current_password": ["Current password is incorrect."]}, status=status.HTTP_400_BAD_REQUEST)
    if new_password != confirm_password:
        return Response({"confirm_password": ["Passwords do not match."]}, status=status.HTTP_400_BAD_REQUEST)
    try:
        password_validation.validate_password(new_password, request.user)
    except ValidationError as error:
        return Response({"new_password": error.messages}, status=status.HTTP_400_BAD_REQUEST)
    request.user.set_password(new_password)
    request.user.save(update_fields=["password"])
    Token.objects.filter(user=request.user).delete()
    record_account_activity(request.user, "password_changed", "WebWatch password changed")
    return Response({"detail": "Password changed. Sign in again with your new password."})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@throttle_classes([AccountDeletionThrottle])
def delete_account(request):
    """Delete only the authenticated account after explicit typed confirmation."""
    if not isinstance(request.data, dict) or set(request.data) != {"confirmation"}:
        return Response(
            {"detail": 'Type DELETE in the confirmation field to delete your account.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if request.data.get("confirmation") != "DELETE":
        return Response(
            {"detail": 'Type DELETE exactly to confirm account deletion.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user_id = request.user.pk
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user_id)
            # This marker is intentionally not linked to PII; Celery workers use it
            # to stop stale checks after the user row and owned websites cascade away.
            DeletedAccountMarker.objects.get_or_create(user_id=user.pk)
            user.delete()
    except User.DoesNotExist:
        return Response(status=status.HTTP_204_NO_CONTENT)
    except ProtectedError:
        logger.warning("Account deletion blocked by protected relation (user_id=%s)", user_id)
        return Response(
            {"detail": "The account could not be deleted because related records are protected."},
            status=status.HTTP_409_CONFLICT,
        )
    except DatabaseError as error:
        logger.error(
            "Account deletion failed (user_id=%s, exception_type=%s)",
            user_id,
            type(error).__name__,
        )
        return Response(
            {"detail": "The account could not be deleted. Please try again later."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(status=status.HTTP_204_NO_CONTENT)
