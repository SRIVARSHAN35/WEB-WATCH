import hashlib
import json
import logging
import socket
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread

from celery import shared_task
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .models import AccountActivity, DeletedAccountMarker, UserPreference, Website, WebsiteAlert, WebsiteChange, WebsiteCredential, WhatsAppDelivery
from .crypto import encrypt_website_secret
from .notifications import send_change_notification
from .website_login import is_login_page, login_page
from .url_safety import guard_public_request, validate_public_web_url
from .snapshots import SNAPSHOT_VERSION, extract_snapshot, stabilize_page
from .services.whatsapp import mask_phone_number, send_message


logger = logging.getLogger(__name__)
CHECK_CLAIM_STALE_AFTER = timedelta(minutes=15)
CHANGE_VALUE_MAX_LENGTH = 5000
NOTIFICATION_CLAIM_TIMEOUT = timedelta(minutes=10)
SENSITIVE_FIELD_MARKERS = ("password", "token", "cookie", "session", "secret", "authorization")


class MonitoringElementNotFound(Exception):
    pass


class AccountDeletedDuringMonitoring(Exception):
    pass


def _account_was_deleted(user_id):
    return DeletedAccountMarker.objects.filter(user_id=user_id).exists()


def _watch_for_deleted_account(owner_id, cancelled, stop_watching):
    while not stop_watching.wait(0.5):
        close_old_connections()
        try:
            if _account_was_deleted(owner_id):
                cancelled.set()
                return
        except Exception as error:
            logger.warning(
                "Could not verify account state during website monitoring (%s)",
                type(error).__name__,
            )
            # Fail closed: don't continue making monitored-site requests if the
            # worker cannot verify that the account is still present.
            cancelled.set()
            return
        finally:
            close_old_connections()


@contextmanager
def _account_deletion_watch(owner_id):
    cancelled = Event()
    stop_watching = Event()
    watcher = Thread(
        target=_watch_for_deleted_account,
        args=(owner_id, cancelled, stop_watching),
        name=f"webwatch-account-watch-{owner_id}",
        daemon=True,
    )
    watcher.start()
    try:
        yield cancelled
    finally:
        stop_watching.set()
        watcher.join(timeout=2)


def _guard_monitor_request(route, cancelled):
    if cancelled.is_set():
        route.abort("blockedbyclient")
        return
    guard_public_request(route)


def _is_sensitive_control(control):
    control_type = str(control.get("type", "")).strip().lower()
    descriptor = " ".join(
        str(control.get(key, ""))
        for key in ("name", "id", "type", "autocomplete")
    ).lower()
    return control_type in {"password", "hidden"} or any(
        marker in descriptor for marker in SENSITIVE_FIELD_MARKERS
    )


def _extract_monitored_content(page, selector, ignore_selectors=(), include_diagnostics=False):
    try:
        snapshot = extract_snapshot(page, selector, ignore_selectors)
    except ValueError as error:
        if "did not match any elements" in str(error):
            raise MonitoringElementNotFound from error
        raise
    result = (snapshot.content, snapshot.display_value, snapshot.legacy_hash)
    return (*result, snapshot.diagnostics) if include_diagnostics else result


def _safe_monitoring_error(error):
    if isinstance(error, MonitoringElementNotFound):
        return "The CSS selector did not match any elements on the page."
    if isinstance(error, (PlaywrightTimeoutError, TimeoutError)):
        return "Connection timed out while loading the website."

    error_text = str(error).lower()
    if "connection refused" in error_text or "err_connection_refused" in error_text:
        return "Connection refused by the website."
    if any(marker in error_text for marker in ("name_not_resolved", "getaddrinfo failed", "dns")):
        return "Unable to resolve the website address."
    if "timeout" in error_text or "timed out" in error_text:
        return "Connection timed out while loading the website."
    if isinstance(error, ImproperlyConfigured):
        if "must be configured" in error_text:
            return "Website credential encryption is not configured."
        return "Stored website login credentials could not be decrypted."
    if isinstance(error, (PlaywrightError, socket.gaierror, ConnectionError, OSError)):
        return "Unable to connect to the website."
    return "Unable to complete the website check."


