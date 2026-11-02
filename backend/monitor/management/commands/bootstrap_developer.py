from getpass import getpass

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from monitor.auth_utils import unique_username_for_email


User = get_user_model()


class Command(BaseCommand):
    help = "Create or recover a local WebWatch developer account securely."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Email for the developer account (prompted if omitted).")

    def handle(self, *args, **options):
        email = (options.get("email") or input("Developer account email: ")).strip().lower()
        try:
            validate_email(email)
        except ValidationError as error:
            raise CommandError("Enter a valid email address.") from error

        matches = list(User.objects.filter(email__iexact=email)[:2])
        if len(matches) > 1:
            raise CommandError("More than one account uses this email. Resolve the duplicate accounts first.")

        with transaction.atomic():
            if matches:
                user = User.objects.select_for_update().get(pk=matches[0].pk)
                if not user.is_active:
                    raise CommandError("This account is inactive. Reactivate it through your account administration process first.")
                account_action = "Updated existing WebWatch account"
            else:
                username = unique_username_for_email(email)
                user = User(username=username, email=email)
                user.set_unusable_password()
                user.save(force_insert=True)
                account_action = "Created WebWatch developer account"

            password = getpass("New developer password: ")
            confirmation = getpass("Confirm new developer password: ")
            if not password or password != confirmation:
                raise CommandError("Passwords were empty or did not match. No account changes were saved.")
            try:
                password_validation.validate_password(password, user)
            except ValidationError as error:
                raise CommandError(" ".join(error.messages)) from error

            user.is_staff = True
            user.set_password(password)
            user.save(update_fields=["password", "is_staff"])

        self.stdout.write(self.style.SUCCESS(f"{account_action}. Sign in with {user.email}."))
        self.stdout.write("This account has developer-console staff access; it was not granted Django superuser access.")
