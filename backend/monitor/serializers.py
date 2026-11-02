from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from .models import Website, WebsiteChange, WebsiteCredential
from .auth_utils import unique_username_for_email
from .url_safety import validate_public_web_url
from .snapshots import validate_ignore_selectors

User = get_user_model()


class RegistrationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "An account with this email already exists. Please log in."
            )
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        name = attrs["name"].strip()
        if not name:
            raise serializers.ValidationError({"name": "This field may not be blank."})
        attrs["name"] = name
        first_name, _, last_name = name.partition(" ")
        candidate = User(username=attrs["email"], email=attrs["email"], first_name=first_name, last_name=last_name)
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": list(error.messages)}) from error
        return attrs

    def create(self, validated_data):
        email = validated_data["email"]
        first_name, _, last_name = validated_data["name"].partition(" ")
        return User.objects.create_user(
            username=unique_username_for_email(email),
            email=email,
            password=validated_data["password"],
            first_name=first_name,
            last_name=last_name,
        )


class WebsiteSerializer(serializers.ModelSerializer):
    credentials_configured = serializers.SerializerMethodField()
    login_status = serializers.SerializerMethodField()
    next_due_at = serializers.SerializerMethodField()
    snapshot_diagnostics = serializers.JSONField(read_only=True)

    def validate_url(self, value):
        try:
            return validate_public_web_url(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error

    def validate_ignore_selectors(self, value):
        try:
            return validate_ignore_selectors(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error

    def validate(self, attrs):
        selector = attrs.get("element_selector", getattr(self.instance, "element_selector", "body"))
        ignores = attrs.get("ignore_selectors", getattr(self.instance, "ignore_selectors", []))
        if selector.strip() in ignores:
            raise serializers.ValidationError({
                "ignore_selectors": "An ignore selector cannot match the monitored root selector."
            })
        return attrs

    class Meta:
        model = Website
        fields = [
            "id",
            "name",
            "url",
            "element_selector",
            "ignore_selectors",
            "status",
            "requires_login",
            "credentials_configured",
            "login_status",
            "check_interval",
            "last_checked",
            "next_due_at",
            "last_changed",
            "last_error",
            "content_hash",
            "snapshot_version",
            "snapshot_diagnostics",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "credentials_configured",
            "last_checked",
            "next_due_at",
            "last_changed",
            "last_error",
            "content_hash",
            "created_at",
            "updated_at",
        ]

    def update(self, instance, validated_data):
        new_selector = validated_data.get("element_selector", instance.element_selector)
        new_ignores = validated_data.get("ignore_selectors", instance.ignore_selectors)
        if (
            new_selector.strip() != instance.element_selector.strip()
            or new_ignores != instance.ignore_selectors
        ):
            instance.content_hash = None
            instance.last_value = ""
            instance.last_checked = None
            instance.last_error = None
            instance.check_claimed_at = None
        return super().update(instance, validated_data)

    def get_credentials_configured(self, website):
        return WebsiteCredential.objects.filter(website_id=website.pk).exists()

    def get_login_status(self, website):
        credential = WebsiteCredential.objects.filter(website_id=website.pk).only("login_status").first()
        if credential:
            return credential.login_status
        return "not_configured" if website.requires_login else None

    def get_next_due_at(self, website):
        if website.status not in {"monitoring", "error"}:
            return None
        if website.last_checked is None:
            return timezone.now()
        return website.last_checked + timedelta(minutes=website.check_interval)


class WebsiteChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebsiteChange
        fields = [
            "id",
            "website",
            "element_selector",
            "detected_at",
            "previous_hash",
            "new_hash",
            "previous_value",
            "new_value",
        ]
        read_only_fields = fields


class WebsiteCredentialInputSerializer(serializers.Serializer):
    login_url = serializers.URLField(max_length=1000)
    username = serializers.CharField(max_length=1000, write_only=True)
    password = serializers.CharField(max_length=4000, write_only=True, trim_whitespace=False)
    username_selector = serializers.CharField(max_length=500, required=False, allow_blank=True)
    password_selector = serializers.CharField(max_length=500, required=False, allow_blank=True)
    submit_selector = serializers.CharField(max_length=500, required=False, allow_blank=True)
    success_selector = serializers.CharField(max_length=500, required=False, allow_blank=True)
    success_url = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate_login_url(self, value):
        try:
            return validate_public_web_url(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error


class WebsiteCredentialOutputSerializer(serializers.ModelSerializer):
    configured = serializers.SerializerMethodField()
    username = serializers.SerializerMethodField()

    class Meta:
        model = WebsiteCredential
        fields = [
            "configured",
            "login_url",
            "username",
            "username_selector",
            "password_selector",
            "submit_selector",
            "success_selector",
            "success_url",
            "login_status",
            "session_updated_at",
        ]

    def get_configured(self, credential):
        return True

    def get_username(self, credential):
        return credential.get_username()
