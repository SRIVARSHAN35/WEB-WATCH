import re

from django.contrib.auth import get_user_model
from django.utils.text import slugify


def unique_username_for_email(email):
    user_model = get_user_model()
    local_part = email.split("@", 1)[0]
    sanitized_local = re.sub(r"[^\w.-]+", "-", local_part)
    base_username = slugify(sanitized_local, allow_unicode=True)[:140] or "webwatch-user"
    username = base_username
    suffix = 1
    while user_model.objects.filter(username__iexact=username).exists():
        suffix_text = f"-{suffix}"
        username = f"{base_username[:150 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return username