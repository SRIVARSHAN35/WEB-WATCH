import ipaddress
import socket
from urllib.parse import urlsplit


LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".test")


def validate_public_web_url(value, *, resolve=False):
    """Reject non-public HTTP destinations before saving or fetching a URL."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("Enter a valid publicly reachable HTTP or HTTPS URL.") from error

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname.lower() == "localhost"
        or hostname.lower().endswith(LOCAL_HOST_SUFFIXES)
    ):
        raise ValueError("Enter a valid publicly reachable HTTP or HTTPS URL.")

    try:
        ip_address = ipaddress.ip_address(hostname)
    except ValueError:
        ip_address = None

    if ip_address is not None:
        if not ip_address.is_global:
            raise ValueError("Private, loopback, and reserved IP addresses cannot be monitored.")
        addresses = {str(ip_address)}
    elif resolve:
        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(
                    hostname,
                    port or (443 if parsed.scheme.lower() == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as error:
            raise ValueError("The website hostname could not be resolved safely.") from error
        if not addresses:
            raise ValueError("The website hostname could not be resolved safely.")
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("Private, loopback, and reserved network addresses cannot be monitored.")

    return value


def guard_public_request(route):
    request_url = route.request.url
    scheme = request_url.split(":", 1)[0].lower()
    if scheme in {"data", "blob", "about"}:
        route.continue_()
        return
    try:
        validate_public_web_url(request_url, resolve=True)
    except ValueError:
        route.abort("blockedbyclient")
        return
    route.continue_()
