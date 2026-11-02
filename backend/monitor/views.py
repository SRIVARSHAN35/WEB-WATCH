from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from .models import AccountActivity, UserPreference, Website, WebsiteAlert, record_account_activity
from .models import WebsiteCredential
from .serializers import (
    WebsiteChangeSerializer,
    WebsiteCredentialInputSerializer,
    WebsiteCredentialOutputSerializer,
    WebsiteSerializer,
)
from .tasks import enqueue_website_check
from .website_login import test_website_login
from django.utils import timezone
from rest_framework.throttling import UserRateThrottle
from rest_framework.decorators import throttle_classes
from .models import WhatsAppDelivery
from .services.whatsapp import configuration_status, mask_phone_number, send_test_message, validate_phone_number
import uuid


def _profile_data(user):
    from .models import GoogleIdentity
    return {
        "id": user.pk,
        "name": user.get_full_name().strip() or user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "account_type": "Google" if GoogleIdentity.objects.filter(user=user).exists() else "Email and password",
        "has_usable_password": user.has_usable_password(),
        "created_at": user.date_joined,
    }


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def account_profile(request):
    user = request.user
    if request.method == "PATCH":
        if set(request.data.keys()) - {"first_name", "last_name"}:
            return Response({"detail": "Only first and last name can be changed."}, status=400)
        values = {}
        for field in ("first_name", "last_name"):
            if field in request.data:
                value = request.data[field]
                if not isinstance(value, str) or len(value.strip()) > 150:
                    return Response({field: "Enter a valid name up to 150 characters."}, status=400)
                if getattr(user, field) != value.strip():
                    values[field] = value.strip()
        for field, value in values.items():
            setattr(user, field, value)
        if values:
            user.save(update_fields=[*values, "updated_at"] if hasattr(user, "updated_at") else list(values))
            record_account_activity(user, "profile_updated", "Profile information updated")
        return Response(_profile_data(user))
    return Response(_profile_data(user))


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def account_settings(request):
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    if request.method == "PATCH":
        unknown = set(request.data.keys()) - {"email_alerts_enabled", "theme"}
        if unknown:
            return Response({"detail": "Unsupported settings field."}, status=400)
        if "email_alerts_enabled" in request.data:
            value = request.data["email_alerts_enabled"]
            if not isinstance(value, bool):
                return Response({"email_alerts_enabled": "Expected true or false."}, status=400)
            preference.email_alerts_enabled = value
        if "theme" in request.data:
            value = request.data["theme"]
            if not isinstance(value, str) or value not in {"light", "dark", "system"}:
                return Response({"theme": "Choose light, dark, or system."}, status=400)
            preference.theme = value
        preference.save()
    return Response({
        "email_alerts_enabled": preference.email_alerts_enabled,
        "theme": preference.theme,
        "updated_at": preference.updated_at,
    })


class WhatsAppTestThrottle(UserRateThrottle):
    scope = "whatsapp_test"


def _whatsapp_settings_data(preference):
    config = configuration_status()
    return {
        "enabled": preference.whatsapp_notifications_enabled,
        "phone_number": preference.whatsapp_phone_number,
        "integration_enabled": config["enabled"],
        "configured": config["configured"],
        "missing_configuration": config["missing"],
        "template_configured": config["template_configured"],
    }


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def whatsapp_notification_settings(request):
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    if request.method == "PATCH":
        unknown = set(request.data.keys()) - {"enabled", "phone_number"}
        if unknown:
            return Response({"detail": "Unsupported WhatsApp setting."}, status=400)
        enabled = request.data.get("enabled", preference.whatsapp_notifications_enabled)
        number = request.data.get("phone_number", preference.whatsapp_phone_number)
        if not isinstance(enabled, bool):
            return Response({"enabled": "Expected true or false."}, status=400)
        if not isinstance(number, str):
            return Response({"phone_number": "Enter a phone number in international format."}, status=400)
        number = number.strip()
        if number:
            try:
                number = validate_phone_number(number)
            except ValueError as error:
                return Response({"phone_number": str(error)}, status=400)
        if enabled and not number:
            return Response({"phone_number": "Add a WhatsApp recipient number before enabling notifications."}, status=400)
        preference.whatsapp_notifications_enabled = enabled
        preference.whatsapp_phone_number = number
        preference.save(update_fields=["whatsapp_notifications_enabled", "whatsapp_phone_number", "updated_at"])
    return Response(_whatsapp_settings_data(preference))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([WhatsAppTestThrottle])
