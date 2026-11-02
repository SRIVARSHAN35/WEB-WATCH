import logging
from datetime import timezone as datetime_timezone
from email.mime.image import MIMEImage
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone


logger = logging.getLogger(__name__)

EMAIL_BANNER_PATH = Path(__file__).resolve().parent / "static" / "monitor" / "email_banner.png"
EMAIL_BANNER_CONTENT_ID = "webwatch-email-banner"


def _safe_http_url(value, *, require_https=False):
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        parsed.port  # Validate malformed ports before using the URL.
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (require_https and parsed.scheme != "https")
        or (
            parsed.scheme != "https"
            and hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        return ""
    return candidate


def _display_datetime(value):
    if value is None:
        return "Not available"
    if timezone.is_naive(value):
        value = timezone.make_aware(value, datetime_timezone.utc)
    return timezone.localtime(value).strftime("%b %d, %Y at %I:%M %p %Z")


def send_change_notification(website, website_change):
    recipient = (website.owner.email or "").strip()
    if not recipient:
        logger.info(
            "Skipping email notification for website %s: owner email is not configured",
            website.id,
        )
        return False

    website_name = " ".join((website.name or "Website").split())
    subject = f"WebWatch Alert: Changes Detected on {website_name}"
    website_url = _safe_http_url(website.url)
    dashboard_url = _safe_http_url(settings.WEBWATCH_DASHBOARD_URL)
    configured_banner_url = _safe_http_url(
        settings.WEBWATCH_EMAIL_BANNER_URL,
        require_https=True,
    )
    use_inline_banner = not configured_banner_url and EMAIL_BANNER_PATH.is_file()
    banner_src = configured_banner_url or (
        f"cid:{EMAIL_BANNER_CONTENT_ID}" if use_inline_banner else ""
    )
    context = {
        "website_name": website_name,
        "website_url": website_url,
        "website_url_display": website_url or "Unavailable",
        "website_status": website.get_status_display() or "Unknown",
        "last_checked": _display_datetime(website.last_checked),
        "check_interval": (
            f"{website.check_interval} minutes"
            if website.check_interval
            else "Not available"
        ),
        "detected_at": _display_datetime(website_change.detected_at),
        "dashboard_url": dashboard_url,
        "banner_src": banner_src,
    }
    message = render_to_string("emails/website_change.txt", context)
    html_message = render_to_string("emails/website_change.html", context)
    email = EmailMultiAlternatives(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
    )
    email.attach_alternative(html_message, "text/html")
    if use_inline_banner:
        try:
            image = MIMEImage(EMAIL_BANNER_PATH.read_bytes(), _subtype="png")
            image.add_header("Content-ID", f"<{EMAIL_BANNER_CONTENT_ID}>")
            image.add_header("Content-Disposition", "inline", filename="webwatch-banner.png")
            email.attach(image)
        except OSError as error:
            logger.warning(
                "Could not attach WebWatch email banner (exception_type=%s)",
                type(error).__name__,
            )
            context["banner_src"] = ""
            message = render_to_string("emails/website_change.txt", context)
            html_message = render_to_string("emails/website_change.html", context)
            email.body = message
            email.alternatives.clear()
            email.attach_alternative(html_message, "text/html")

    sent_count = email.send(fail_silently=False)
    if sent_count != 1:
        raise RuntimeError("Django did not deliver the website change email")
    return True
