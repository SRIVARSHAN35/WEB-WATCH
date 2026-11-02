"""Privacy-conscious integration with Meta's official WhatsApp Cloud API."""

import logging
import re

import requests
from django.conf import settings


logger = logging.getLogger(__name__)
PHONE_RE = re.compile(r"\+[1-9][0-9]{7,14}\Z")
GRAPH_VERSION_RE = re.compile(r"v[0-9]+\.[0-9]+\Z")
REQUEST_TIMEOUT = (4, 10)


class WhatsAppConfigurationError(Exception):
    def __init__(self, category, detail):
        super().__init__(detail)
        self.category = category
        self.detail = detail


def validate_phone_number(value):
    if not isinstance(value, str):
        raise ValueError("Enter a phone number in international format, such as +14155552671.")
    number = value.strip()
    if not PHONE_RE.fullmatch(number):
        raise ValueError("Use international E.164 format: + followed by 8 to 15 digits, with no spaces.")
    return number


def mask_phone_number(number):
    digits = re.sub(r"\D", "", number or "")
    if len(digits) < 4:
        return ""
    return f"+{'*' * max(0, len(digits) - 4)}{digits[-4:]}"


def configuration_status():
    missing = []
    if not settings.WHATSAPP_ACCESS_TOKEN:
        missing.append("access token")
    if not settings.WHATSAPP_PHONE_NUMBER_ID:
        missing.append("phone number ID")
    if not GRAPH_VERSION_RE.fullmatch(settings.WHATSAPP_GRAPH_API_VERSION):
        missing.append("valid Graph API version")
    return {
        "enabled": bool(settings.WHATSAPP_ENABLED),
        "configured": not missing,
        "missing": missing,
        "template_configured": bool(settings.WHATSAPP_TEMPLATE_NAME),
    }


def _configuration():
    if not settings.WHATSAPP_ENABLED:
        raise WhatsAppConfigurationError("disabled", "WhatsApp notifications are disabled by the administrator.")
    state = configuration_status()
    if not state["configured"]:
        raise WhatsAppConfigurationError(
            "configuration_missing",
            "WhatsApp Cloud API is not fully configured by the administrator.",
        )
    if not re.fullmatch(r"[0-9]+", settings.WHATSAPP_PHONE_NUMBER_ID):
        raise WhatsAppConfigurationError(
            "configuration_invalid",
            "The configured WhatsApp Phone Number ID is invalid.",
        )
    return (
        f"https://graph.facebook.com/{settings.WHATSAPP_GRAPH_API_VERSION}/"
        f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )


def build_message_payload(recipient, *, website_name, website_url, detected_at, test=False):
    recipient = re.sub(r"\D", "", validate_phone_number(recipient))
    if test:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": {
                "name": "hello_world",
                "language": {"code": "en_US"},
            },
        }
    if settings.WHATSAPP_TEMPLATE_NAME:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": {
                "name": settings.WHATSAPP_TEMPLATE_NAME,
                "language": {"code": settings.WHATSAPP_TEMPLATE_LANGUAGE},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "WebWatch test" if test else str(website_name)[:200]},
                        {"type": "text", "text": str(website_url)[:1000]},
                        {"type": "text", "text": str(detected_at)[:100]},
                    ],
                }],
            },
        }
    body = (
        "WebWatch test message: WhatsApp notifications are working."
        if test else
        f"WebWatch alert: A change was detected on {str(website_name)[:200]}. "
        f"Website: {str(website_url)[:1000]}. Detected at: {str(detected_at)[:100]}. "
        "Open WebWatch to review the changes."
    )
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }


def _provider_error(response):
    try:
        data = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code == 190:
        category, detail = "access_token_invalid", "Meta rejected the access token. Check or renew the server-side token."
    elif code in {132000, 132001, 132005, 132007, 132012, 132015, 132016}:
        category, detail = "template_invalid", "Meta rejected the configured message template. Verify its approval, language, and three body parameters."
    elif code == 131047:
        category, detail = "template_required", "Meta requires an approved template to start this conversation. Configure the WebWatch template."
    elif code in {131026, 133010}:
        category, detail = "recipient_unavailable", "Meta could not deliver to this recipient. Confirm the number can receive WhatsApp messages and is allowed for this sender."
    elif response.status_code == 429:
        category, detail = "rate_limited", "Meta temporarily rate-limited WhatsApp messages. WebWatch will retry shortly."
    elif response.status_code >= 500:
        category, detail = "provider_unavailable", "Meta WhatsApp service is temporarily unavailable. WebWatch will retry shortly."
    else:
        category, detail = "provider_rejected", "Meta rejected the WhatsApp message. Review the WhatsApp sender and template configuration."
    return {"accepted": False, "message_id": None, "category": category, "detail": detail,
            "retryable": response.status_code == 429 or response.status_code >= 500,
            "provider_code": code}


def send_message(recipient, *, website_name="WebWatch", website_url="", detected_at="", test=False):
    try:
        endpoint = _configuration()
        payload = build_message_payload(
            recipient,
            website_name=website_name,
            website_url=website_url,
            detected_at=detected_at,
            test=test,
        )
    except ValueError as error:
        return {"accepted": False, "message_id": None, "category": "invalid_recipient", "detail": str(error), "retryable": False}
    except WhatsAppConfigurationError as error:
        return {"accepted": False, "message_id": None, "category": error.category, "detail": error.detail, "retryable": False}

    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        logger.warning("WhatsApp Cloud API request timed out")
        return {"accepted": False, "message_id": None, "category": "provider_timeout", "detail": "Meta did not respond before the request timed out.", "retryable": True}
    except requests.exceptions.RequestException as error:
        logger.warning("WhatsApp Cloud API transport failed (%s)", type(error).__name__)
        return {"accepted": False, "message_id": None, "category": "provider_connection_error", "detail": "WebWatch could not connect to Meta WhatsApp.", "retryable": True}

    if not 200 <= response.status_code < 300:
        result = _provider_error(response)
        logger.warning(
            "WhatsApp Cloud API rejected a request (status=%s category=%s provider_code=%s)",
            response.status_code,
            result["category"],
            result["provider_code"],
        )
        return result

    try:
        data = response.json()
        messages = data.get("messages", []) if isinstance(data, dict) else []
        message_id = messages[0].get("id", "") if messages and isinstance(messages[0], dict) else ""
    except (ValueError, requests.exceptions.JSONDecodeError):
        message_id = ""
    if not message_id:
        logger.warning("WhatsApp Cloud API returned success without a message ID")
        return {"accepted": False, "message_id": None, "category": "provider_response_invalid", "detail": "Meta returned an incomplete message response.", "retryable": False}
    return {
        "accepted": True,
        "message_id": str(message_id),
        "category": "accepted",
        "detail": "Meta accepted the message for processing; recipient delivery is not confirmed.",
        "delivery_status": "unconfirmed",
        "retryable": False,
    }


def send_test_message(recipient):
    """Send Meta's sample template; acceptance does not establish delivery."""
    return send_message(recipient, test=True)