def _record_monitoring_error(website, safe_message, error):
    website.refresh_from_db(fields=["status"])
    if website.status == "stopped":
        website.check_claimed_at = None
        website.save(update_fields=["check_claimed_at", "updated_at"])
        return {
            "website": website.name,
            "url": website.url,
            "changed": False,
            "skipped": "Monitoring was stopped before the check completed.",
        }
    checked_at = timezone.now()
    website.status = "error"
    website.last_checked = checked_at
    website.last_error = safe_message
    try:
        website.check_claimed_at = None
        website.save(
            update_fields=[
                "status",
                "last_checked",
                "last_error",
                "check_claimed_at",
                "updated_at",
            ]
        )
    except Exception as save_error:
        logger.error(
            "Could not persist monitoring error for website %s (%s)",
            website.id,
            type(save_error).__name__,
        )
    logger.error(
        "Website monitoring failed for website %s: %s (%s)",
        website.id,
        safe_message,
        error if isinstance(error, str) else type(error).__name__,
    )
    return {
        "website": website.name,
        "url": website.url,
        "changed": False,
        "error": safe_message,
    }


def _record_login_required(website, safe_message, login_status=None):
    website.refresh_from_db(fields=["status"])
    if website.status == "stopped":
        website.check_claimed_at = None
        website.save(update_fields=["check_claimed_at", "updated_at"])
        return {
            "website": website.name,
            "url": website.url,
            "changed": False,
            "skipped": "Monitoring was stopped before the check completed.",
        }
    website.status = "login_required"
    website.last_checked = timezone.now()
    website.last_error = safe_message
    website.check_claimed_at = None
    website.save(
        update_fields=[
            "status",
            "last_checked",
            "last_error",
            "check_claimed_at",
            "updated_at",
        ]
    )
    if login_status is None:
        lowered = safe_message.lower()
        needs_attention_markers = (
            "additional authentication", "captcha", "mfa", "manual", "selector",
            "multiple possible", "could not identify", "ambiguous",
        )
        login_status = "needs_attention" if any(marker in lowered for marker in needs_attention_markers) else "failed"
    WebsiteCredential.objects.filter(website_id=website.pk).update(login_status=login_status)
    return {
        "website": website.name,
        "url": website.url,
        "changed": False,
        "error": safe_message,
    }


