from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    key = settings.WEBSITE_CREDENTIAL_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured(
            "WEBSITE_CREDENTIAL_ENCRYPTION_KEY must be configured before "
            "website credentials can be used."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as error:
        raise ImproperlyConfigured(
            "WEBSITE_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key."
        ) from error


def encrypt_website_secret(value):
    if not isinstance(value, str):
        raise TypeError("Website credential values must be text.")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_website_secret(value):
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as error:
        raise ImproperlyConfigured(
            "Stored website credentials cannot be decrypted with the current key."
        ) from error