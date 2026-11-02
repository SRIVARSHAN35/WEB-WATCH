import logging
import smtplib
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send a safe SMTP configuration test email to one recipient."

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Email address to receive the test message")
        parser.add_argument(
            "--diagnose",
            action="store_true",
            help="Test SMTP connection, TLS, and authentication without sending email.",
        )
        parser.add_argument(
            "--ssl",
            action="store_true",
            help="With --diagnose, test implicit TLS on port 465 without sending email.",
        )

    @contextmanager
    def _track_smtp_stages(self, state):
        methods = {
            "connect": "connect",
            "ehlo": "EHLO",
            "helo": "HELO",
            "starttls": "STARTTLS",
            "login": "authentication",
            "docmd": "SMTP command",
            "sendmail": "send",
            "quit": "connection close",
        }

        def wrap(method_name, original):
            def tracked(connection, *args, **kwargs):
                stage = methods[method_name]
                command = None
                if method_name == "docmd":
                    command = args[0] if args else kwargs.get("cmd")
                    if command == "AUTH":
                        stage = "AUTH command/reply"
                        state["auth_started"] = True
                    elif state.get("auth_started") and not state.get("auth_accepted"):
                        stage = "AUTH challenge response/reply"
                if method_name in {"ehlo", "helo"} and getattr(
                    connection, "_webwatch_starttls_complete", False
                ):
                    stage += " after STARTTLS"
                try:
                    result = original(connection, *args, **kwargs)
                except Exception as error:
                    if state["error"] is not error:
                        state["stage"] = stage
                        state["error"] = error
                    raise
                if method_name == "starttls":
                    connection._webwatch_starttls_complete = True
                if method_name == "docmd" and state.get("auth_started"):
                    response_code = result[0]
                    if response_code in (235, 503):
                        state["auth_accepted"] = True
                        state["stage"] = "after AUTH success"
                    elif response_code == 334:
                        state["stage"] = "AUTH challenge response"
                return result

            return tracked

        with ExitStack() as stack:
            for method_name in methods:
                original = getattr(smtplib.SMTP, method_name)
                stack.enter_context(
                    patch.object(
                        smtplib.SMTP,
                        method_name,
                        wrap(method_name, original),
                    )
                )
            yield

    def handle(self, *args, **options):
        recipient = options["recipient"]
        if options["ssl"] and not options["diagnose"]:
            raise CommandError("--ssl is supported only with --diagnose.")
        state = {"stage": "SMTP setup", "error": None}
        connection_options = {}
        if options["ssl"]:
            connection_options = {
                "host": settings.EMAIL_HOST,
                "port": 465,
                "use_tls": False,
                "use_ssl": True,
            }
        connection = get_connection(
            fail_silently=False,
            **connection_options,
        )
        try:
            with self._track_smtp_stages(state):
                if options["diagnose"]:
                    connection.open()
                    connection.close()
                    self.stdout.write(
                        self.style.SUCCESS(
                            "SMTP connection, TLS, and authentication succeeded; no email was sent."
                        )
                    )
                    return
                sent_count = send_mail(
                    subject="WebWatch SMTP configuration test",
                    message=(
                        "This is a test message from WebWatch.\n\n"
                        "If you received it, the configured Django email backend can deliver mail."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=False,
                    connection=connection,
                )
        except Exception as error:
            stage = state["stage"]
            logger.error(
                "Email test failed at SMTP stage %s (%s)",
                stage,
                type(error).__name__,
            )
            raise CommandError(
                f"Email test failed at SMTP stage {stage} "
                f"({type(error).__name__}); exception details are intentionally omitted."
            ) from None

        if sent_count != 1:
            raise CommandError(
                "Email test failed (no message was accepted by the configured backend)."
            )

        self.stdout.write(self.style.SUCCESS("Test email sent successfully."))