def whatsapp_test_message(request):
    if request.data:
        return Response({"detail": "The test endpoint does not accept a recipient number."}, status=400)
    preference = UserPreference.objects.filter(user=request.user).first()
    if preference is None or not preference.whatsapp_phone_number:
        return Response({"detail": "Save your WhatsApp recipient number in Settings first."}, status=400)
    result = send_test_message(preference.whatsapp_phone_number)
    delivery = WhatsAppDelivery.objects.create(
        user=request.user,
        channel="whatsapp",
        idempotency_key=f"test:{request.user.pk}:{uuid.uuid4().hex}",
        recipient_masked=mask_phone_number(preference.whatsapp_phone_number),
        status="accepted" if result["accepted"] else "failed",
        provider_message_id=result.get("message_id") or "",
        error_category="" if result["accepted"] else result["category"],
        error_detail="" if result["accepted"] else result["detail"][:255],
        attempts=1,
        accepted_at=timezone.now() if result["accepted"] else None,
    )
    if not result["accepted"]:
        return Response({
            "accepted": False,
            "message_id": None,
            "delivery_status": "unconfirmed",
            "category": result["category"],
            "detail": result["detail"],
            "delivery_id": delivery.pk,
        }, status=400 if not result.get("retryable") else 503)
    return Response({
        "accepted": True,
        "message_id": result["message_id"],
        "delivery_status": "unconfirmed",
        "detail": result["detail"],
        "delivery_id": delivery.pk,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def whatsapp_notification_history(request):
    rows = WhatsAppDelivery.objects.filter(user=request.user, channel="whatsapp").select_related(
        "website", "website_change__website"
    ).order_by("-created_at", "-pk")[:30]
    return Response([{
        "id": row.pk,
        "website_name": row.website.name if row.website_id else (
            row.website_change.website.name if row.website_change_id else "Test message"
        ),
        "recipient": row.recipient_masked,
        "status": row.status,
        "provider_message_id": row.provider_message_id,
        "error_category": row.error_category,
        "error_detail": row.error_detail,
        "attempts": row.attempts,
        "created_at": row.created_at,
        "accepted_at": row.accepted_at,
    } for row in rows])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def account_history(request):
    try:
        page = max(1, int(request.query_params.get("page", "1")))
        page_size = min(100, max(1, int(request.query_params.get("page_size", "25"))))
    except ValueError:
        return Response({"detail": "Page values must be integers."}, status=400)
    events = AccountActivity.objects.filter(user=request.user)
    count = events.count()
    start = (page - 1) * page_size
    rows = events[start:start + page_size]
    return Response({
        "count": count,
        "results": [{
            "id": event.pk,
            "event_type": event.event_type,
            "description": event.description,
            "website_name": event.website_name,
            "website_url": event.website_url,
            "created_at": event.created_at,
        } for event in rows],
        "page": page,
        "has_more": start + len(rows) < count,
    })


def _alert_queryset(user):
    return WebsiteAlert.objects.filter(
        user=user, change__website__owner=user
    ).select_related("change__website")


def _alert_data(alert):
    change = alert.change
    website = change.website
    return {
        "id": alert.pk,
        "is_read": alert.is_read,
        "created_at": alert.created_at,
        "read_at": alert.read_at,
        "change_id": change.pk,
        "website": {
            "id": website.pk,
            "name": website.name,
            "url": website.url,
        },
        "detected_at": change.detected_at,
        "summary": "Changes detected. Please view in the dashboard.",
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def alerts_list(request):
    alerts = _alert_queryset(request.user).order_by("-created_at", "-pk")
    unread_count = alerts.filter(is_read=False).count()
    all_count = alerts.count()
    filter_value = request.query_params.get("filter", "all")
    if filter_value not in {"all", "unread"}:
        return Response({"detail": "Filter must be 'all' or 'unread'."}, status=400)
    if filter_value == "unread":
        alerts = alerts.filter(is_read=False)
    try:
        page = max(1, int(request.query_params.get("page", "1")))
        page_size = min(100, max(1, int(request.query_params.get("page_size", "20"))))
    except ValueError:
        return Response({"detail": "Page values must be integers."}, status=400)
    filtered_count = alerts.count()
    start = (page - 1) * page_size
    rows = list(alerts[start:start + page_size])
    return Response({
        "count": all_count,
        "filtered_count": filtered_count,
        "unread_count": unread_count,
        "results": [_alert_data(alert) for alert in rows],
        "page": page,
        "page_size": page_size,
        "has_more": start + len(rows) < filtered_count,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def alerts_unread_count(request):
    count = _alert_queryset(request.user).filter(is_read=False).count()
    return Response({"unread_count": count})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def alert_detail(request, alert_id):
    from django.shortcuts import get_object_or_404
    alert = get_object_or_404(_alert_queryset(request.user), pk=alert_id)
    return Response(_alert_data(alert))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def alert_mark_read(request, alert_id):
    from django.shortcuts import get_object_or_404
    alert = get_object_or_404(_alert_queryset(request.user), pk=alert_id)
    if not alert.is_read:
        alert.is_read = True
        alert.read_at = timezone.now()
        alert.save(update_fields=["is_read", "read_at"])
    return Response(_alert_data(alert))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def alerts_mark_all_read(request):
    _alert_queryset(request.user).filter(is_read=False).update(
        is_read=True, read_at=timezone.now()
    )
    return Response({"updated": "all", "unread_count": 0})


class WebsiteViewSet(viewsets.ModelViewSet):
    serializer_class = WebsiteSerializer

    def get_queryset(self):
        return Website.objects.filter(owner=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        website = serializer.save(owner=self.request.user)
        record_account_activity(
            self.request.user,
            "website_added",
            f"Added website: {website.name[:160]}",
            website=website,
        )

    def perform_update(self, serializer):
        website = serializer.instance
        changed = any(
            getattr(website, field) != value
            for field, value in serializer.validated_data.items()
        )
        website = serializer.save()
        if changed:
            record_account_activity(
                self.request.user,
                "website_updated",
                f"Updated website settings: {website.name[:160]}",
                website=website,
            )

    def destroy(self, request, *args, **kwargs):
        website = self.get_object()
        record_account_activity(
            request.user,
            "website_removed",
            f"Removed website: {website.name[:160]}",
            website=website,
        )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        website = self.get_object()
        was_monitoring = website.status == "monitoring"
        website.status = "monitoring"
        website.save(update_fields=["status", "updated_at"])
        if not was_monitoring:
            record_account_activity(request.user, "monitoring_started", f"Started monitoring: {website.name[:160]}", website=website)

        return Response(
            WebsiteSerializer(website).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        website = self.get_object()
        was_stopped = website.status == "stopped"
        website.status = "stopped"
        website.save(update_fields=["status", "updated_at"])
        if not was_stopped:
            record_account_activity(request.user, "monitoring_stopped", f"Stopped monitoring: {website.name[:160]}", website=website)

        return Response(
            WebsiteSerializer(website).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=["post"], url_path="check-now")
    def check_now(self, request, pk=None):
        website = self.get_object()
        if website.status not in {"monitoring", "error"}:
            return Response(
                {"detail": "Start monitoring before requesting a check."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            queued = enqueue_website_check(website.pk)
        except Exception:
            return Response(
                {"detail": "Website check could not be queued."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "message": (
                    "Website check started."
                    if queued
                    else "Website check is already queued or running."
                ),
                "website_id": website.pk,
                "queued": queued,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=["get", "post", "put", "patch", "delete"],
        url_path="credentials",
    )
    def credentials(self, request, pk=None):
        website = self.get_object()
        credential = WebsiteCredential.objects.filter(website=website).first()

        if request.method == "GET":
            if credential is None:
                return Response({"configured": False})
            try:
                return Response(WebsiteCredentialOutputSerializer(credential).data)
            except ImproperlyConfigured:
                return Response(
                    {"detail": "Website credential encryption is not configured."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        if request.method == "DELETE":
            if credential is not None:
                credential.delete()
            website.status = "login_required"
            website.save(update_fields=["status", "updated_at"])
            return Response(status=status.HTTP_204_NO_CONTENT)

        if request.method == "POST" and credential is not None:
            return Response(
                {"detail": "Website login is already configured."},
                status=status.HTTP_409_CONFLICT,
            )

        if request.method == "PATCH" and credential is not None:
            try:
                current_values = {
                    "login_url": credential.login_url,
                    "username": credential.get_username(),
                    "password": credential.get_password(),
                    "username_selector": credential.username_selector,
                    "password_selector": credential.password_selector,
                    "submit_selector": credential.submit_selector,
                    "success_selector": credential.success_selector,
                    "success_url": credential.success_url,
                }
            except ImproperlyConfigured:
                return Response(
                    {"detail": "Saved login credentials could not be read. Check the encryption-key configuration."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            current_values.update(request.data)
            serializer = WebsiteCredentialInputSerializer(data=current_values)
        else:
            serializer = WebsiteCredentialInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data

        credentials = {
            "login_url": values["login_url"],
            "username": values["username"],
            "password": values["password"],
            "username_selector": values.get("username_selector", ""),
            "password_selector": values.get("password_selector", ""),
            "submit_selector": values.get("submit_selector", ""),
            "success_selector": values.get("success_selector", ""),
            "success_url": values.get("success_url", ""),
        }
        # Re-test at save time so the API cannot persist credentials based only
        # on a stale or frontend-forged Test Login result.
        login_result = test_website_login(credentials)
        session_state = login_result.get("storage_state")
        if not login_result.get("success") or not isinstance(session_state, dict):
            return Response(
                {
                    "success": False,
                    "detail": login_result.get("message", "Login could not be verified. Check the credentials or advanced settings."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                if credential is None:
                    credential = WebsiteCredential(website=website)
                credential.login_url = credentials["login_url"]
                credential.username_selector = credentials["username_selector"]
                credential.password_selector = credentials["password_selector"]
                credential.submit_selector = credentials["submit_selector"]
                credential.success_selector = credentials["success_selector"]
                credential.success_url = credentials["success_url"]
                credential.set_username(credentials["username"])
                credential.set_password(credentials["password"])
                credential.set_session_state(session_state)
                credential.session_updated_at = timezone.now()
                credential.login_status = "connected"
                credential.save()
                website.requires_login = True
                if website.status == "login_required":
                    website.status = "monitoring"
                website.save(update_fields=["requires_login", "status", "updated_at"])
        except ImproperlyConfigured:
            return Response(
                {"detail": "Website credential encryption is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            WebsiteCredentialOutputSerializer(credential).data,
            status=status.HTTP_201_CREATED if request.method == "POST" else status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="credentials/test",
    )
    def test_credentials(self, request, pk=None):
        self.get_object()
        serializer = WebsiteCredentialInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        credentials = {
            "login_url": values["login_url"],
            "username": values["username"],
            "password": values["password"],
            "username_selector": values.get("username_selector", ""),
            "password_selector": values.get("password_selector", ""),
            "submit_selector": values.get("submit_selector", ""),
            "success_selector": values.get("success_selector", ""),
            "success_url": values.get("success_url", ""),
        }
        result = test_website_login(credentials)
        return Response(
            {"success": bool(result.get("success")), "message": result.get("message", "Unable to verify login.")},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="changes")
    def changes(self, request, pk=None):
        website = self.get_object()
        changes = website.changes.order_by("-detected_at")
        serializer = WebsiteChangeSerializer(changes, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="clear-change-history")
    def clear_change_history(self, request, pk=None):
        website = self.get_object()
        website.changes.all().delete()
        website.last_changed = None
        website.save(update_fields=["last_changed", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