@shared_task
def check_website(website_id):
    website = None
    authenticated_check = False
    owner_id = None
    try:
        website = Website.objects.get(id=website_id)
        owner_id = website.owner_id
        if _account_was_deleted(owner_id):
            return {"changed": False, "skipped": "Account was deleted."}
        if website.status not in {"monitoring", "error"}:
            Website.objects.filter(pk=website_id).update(check_claimed_at=None)
            return {
                "website": website.name,
                "url": website.url,
                "changed": False,
                "skipped": "Monitoring is stopped.",
            }
        validate_public_web_url(website.url)
        authenticated_check = website.requires_login
        credential = None

        if authenticated_check:
            try:
                credential = WebsiteCredential.objects.get(website=website)
            except WebsiteCredential.DoesNotExist:
                return _record_login_required(
                    website,
                    "Website login credentials are not configured.",
                )

            credentials = {
                "login_url": credential.login_url,
                "username": credential.get_username(),
                "password": credential.get_password(),
                "username_selector": credential.username_selector,
                "password_selector": credential.password_selector,
                "submit_selector": credential.submit_selector,
                "success_selector": credential.success_selector,
                "success_url": credential.success_url,
            }
            try:
                session_state = credential.get_session_state()
            except ImproperlyConfigured as error:
                WebsiteCredential.objects.filter(pk=credential.pk).update(login_status="needs_attention")
                return _record_monitoring_error(
                    website,
                    _safe_monitoring_error(error),
                    error,
                )
        else:
            session_state = None

        encrypted_session_state = None
        login_was_expired = False

        with _account_deletion_watch(owner_id) as account_cancelled, sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = None
            try:
                if authenticated_check:
                    if session_state:
                        context = browser.new_context(storage_state=session_state)
                    else:
                        context = browser.new_context()
                    page = context.new_page()
                    page.route("**/*", lambda route: _guard_monitor_request(route, account_cancelled))
                    if not session_state:
                        login_result = login_page(page, credentials)
                        if account_cancelled.is_set():
                            raise AccountDeletedDuringMonitoring
                        if not login_result["success"]:
                            if login_result.get("technical_error"):
                                return _record_monitoring_error(
                                    website,
                                    login_result["message"],
                                    login_result.get("error_type", "LoginError"),
                                )
                            return _record_login_required(website, login_result["message"])
                    else:
                        page.goto(website.url, wait_until="domcontentloaded", timeout=30000)
                        if account_cancelled.is_set():
                            raise AccountDeletedDuringMonitoring
                        if is_login_page(page, credentials):
                            login_was_expired = True
                            WebsiteCredential.objects.filter(pk=credential.pk).update(login_status="expired")
                            login_result = login_page(page, credentials)
                            if account_cancelled.is_set():
                                raise AccountDeletedDuringMonitoring
                            if not login_result["success"]:
                                if login_result.get("technical_error"):
                                    return _record_monitoring_error(
                                        website,
                                        login_result["message"],
                                        login_result.get("error_type", "LoginError"),
                                    )
                                return _record_login_required(
                                    website,
                                    login_result["message"],
                                    login_status="expired" if login_was_expired else None,
                                )
                    page.goto(
                        website.url,
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    if account_cancelled.is_set():
                        raise AccountDeletedDuringMonitoring
                    if is_login_page(page, credentials):
                        return _record_login_required(
                            website,
                            "The website redirected to its login page after authentication.",
                            login_status="expired",
                        )
                else:
                    page = browser.new_page()
                    page.route("**/*", lambda route: _guard_monitor_request(route, account_cancelled))
                    page.goto(
                        website.url,
                        wait_until="load",
                        timeout=30000
                    )
                    if account_cancelled.is_set():
                        raise AccountDeletedDuringMonitoring

                stabilize_page(page, website.ignore_selectors)
                if account_cancelled.is_set():
                    raise AccountDeletedDuringMonitoring
                content, current_value, legacy_hash, snapshot_diagnostics = _extract_monitored_content(
                    page,
                    website.element_selector,
                    website.ignore_selectors,
                    include_diagnostics=True,
                )
                if authenticated_check:
                    try:
                        state = context.storage_state()
                        if isinstance(state, dict):
                            encrypted_session_state = encrypt_website_secret(
                                json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                            )
                    except Exception as state_error:
                        logger.warning(
                            "Could not refresh login session for website %s (%s)",
                            website.pk,
                            type(state_error).__name__,
                        )
            finally:
                if context is not None:
                    context.close()
                browser.close()

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        snapshot_diagnostics.update({
            "snapshot_version": SNAPSHOT_VERSION,
            "content_hash_prefix": content_hash[:12],
        })

        website_change = None
        with transaction.atomic():
            if _account_was_deleted(owner_id):
                return {"changed": False, "skipped": "Account was deleted."}
            website = Website.objects.select_for_update().get(pk=website_id)
            if website.status not in {"monitoring", "error"}:
                website.check_claimed_at = None
                website.save(update_fields=["check_claimed_at", "updated_at"])
                return {
                    "website": website.name,
                    "url": website.url,
                    "changed": False,
                    "skipped": "Monitoring was stopped before the check completed.",
                }
            if authenticated_check:
                saved_credential = WebsiteCredential.objects.select_for_update().filter(
                    website_id=website.pk
                ).first()
                if saved_credential is not None:
                    saved_credential.login_status = "connected"
                    if encrypted_session_state:
                        saved_credential.session_state_encrypted = encrypted_session_state
                        saved_credential.session_updated_at = timezone.now()
                    saved_credential.save(
                        update_fields=[
                            "login_status",
                            "session_state_encrypted",
                            "session_updated_at",
                            "updated_at",
                        ]
                    )
            previous_hash = website.content_hash
            checked_at = timezone.now()
            if website.snapshot_version != SNAPSHOT_VERSION:
                website.content_hash = content_hash
                website.last_value = current_value[:CHANGE_VALUE_MAX_LENGTH]
                website.snapshot_version = SNAPSHOT_VERSION
                website.snapshot_diagnostics = {
                    **snapshot_diagnostics,
                    "decision": "rebaselined",
                    "reason": "snapshot_version_changed",
                }
                website.last_checked = checked_at
                website.last_error = None
                website.check_claimed_at = None
                website.save(update_fields=[
                    "content_hash", "last_value", "snapshot_version",
                    "snapshot_diagnostics", "last_checked", "last_error",
                    "check_claimed_at", "updated_at",
                ])
                return {
                    "website": website.name,
                    "url": website.url,
                    "changed": False,
                    "rebaselined": True,
                    "reason": "snapshot_version_changed",
                }
            legacy_baseline_match = bool(
                legacy_hash and previous_hash == legacy_hash
            )
            changed = bool(
                previous_hash
                and previous_hash != content_hash
                and not legacy_baseline_match
            )
            snapshot_diagnostics.update({
                "decision": "changed" if changed else ("baseline" if not previous_hash else "unchanged"),
                "reason": "content_hash_changed" if changed else ("initial_baseline" if not previous_hash else "content_hash_same"),
            })
            website.snapshot_diagnostics = snapshot_diagnostics

            if changed:
                website_change = WebsiteChange.objects.create(
                    website=website,
                    element_selector=website.element_selector,
                    previous_hash=previous_hash,
                    new_hash=content_hash,
                    previous_value=website.last_value[:CHANGE_VALUE_MAX_LENGTH],
                    new_value=current_value[:CHANGE_VALUE_MAX_LENGTH],
                )
                WebsiteAlert.objects.get_or_create(
                    user_id=website.owner_id,
                    change=website_change,
                )
                AccountActivity.objects.create(
                    user_id=website.owner_id,
                    event_type="website_change",
                    description=f"Website change detected: {website.name[:160]}",
                    website_name=website.name,
                    website_url=website.url,
                )
                website.content_hash = content_hash
                website.last_value = current_value[:CHANGE_VALUE_MAX_LENGTH]
                website.last_changed = checked_at
                website.last_checked = checked_at
                website.last_error = None
                if website.status in {"monitoring", "error"}:
                    website.status = "monitoring"
                update_fields = [
                    "content_hash",
                    "last_value",
                    "last_changed",
                    "last_checked",
                    "last_error",
                    "check_claimed_at",
                    "status",
                    "snapshot_diagnostics",
                    "updated_at",
                ]
                website.check_claimed_at = None
                website.save(update_fields=update_fields)
            else:
                website.last_checked = checked_at
                if website.status in {"monitoring", "error"}:
                    website.status = "monitoring"
                website.last_error = None
                website.check_claimed_at = None
                update_fields = [
                    "last_checked",
                    "last_error",
                    "check_claimed_at",
                    "status",
                    "snapshot_diagnostics",
                    "updated_at",
                ]
                if not previous_hash:
                    website.content_hash = content_hash
                    update_fields.append("content_hash")
                elif legacy_baseline_match:
                    website.content_hash = content_hash
                    update_fields.append("content_hash")
                if website.last_value != current_value[:CHANGE_VALUE_MAX_LENGTH]:
                    website.last_value = current_value[:CHANGE_VALUE_MAX_LENGTH]
                    update_fields.append("last_value")
                website.save(update_fields=update_fields)

        result = {
            "website": website.name,
            "url": website.url,
            "changed": changed,
            "content_hash": content_hash,
        }
        if website_change:
            try:
                deliver_change_notification.delay(website_change.pk)
            except Exception as error:
                logger.error(
                    "Could not queue email notification for change %s, website %s (%s)",
                    website_change.pk,
                    website.id,
                    type(error).__name__,
                )
                result["notification_error"] = "Email notification could not be queued."
            try:
                whatsapp_preference = UserPreference.objects.filter(
                    user_id=website.owner_id,
                    whatsapp_notifications_enabled=True,
                ).only("whatsapp_phone_number").first()
                if whatsapp_preference is not None:
                    WhatsAppDelivery.objects.get_or_create(
                        idempotency_key=f"whatsapp:change:{website_change.pk}",
                        defaults={
                            "user_id": website.owner_id,
                            "website_id": website.pk,
                            "website_change_id": website_change.pk,
                            "recipient_masked": mask_phone_number(whatsapp_preference.whatsapp_phone_number),
                        },
                    )
                    deliver_whatsapp_notification.delay(website_change.pk)
            except Exception as error:
                logger.error(
                    "Could not queue WhatsApp notification for change %s (%s)",
                    website_change.pk,
                    type(error).__name__,
                )
                result["whatsapp_notification_error"] = "WhatsApp notification could not be queued."

        return result
    except Website.DoesNotExist:
        return {
            "changed": False,
            "error": f"Website {website_id} does not exist"
        }

    except Exception as error:
        if owner_id is not None and _account_was_deleted(owner_id):
            return {"changed": False, "skipped": "Account was deleted."}
        if website is not None:
            safe_message = _safe_monitoring_error(error)
            return _record_monitoring_error(website, safe_message, error)
        logger.error(
            "Website monitoring failed before loading website %s (%s)",
            website_id,
            type(error).__name__,
        )
        return {
            "changed": False,
            "error": "Unable to load the website for monitoring.",
        }


def _notification_retry_delay(retry_count):
    return min(60 * (2 ** min(retry_count, 6)), 3600)


@shared_task(bind=True, max_retries=5)
def deliver_change_notification(self, website_change_id):
    now = timezone.now()
    with transaction.atomic():
        try:
            change = WebsiteChange.objects.select_for_update().select_related(
                "website__owner"
            ).get(pk=website_change_id)
        except WebsiteChange.DoesNotExist:
            return {"sent": False, "skipped": "Change record no longer exists."}

        if change.notification_sent_at is not None:
            return {"sent": False, "skipped": "Notification was already delivered."}
        if _account_was_deleted(change.website.owner_id):
            return {"sent": False, "skipped": "Account was deleted."}
        if change.website.status not in {"monitoring", "error"}:
            return {"sent": False, "skipped": "Website monitoring is stopped."}
        if (
            change.notification_next_attempt_at is not None
            and change.notification_next_attempt_at > now
        ):
            return {"sent": False, "skipped": "Notification retry is not due."}

        preference = UserPreference.objects.filter(
            user_id=change.website.owner_id
        ).only("email_alerts_enabled").first()
        if preference is not None and not preference.email_alerts_enabled:
            change.notification_sent_at = now
            change.notification_next_attempt_at = None
            change.save(update_fields=["notification_sent_at", "notification_next_attempt_at"])
            return {"sent": False, "skipped": "Email alerts are disabled."}

        recipient = (change.website.owner.email or "").strip()
        if not recipient:
            logger.info(
                "Skipping email notification for website %s: owner email is not configured",
                change.website_id,
            )
            return {"sent": False, "skipped": "Owner email is not configured."}

        change.notification_attempts += 1
        change.notification_next_attempt_at = now + NOTIFICATION_CLAIM_TIMEOUT
        change.save(
            update_fields=["notification_attempts", "notification_next_attempt_at"]
        )

    # A queued notification may have waited while the account was being deleted.
    if _account_was_deleted(change.website.owner_id):
        return {"sent": False, "skipped": "Account was deleted."}

    try:
        sent = send_change_notification(change.website, change)
    except Exception as error:
        retry_delay = _notification_retry_delay(self.request.retries)
        try:
            with transaction.atomic():
                pending_change = WebsiteChange.objects.select_for_update().get(
                    pk=website_change_id
                )
                if pending_change.notification_sent_at is None:
                    pending_change.notification_next_attempt_at = (
                        timezone.now() + timedelta(seconds=retry_delay)
                    )
                    pending_change.save(update_fields=["notification_next_attempt_at"])
        except Exception as state_error:
            logger.error(
                "Could not record email retry for change %s (%s)",
                website_change_id,
                type(state_error).__name__,
            )
        logger.warning(
            "Email notification failed for change %s (attempt=%s, exception_type=%s)",
            website_change_id,
            change.notification_attempts,
            type(error).__name__,
        )
        raise self.retry(
            exc=RuntimeError("Website change email delivery failed"),
            countdown=retry_delay,
        )

    if not sent:
        WebsiteChange.objects.filter(
            pk=website_change_id,
            notification_sent_at__isnull=True,
        ).update(notification_next_attempt_at=None)
        return {"sent": False, "skipped": "Owner email is not configured."}

    sent_at = timezone.now()
    with transaction.atomic():
        pending_change = WebsiteChange.objects.select_for_update().get(
            pk=website_change_id
        )
        if pending_change.notification_sent_at is None:
            pending_change.notification_sent_at = sent_at
            pending_change.notification_next_attempt_at = None
            pending_change.save(
                update_fields=["notification_sent_at", "notification_next_attempt_at"]
            )
    return {"sent": True, "change_id": website_change_id}


@shared_task(bind=True, max_retries=2)
def deliver_whatsapp_notification(self, website_change_id):
    now = timezone.now()
    idempotency_key = f"whatsapp:change:{website_change_id}"

    def mark_skipped(category, detail):
        WhatsAppDelivery.objects.filter(idempotency_key=idempotency_key).exclude(status="accepted").update(
            status="failed",
            error_category=category,
            error_detail=detail[:255],
            claimed_at=None,
            next_attempt_at=None,
        )
        return {"accepted": False, "skipped": detail}

    try:
        change = WebsiteChange.objects.select_related("website__owner").get(pk=website_change_id)
    except WebsiteChange.DoesNotExist:
        return {"accepted": False, "skipped": "Change record no longer exists."}
    if _account_was_deleted(change.website.owner_id):
        return {"accepted": False, "skipped": "Account was deleted."}
    if change.website.status not in {"monitoring", "error"}:
        return mark_skipped("monitoring_stopped", "Website monitoring is stopped.")
    preference = UserPreference.objects.filter(user_id=change.website.owner_id).first()
    if preference is None or not preference.whatsapp_notifications_enabled:
        return mark_skipped("notifications_disabled", "WhatsApp notifications are disabled.")
    recipient = (preference.whatsapp_phone_number or "").strip()
    if not recipient:
        return mark_skipped("recipient_missing", "WhatsApp recipient is not configured.")

    key = idempotency_key
    with transaction.atomic():
        delivery, _ = WhatsAppDelivery.objects.get_or_create(
            idempotency_key=key,
            defaults={
                "user_id": change.website.owner_id,
                "website_id": change.website_id,
                "website_change_id": change.pk,
                "recipient_masked": mask_phone_number(recipient),
            },
        )
        delivery = WhatsAppDelivery.objects.select_for_update().get(pk=delivery.pk)
        if delivery.status in {"accepted", "failed"}:
            return {"accepted": delivery.status == "accepted", "skipped": "This change notification was already handled."}
        if delivery.next_attempt_at and delivery.next_attempt_at > now:
            return {"accepted": False, "skipped": "WhatsApp retry is not due."}
        if delivery.status == "sending" and delivery.claimed_at and delivery.claimed_at > now - NOTIFICATION_CLAIM_TIMEOUT:
            return {"accepted": False, "skipped": "This change notification is already being sent."}
        delivery.status = "sending"
        delivery.recipient_masked = mask_phone_number(recipient)
        delivery.attempts += 1
        delivery.claimed_at = now
        delivery.next_attempt_at = None
        delivery.save(update_fields=["status", "recipient_masked", "attempts", "claimed_at", "next_attempt_at", "updated_at"])

    detected_at = timezone.localtime(change.detected_at).strftime("%Y-%m-%d %H:%M %Z")
    try:
        result = send_message(
            recipient,
            website_name=change.website.name,
            website_url=change.website.url,
            detected_at=detected_at,
        )
    except Exception as error:
        logger.error(
            "Unexpected WhatsApp delivery error for change %s (%s)",
            change.pk,
            type(error).__name__,
        )
        result = {
            "accepted": False,
            "message_id": None,
            "category": "internal_error",
            "detail": "WebWatch could not complete the WhatsApp delivery.",
            "retryable": False,
        }
    with transaction.atomic():
        delivery = WhatsAppDelivery.objects.select_for_update().get(idempotency_key=key)
        if result["accepted"]:
            delivery.status = "accepted"
            delivery.accepted_at = timezone.now()
            delivery.provider_message_id = result.get("message_id", "")
            delivery.error_category = ""
            delivery.error_detail = ""
            delivery.save(update_fields=["status", "accepted_at", "provider_message_id", "error_category", "error_detail", "updated_at"])
            return {"accepted": True, "delivery_id": delivery.pk, "message_id": delivery.provider_message_id, "delivery_status": "unconfirmed"}
        delivery.error_category = result["category"]
        delivery.error_detail = result["detail"][:255]
        delivery.claimed_at = None
        if result.get("retryable") and self.request.retries < self.max_retries:
            delivery.status = "queued"
            retry_delay = 30 * (2 ** self.request.retries)
            delivery.next_attempt_at = timezone.now() + timedelta(seconds=retry_delay)
            delivery.save(update_fields=["status", "error_category", "error_detail", "claimed_at", "next_attempt_at", "updated_at"])
        else:
            retry_delay = None
            delivery.status = "failed"
            delivery.next_attempt_at = None
            delivery.save(update_fields=["status", "error_category", "error_detail", "claimed_at", "next_attempt_at", "updated_at"])
    if retry_delay is not None:
        raise self.retry(
            exc=RuntimeError("WhatsApp delivery temporarily failed"),
            countdown=retry_delay,
        )
    logger.warning(
        "WhatsApp delivery failed for change %s (category=%s attempt=%s)",
        change.pk,
        result["category"],
        delivery.attempts,
    )
    return {"accepted": False, "delivery_id": delivery.pk, "category": result["category"]}


@shared_task
def queue_pending_whatsapp_notifications():
    now = timezone.now()
    pending = WhatsAppDelivery.objects.filter(
        channel="whatsapp",
        status="queued",
        website_change__isnull=False,
        user__preferences__whatsapp_notifications_enabled=True,
        website_change__website__status__in=["monitoring", "error"],
    ).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
    ).order_by("created_at").values_list("website_change_id", flat=True)[:100]
    queued = 0
    for change_id in pending:
        try:
            deliver_whatsapp_notification.delay(change_id)
        except Exception as error:
            logger.error("Could not queue WhatsApp delivery for change %s (%s)", change_id, type(error).__name__)
        else:
            queued += 1
    return {"queued": queued}


