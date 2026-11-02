from django.conf import settings
from django.db import models
from django.core.exceptions import ImproperlyConfigured
import json


class Website(models.Model):
    STATUS_CHOICES = [
        ("stopped", "Stopped"),
        ("monitoring", "Monitoring"),
        ("login_required", "Login Required"),
        ("error", "Error"),
    ]

    name = models.CharField(max_length=200)
    url = models.URLField(max_length=1000)
    element_selector = models.CharField(max_length=500, default="body")
    ignore_selectors = models.JSONField(default=list, blank=True)
    snapshot_version = models.PositiveSmallIntegerField(default=2)
    snapshot_diagnostics = models.JSONField(default=dict, blank=True)
    last_value = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="websites",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="stopped"
    )

    requires_login = models.BooleanField(default=False)

    check_interval = models.PositiveIntegerField(
        default=15,
        help_text="Check interval in minutes"
    )

    last_checked = models.DateTimeField(null=True, blank=True)
    check_claimed_at = models.DateTimeField(null=True, blank=True)
    last_changed = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)

    content_hash = models.CharField(
    max_length=64,
    blank=True,
    null=True
)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class WebsiteChange(models.Model):
    website = models.ForeignKey(
        Website,
        on_delete=models.CASCADE,
        related_name="changes"
    )
    element_selector = models.CharField(max_length=500, default="body")

    detected_at = models.DateTimeField(auto_now_add=True)

    previous_hash = models.CharField(
        max_length=64,
        blank=True,
        null=True
    )

    new_hash = models.CharField(
        max_length=64
    )
    previous_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    notification_attempts = models.PositiveIntegerField(default=0)
    notification_next_attempt_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.website.name} - {self.detected_at}"


class WebsiteAlert(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="website_alerts",
    )
    change = models.ForeignKey(
        WebsiteChange,
        on_delete=models.CASCADE,
        related_name="alerts",
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "change"), name="uniq_alert_user_change"),
        ]
        indexes = [
            models.Index(fields=("user", "is_read", "created_at"), name="alert_user_read_created_idx"),
        ]

    def __str__(self):
        return f"Alert for user {self.user_id}, change {self.change_id}"


class UserPreference(models.Model):
    THEME_CHOICES = [("light", "Light"), ("dark", "Dark"), ("system", "System")]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preferences",
    )
    email_alerts_enabled = models.BooleanField(default=True)
    whatsapp_notifications_enabled = models.BooleanField(default=False)
    whatsapp_phone_number = models.CharField(max_length=16, blank=True, default="")
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="system")
    updated_at = models.DateTimeField(auto_now=True)


class WhatsAppDelivery(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sending", "Sending"),
        ("accepted", "Accepted by Meta; delivery unconfirmed"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="whatsapp_deliveries",
    )
    website = models.ForeignKey(
        Website,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="whatsapp_deliveries",
    )
    website_change = models.ForeignKey(
        WebsiteChange,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="whatsapp_deliveries",
    )
    channel = models.CharField(max_length=20, default="whatsapp")
    idempotency_key = models.CharField(max_length=120, unique=True)
    recipient_masked = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="queued")
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    error_category = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=255, blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=("user", "channel", "created_at"), name="wa_delivery_user_chan_idx"),
            models.Index(fields=("status", "created_at"), name="wa_delivery_status_created_idx"),
        ]

    def __str__(self):
        return f"WhatsApp delivery {self.pk} ({self.status})"


class AccountActivity(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_activity",
    )
    event_type = models.CharField(max_length=40)
    description = models.CharField(max_length=200)
    website_name = models.CharField(max_length=200, blank=True, default="")
    website_url = models.URLField(max_length=1000, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=("user", "-created_at"), name="activity_user_created_idx"),
        ]
        ordering = ("-created_at", "-pk")


def record_account_activity(user, event_type, description, website=None):
    return AccountActivity.objects.create(
        user=user,
        event_type=event_type,
        description=description,
        website_name=website.name if website else "",
        website_url=website.url if website else "",
    )


class WebsiteCredential(models.Model):
    LOGIN_STATUS_CHOICES = [
        ("connected", "Connected"),
        ("expired", "Login expired"),
        ("failed", "Login failed"),
        ("needs_attention", "Configuration needs attention"),
    ]

    website = models.OneToOneField(
        Website,
        on_delete=models.CASCADE,
        related_name="credentials",
    )
    username_encrypted = models.TextField()
    password_encrypted = models.TextField()
    login_url = models.URLField(max_length=1000)
    username_selector = models.CharField(
        max_length=500,
        default='input[type="email"]',
    )
    password_selector = models.CharField(
        max_length=500,
        default='input[type="password"]',
    )
    submit_selector = models.CharField(
        max_length=500,
        default='button[type="submit"]',
    )
    success_selector = models.CharField(max_length=500, blank=True, default="")
    success_url = models.CharField(max_length=1000, blank=True, default="")
    session_state_encrypted = models.TextField(blank=True, default="")
    session_updated_at = models.DateTimeField(null=True, blank=True)
    login_status = models.CharField(
        max_length=20,
        choices=LOGIN_STATUS_CHOICES,
        default="needs_attention",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_username(self, value):
        from .crypto import encrypt_website_secret

        self.username_encrypted = encrypt_website_secret(value)

    def get_username(self):
        from .crypto import decrypt_website_secret

        return decrypt_website_secret(self.username_encrypted)

    def set_password(self, value):
        from .crypto import encrypt_website_secret

        self.password_encrypted = encrypt_website_secret(value)

    def get_password(self):
        from .crypto import decrypt_website_secret

        return decrypt_website_secret(self.password_encrypted)

    def set_session_state(self, state):
        from .crypto import encrypt_website_secret

        if not isinstance(state, dict):
            raise TypeError("Playwright session state must be an object.")
        self.session_state_encrypted = encrypt_website_secret(
            json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )

    def get_session_state(self):
        from .crypto import decrypt_website_secret

        if not self.session_state_encrypted:
            return None
        try:
            state = json.loads(decrypt_website_secret(self.session_state_encrypted))
        except (json.JSONDecodeError, TypeError) as error:
            raise ImproperlyConfigured("Stored website login session is invalid.") from error
        if not isinstance(state, dict):
            raise ImproperlyConfigured("Stored website login session is invalid.")
        return state

    def __str__(self):
        return f"Website credentials for website {self.website_id}"


class GoogleIdentity(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_identity",
    )
    subject = models.CharField(max_length=255, unique=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Google identity for user {self.user_id}"


class DeletedAccountMarker(models.Model):
    """Minimal tombstone used to invalidate work queued for a deleted user."""

    user_id = models.PositiveBigIntegerField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DeveloperAuditLog(models.Model):
    ACTION_CHOICES = [
        ("update", "Update"),
        ("delete", "Delete"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="developer_audit_logs",
    )
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    model_key = models.CharField(max_length=40)
    record_id = models.CharField(max_length=100)
    changed_fields = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("-created_at",), name="dev_audit_created_idx"),
            models.Index(fields=("model_key", "record_id"), name="dev_audit_record_idx"),
        ]

    def __str__(self):
        return f"{self.action} {self.model_key} {self.record_id} by {self.actor_id}"