@shared_task
def queue_pending_change_notifications():
    now = timezone.now()
    pending_changes = WebsiteChange.objects.filter(
        notification_sent_at__isnull=True,
        website__owner__email__gt="",
        website__status__in=["monitoring", "error"],
    ).filter(
        Q(notification_next_attempt_at__isnull=True)
        | Q(notification_next_attempt_at__lte=now)
    ).order_by("detected_at").values_list("pk", flat=True)[:100]

    queued = 0
    for change_id in pending_changes:
        try:
            deliver_change_notification.delay(change_id)
        except Exception as error:
            logger.error(
                "Could not queue email notification for change %s (%s)",
                change_id,
                type(error).__name__,
            )
        else:
            queued += 1
    return {"queued": queued}

def enqueue_website_check(website_id, *, due_only=False, now=None):
    now = now or timezone.now()
    website_query = Website.objects.filter(
        pk=website_id,
        status__in=["monitoring", "error"],
    )

    if due_only:
        website = website_query.filter(
            status__in=["monitoring", "error"]
        ).only("status", "check_interval", "last_checked").first()
        if website is None:
            return False
        if (
            website.last_checked is not None
            and now - website.last_checked < timedelta(minutes=website.check_interval)
        ):
            return False

        website_query = website_query.filter(
            status__in=["monitoring", "error"],
            check_interval=website.check_interval,
        )
        if website.last_checked is None:
            website_query = website_query.filter(last_checked__isnull=True)
        else:
            website_query = website_query.filter(last_checked=website.last_checked)

    website_query = website_query.filter(
        Q(check_claimed_at__isnull=True)
        | Q(check_claimed_at__lte=now - CHECK_CLAIM_STALE_AFTER)
    )
    if not website_query.update(check_claimed_at=now):
        return False

    try:
        check_website.delay(website_id)
    except Exception:
        Website.objects.filter(
            pk=website_id,
            check_claimed_at=now,
        ).update(check_claimed_at=None)
        raise
    return True


@shared_task
def check_due_websites():
    now = timezone.now()
    queued = 0

    for website_id in Website.objects.filter(
        status__in=["monitoring", "error"]
    ).values_list("id", flat=True):
        try:
            queued += enqueue_website_check(
                website_id,
                due_only=True,
                now=now,
            )
        except Exception as error:
            logger.error(
                "Could not queue website check for %s (%s)",
                website_id,
                type(error).__name__,
            )

    return {"queued": queued}
