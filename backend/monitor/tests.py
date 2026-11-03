import os
from urllib.parse import parse_qs, urlsplit
from datetime import timedelta
from hashlib import sha256
from unittest import SkipTest
from unittest.mock import Mock, patch
from unittest import skipUnless

from celery.exceptions import Retry
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError
from django.urls import reverse
from django.test import TestCase, override_settings
from django.test import SimpleTestCase
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError, TransportError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from config.celery import app as celery_app
from config import settings as project_settings
from .models import AccountActivity, DeletedAccountMarker, DeveloperAuditLog, GoogleIdentity, UserPreference, Website, WebsiteAlert, WebsiteChange, WebsiteCredential, WhatsAppDelivery
from .notifications import send_change_notification
from .tasks import (
	CHROMIUM_LAUNCH_ARGS,
	check_due_websites,
	check_website,
	deliver_change_notification,
	deliver_whatsapp_notification,
	_extract_monitored_content,
	queue_pending_change_notifications,
	queue_pending_whatsapp_notifications,
)
from .website_login import CHROMIUM_LAUNCH_ARGS as LOGIN_CHROMIUM_LAUNCH_ARGS
from .url_safety import validate_public_web_url
from .snapshots import extract_snapshot, validate_ignore_selectors
from .services.whatsapp import build_message_payload, mask_phone_number, send_message, send_test_message, validate_phone_number

User = get_user_model()


class MonitorDemoEndpointTests(TestCase):
	def setUp(self):
		cache.delete("webwatch_monitor_demo_status")

	def test_demo_endpoint_is_disabled_when_demo_flag_is_off_even_with_debug_disabled(self):
		with override_settings(DEBUG=False, MONITOR_DEMO_ENABLED=False):
			response = self.client.get("/api/monitor-demo/status/")
		self.assertEqual(response.status_code, 404)

	def test_demo_endpoint_can_run_with_debug_disabled_when_explicitly_enabled(self):
		with override_settings(DEBUG=False, MONITOR_DEMO_ENABLED=True):
			initial = self.client.get("/api/monitor-demo/status/")
			updated = self.client.post("/api/monitor-demo/status/")
		self.assertEqual(initial.status_code, 200)
		self.assertEqual(initial.json(), {"status": "Applied"})
		self.assertEqual(updated.status_code, 200)
		self.assertEqual(updated.json(), {"status": "Rejected"})


class MobileApiAccessTests(APITestCase):
	@override_settings(
		ALLOWED_HOSTS=["testserver", "backend.example.test"],
		CORS_ALLOWED_ORIGINS=["https://frontend.example.test"],
	)
	def test_cors_preflight_allows_only_configured_frontend_origin(self):
		response = self.client.options(
			reverse("auth-login"),
			HTTP_HOST="backend.example.test",
			HTTP_ORIGIN="https://frontend.example.test",
			HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			response["Access-Control-Allow-Origin"],
			"https://frontend.example.test",
		)
		self.assertNotIn("Access-Control-Allow-Credentials", response)

	@override_settings(
		ALLOWED_HOSTS=["testserver", "backend.example.test"],
		CORS_ALLOWED_ORIGINS=["https://frontend.example.test"],
	)
	def test_backend_api_route_accepts_configured_host(self):
		response = self.client.get(
			reverse("website-list"),
			HTTP_HOST="backend.example.test",
		)

		self.assertEqual(response.status_code, 401)

	def test_current_host_and_cors_configuration_are_not_wildcarded(self):
		self.assertIn("localhost", settings.ALLOWED_HOSTS)
		self.assertIn("127.0.0.1", settings.ALLOWED_HOSTS)
		self.assertNotIn("*", settings.ALLOWED_HOSTS)
		self.assertNotIn("*", settings.CORS_ALLOWED_ORIGINS)
		self.assertLess(
			settings.MIDDLEWARE.index("corsheaders.middleware.CorsMiddleware"),
			settings.MIDDLEWARE.index("django.middleware.common.CommonMiddleware"),
		)


class CeleryMonitoringScheduleTests(SimpleTestCase):
	def test_beat_schedules_due_website_checks_each_minute(self):
		self.assertEqual(
			celery_app.conf.beat_schedule["check-websites-every-minute"],
			{
				"task": "monitor.tasks.check_due_websites",
				"schedule": 60.0,
			},
		)
		self.assertEqual(
			celery_app.conf.beat_schedule["retry-change-notifications-every-minute"],
			{
				"task": "monitor.tasks.queue_pending_change_notifications",
				"schedule": 60.0,
			},
		)
		self.assertEqual(
			celery_app.conf.beat_schedule["retry-whatsapp-notifications-every-minute"],
			{
				"task": "monitor.tasks.queue_pending_whatsapp_notifications",
				"schedule": 60.0,
			},
		)

	def test_beat_schedule_uses_django_timezone_and_registered_task(self):
		self.assertEqual(celery_app.conf.timezone, "Asia/Kolkata")
		self.assertIn("monitor.tasks.check_due_websites", celery_app.tasks)
		self.assertIn("monitor.tasks.deliver_whatsapp_notification", celery_app.tasks)
		self.assertIn("monitor.tasks.queue_pending_whatsapp_notifications", celery_app.tasks)


class DeploymentRuntimeConfigurationTests(SimpleTestCase):
	@patch.dict(
		os.environ,
		{
			"DB_SSL_MODE": "VERIFY_IDENTITY",
			"DB_SSL_CA": "/run/secrets/db-ca.pem",
			"DB_SSL_CERT": "/run/secrets/db-client.pem",
			"DB_SSL_KEY": "/run/secrets/db-client.key",
		},
	)
	def test_mysql_tls_options_are_configurable(self):
		self.assertEqual(
			project_settings._mysql_options(),
			{
				"connect_timeout": 10,
				"ssl_mode": "VERIFY_IDENTITY",
				"ssl": {
					"ca": "/run/secrets/db-ca.pem",
					"cert": "/run/secrets/db-client.pem",
					"key": "/run/secrets/db-client.key",
				},
			},
		)

	@patch.dict(os.environ, {"DB_SSL_MODE": "NOT_A_MODE"})
	def test_mysql_tls_rejects_unknown_mode(self):
		with self.assertRaises(ImproperlyConfigured):
			project_settings._mysql_options()

	@patch.dict(os.environ, {"DB_SSL_CERT": "/run/secrets/db-client.pem", "DB_SSL_KEY": ""})
	def test_mysql_tls_requires_client_certificate_pair(self):
		with self.assertRaises(ImproperlyConfigured):
			project_settings._mysql_options()

	def test_chromium_launch_does_not_force_single_process_mode(self):
		self.assertNotIn("--single-process", CHROMIUM_LAUNCH_ARGS)
		self.assertNotIn("--single-process", LOGIN_CHROMIUM_LAUNCH_ARGS)


class WebsiteChangesApiTests(APITestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="history-user",
			email="history@example.com",
			password="Str0ng!HistoryPass",
		)
		self.client.force_authenticate(self.user)
		self.website = Website.objects.create(
			name="Primary test website",
			url="https://example.com/",
			owner=self.user,
		)
		self.url = reverse("website-changes", kwargs={"pk": self.website.pk})

	def test_returns_empty_list_when_website_has_no_changes(self):
		response = self.client.get(self.url)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data, [])


	def test_returns_only_website_changes_newest_first(self):
		older_change = WebsiteChange.objects.create(
			website=self.website,
			previous_hash="a" * 64,
			new_hash="b" * 64,
		)
		newer_change = WebsiteChange.objects.create(
			website=self.website,
			previous_hash="b" * 64,
			new_hash="c" * 64,
		)
		other_website = Website.objects.create(
			name="Other test website",
			url="https://other.example.com/",
			owner=self.user,
		)
		WebsiteChange.objects.create(
			website=other_website,
			previous_hash="d" * 64,
			new_hash="e" * 64,
		)

		now = timezone.now()
		WebsiteChange.objects.filter(pk=older_change.pk).update(
			detected_at=now - timedelta(minutes=2)
		)
		WebsiteChange.objects.filter(pk=newer_change.pk).update(
			detected_at=now - timedelta(minutes=1)
		)

		response = self.client.get(self.url)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			[item["id"] for item in response.data],
			[newer_change.pk, older_change.pk],
		)
		self.assertEqual(response.data[0]["website"], self.website.pk)
		self.assertEqual(
			set(response.data[0]),
			{
				"id", "website", "element_selector", "detected_at",
				"previous_hash", "new_hash", "previous_value", "new_value",
			},
		)
		self.assertEqual(response.data[0]["element_selector"], "body")

	def test_invalid_website_returns_not_found(self):
		response = self.client.get(
			reverse("website-changes", kwargs={"pk": self.website.pk + 100})
		)

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

	def test_changes_endpoint_rejects_write_methods(self):
		requests = (
			self.client.post,
			self.client.put,
			self.client.patch,
			self.client.delete,
		)

		for request in requests:
			with self.subTest(method=request.__name__):
				response = request(self.url, {}, format="json")
				self.assertEqual(
					response.status_code,
					status.HTTP_405_METHOD_NOT_ALLOWED,
				)

	def test_clear_history_uses_separate_owner_scoped_action_and_keeps_baseline(self):
		self.website.content_hash = "a" * 64
		self.website.last_changed = timezone.now()
		self.website.save(update_fields=["content_hash", "last_changed"])
		change = WebsiteChange.objects.create(
			website=self.website,
			previous_hash="a" * 64,
			new_hash="b" * 64,
		)

		response = self.client.post(
			reverse("website-clear-change-history", kwargs={"pk": self.website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(WebsiteChange.objects.filter(pk=change.pk).exists())
		self.website.refresh_from_db()
		self.assertEqual(self.website.content_hash, "a" * 64)
		self.assertIsNone(self.website.last_changed)


class WebsiteAlertsApiTests(APITestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="alert-user", email="alert@example.com", password="Str0ng!AlertPass")
		self.other_user = User.objects.create_user(username="other-alert-user", email="other-alert@example.com", password="Str0ng!AlertPass")
		self.website = Website.objects.create(name="Alert test site", url="https://example.com/", owner=self.user)
		self.url = reverse("website-changes", kwargs={"pk": self.website.pk})
		self.change = WebsiteChange.objects.create(website=self.website, previous_hash="a" * 64, new_hash="b" * 64,
			previous_value="<script>secret-cookie</script>", new_value="updated content")
		self.alert = WebsiteAlert.objects.create(user=self.user, change=self.change)
		self.client.force_authenticate(self.user)

	def test_list_is_newest_first_and_contains_safe_details_only(self):
		new_change = WebsiteChange.objects.create(website=self.website, previous_hash="b" * 64, new_hash="c" * 64)
		new_alert = WebsiteAlert.objects.create(user=self.user, change=new_change)
		response = self.client.get("/api/alerts/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual([row["id"] for row in response.data["results"]], [new_alert.pk, self.alert.pk])
		self.assertEqual(response.data["unread_count"], 2)
		self.assertNotIn("previous_hash", str(response.data))
		self.assertNotIn("new_hash", str(response.data))
		self.assertNotIn("secret-cookie", str(response.data))
		self.assertNotIn("<script>", str(response.data))

	def test_unauthenticated_requests_are_rejected(self):
		self.client.force_authenticate(user=None)
		self.assertEqual(self.client.get("/api/alerts/").status_code, status.HTTP_401_UNAUTHORIZED)

	def test_users_cannot_retrieve_or_modify_another_users_alert(self):
		self.client.force_authenticate(self.other_user)
		self.assertEqual(self.client.get(f"/api/alerts/{self.alert.pk}/").status_code, status.HTTP_404_NOT_FOUND)
		self.assertEqual(self.client.post(f"/api/alerts/{self.alert.pk}/mark-read/").status_code, status.HTTP_404_NOT_FOUND)

	def test_mark_read_persists_and_count_is_user_scoped(self):
		response = self.client.post(f"/api/alerts/{self.alert.pk}/mark-read/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.alert.refresh_from_db()
		self.assertTrue(self.alert.is_read)
		self.assertIsNotNone(self.alert.read_at)
		self.assertEqual(self.client.get("/api/alerts/unread-count/").data["unread_count"], 0)

	def test_mark_all_read_only_affects_current_user(self):
		other_change = WebsiteChange.objects.create(website=Website.objects.create(name="Other", url="https://other.example/", owner=self.other_user), new_hash="c" * 64)
		other_alert = WebsiteAlert.objects.create(user=self.other_user, change=other_change)
		response = self.client.post("/api/alerts/mark-all-read/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.alert.refresh_from_db()
		other_alert.refresh_from_db()
		self.assertTrue(self.alert.is_read)
		self.assertFalse(other_alert.is_read)


class AccountApiTests(APITestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="account-one", email="one@example.com", password="Str0ng!AccountPass")
		self.other_user = User.objects.create_user(username="account-two", email="two@example.com", password="Str0ng!AccountPass")
		self.client.force_authenticate(self.user)

	def test_profile_returns_only_the_authenticated_user_and_allows_name_edit(self):
		response = self.client.get("/api/account/profile/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["email"], self.user.email)
		self.assertNotIn("password", response.data)
		self.assertEqual(self.client.patch("/api/account/profile/", {"first_name": "Updated"}, format="json").data["name"], "Updated")

	def test_profile_accepts_the_token_authentication_used_by_both_sign_in_flows(self):
		token = Token.objects.create(user=self.user)
		self.client.force_authenticate(user=None)
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

		response = self.client.get("/api/account/profile/")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["id"], self.user.pk)
		self.assertEqual(response.data["email"], self.user.email)
		self.assertNotIn("password", response.data)

	def test_profile_rejects_an_expired_or_unknown_token(self):
		self.client.force_authenticate(user=None)
		self.client.credentials(HTTP_AUTHORIZATION="Token expired-or-unknown")

		self.assertEqual(self.client.get("/api/account/profile/").status_code, status.HTTP_401_UNAUTHORIZED)

	def test_profile_rejects_email_edits(self):
		response = self.client.patch("/api/account/profile/", {"email": self.other_user.email}, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.user.refresh_from_db()
		self.assertNotEqual(self.user.email, self.other_user.email)

	def test_settings_persist_email_preference_and_validate_theme(self):
		response = self.client.get("/api/account/settings/")
		self.assertTrue(response.data["email_alerts_enabled"])
		self.assertEqual(response.data["theme"], "system")
		self.assertEqual(self.client.patch("/api/account/settings/", {"email_alerts_enabled": False, "theme": "dark"}, format="json").status_code, status.HTTP_200_OK)
		self.assertEqual(self.client.get("/api/account/settings/").data["theme"], "dark")
		self.assertFalse(self.client.get("/api/account/settings/").data["email_alerts_enabled"])
		self.assertEqual(self.client.patch("/api/account/settings/", {"theme": []}, format="json").status_code, status.HTTP_400_BAD_REQUEST)

	def test_history_is_private_and_newest_first(self):
		AccountActivity.objects.create(user=self.user, event_type="login", description="My login")
		AccountActivity.objects.create(user=self.other_user, event_type="login", description="Other login")
		response = self.client.get("/api/account/history/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual([row["description"] for row in response.data["results"]], ["My login"])

	def test_history_returns_an_explicit_empty_page_for_new_accounts(self):
		response = self.client.get("/api/account/history/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["results"], [])
		self.assertFalse(response.data["has_more"])

	def test_profile_settings_and_history_require_authentication(self):
		self.client.force_authenticate(user=None)
		for path in ("/api/account/profile/", "/api/account/settings/", "/api/account/history/"):
			with self.subTest(path=path):
				self.assertEqual(self.client.get(path).status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(
	EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	WEBWATCH_DASHBOARD_URL="https://webwatch.example.test/dashboard",
	WEBWATCH_PASSWORD_RESET_URL="",
	ALLOWED_HOSTS=["attacker.example", "testserver"],
)
class PasswordRecoveryApiTests(APITestCase):
	def setUp(self):
		cache.clear()
		self.user = User.objects.create_user(
			username="reset-user", email="reset@example.com", password="Str0ng!OldPassword",
		)
		self.google_user = User.objects.create_user(
			username="google-reset-user", email="google-reset@example.com", password=None,
		)

	def request_reset_link(self):
		return self.client.post("/api/auth/password-reset/", {"email": self.user.email}, format="json")

	def get_link_parts(self):
		link = next(line for line in mail.outbox[-1].body.splitlines() if line.startswith("https://"))
		parsed = urlsplit(link)
		self.assertEqual(parsed.netloc, "webwatch.example.test")
		self.assertTrue(parsed.path.startswith("/reset-password/"))
		return parsed.path.rstrip("/").split("/")[-1], parse_qs(parsed.fragment)["token"][0]

	def test_reset_request_is_neutral_and_uses_configured_host_not_request_host(self):
		known = self.client.post("/api/auth/password-reset/", {"email": self.user.email}, format="json", HTTP_HOST="attacker.example")
		unknown = self.client.post("/api/auth/password-reset/", {"email": "missing@example.com"}, format="json")
		self.assertEqual(known.status_code, status.HTTP_200_OK)
		self.assertEqual(known.data, unknown.data)
		self.assertEqual(len(mail.outbox), 1)
		self.get_link_parts()
		self.assertNotIn("attacker.example", mail.outbox[0].body)

	def test_google_only_user_gets_neutral_response_without_reset_email(self):
		response = self.client.post("/api/auth/password-reset/", {"email": self.google_user.email}, format="json")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["detail"], "If an account matches that email, password reset instructions will be sent.")
		self.assertEqual(len(mail.outbox), 0)

	@patch("monitor.auth_views.send_mail", side_effect=OSError("SMTP rejected private-canary-value"))
	def test_smtp_failure_is_logged_safely_and_keeps_generic_response(self, send_mail_mock):
		with self.assertLogs("monitor.auth_views", level="ERROR") as captured:
			response = self.request_reset_link()

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["detail"], "If an account matches that email, password reset instructions will be sent.")
		self.assertEqual(len(mail.outbox), 0)
		send_mail_mock.assert_called_once()
		log_output = "\n".join(captured.output)
		self.assertIn("Password reset email submission failed", log_output)
		self.assertIn("OSError", log_output)
		self.assertNotIn("private-canary-value", log_output)
		self.assertNotIn(self.user.email, log_output)

	@override_settings(WEBWATCH_PASSWORD_RESET_URL="not-a-valid-url")
	@patch("monitor.auth_views.send_mail")
	def test_invalid_reset_frontend_url_does_not_attempt_email(self, send_mail_mock):
		with self.assertLogs("monitor.auth_views", level="ERROR") as captured:
			response = self.request_reset_link()

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["detail"], "If an account matches that email, password reset instructions will be sent.")
		send_mail_mock.assert_not_called()
		self.assertIn("configured frontend URL is invalid", "\n".join(captured.output))

	def test_valid_reset_changes_password_revokes_token_and_token_cannot_be_reused(self):
		Token.objects.create(user=self.user)
		self.request_reset_link()
		uid, token = self.get_link_parts()
		self.assertEqual(self.client.post("/api/auth/password-reset/validate/", {"uid": uid, "token": token}, format="json").status_code, status.HTTP_200_OK)
		payload = {"uid": uid, "token": token, "new_password": "N3w!UniquePassword-4098", "confirm_password": "N3w!UniquePassword-4098"}
		response = self.client.post("/api/auth/password-reset/confirm/", payload, format="json")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password(payload["new_password"]))
		self.assertFalse(Token.objects.filter(user=self.user).exists())
		self.assertEqual(self.client.post("/api/auth/password-reset/confirm/", payload, format="json").status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(self.client.post("/api/auth/login/", {"email": self.user.email, "password": "Str0ng!OldPassword"}, format="json").status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(self.client.post("/api/auth/login/", {"email": self.user.email, "password": payload["new_password"]}, format="json").status_code, status.HTTP_200_OK)

	def test_invalid_and_expired_tokens_are_rejected(self):
		invalid = self.client.post("/api/auth/password-reset/confirm/", {
			"uid": "not-a-valid-id", "token": "invalid", "new_password": "N3w!UniquePassword-4098", "confirm_password": "N3w!UniquePassword-4098",
		}, format="json")
		self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
		self.request_reset_link()
		uid, token = self.get_link_parts()
		with override_settings(PASSWORD_RESET_TIMEOUT=-1):
			expired = self.client.post("/api/auth/password-reset/confirm/", {
				"uid": uid, "token": token, "new_password": "N3w!UniquePassword-4098", "confirm_password": "N3w!UniquePassword-4098",
			}, format="json")
		self.assertEqual(expired.status_code, status.HTTP_400_BAD_REQUEST)

	def test_password_change_requires_current_password_and_revokes_api_token(self):
		token = Token.objects.create(user=self.user)
		self.client.force_authenticate(self.user)
		bad = self.client.post("/api/auth/password/change/", {
			"current_password": "wrong", "new_password": "N3w!UniquePassword-4098", "confirm_password": "N3w!UniquePassword-4098",
		}, format="json")
		self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
		good = self.client.post("/api/auth/password/change/", {
			"current_password": "Str0ng!OldPassword", "new_password": "N3w!UniquePassword-4098", "confirm_password": "N3w!UniquePassword-4098",
		}, format="json")
		self.assertEqual(good.status_code, status.HTTP_200_OK)
		self.assertFalse(Token.objects.filter(pk=token.pk).exists())
		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password("N3w!UniquePassword-4098"))

	def test_reset_requests_are_rate_limited_by_client_not_account_existence(self):
		for number in range(5):
			response = self.client.post("/api/auth/password-reset/", {"email": f"unknown{number}@example.com"}, format="json")
			self.assertEqual(response.status_code, status.HTTP_200_OK)
		limited = self.client.post("/api/auth/password-reset/", {"email": self.user.email}, format="json")
		self.assertEqual(limited.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class SnapshotPipelineTests(SimpleTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.playwright = sync_playwright().start()
		try:
			cls.browser = cls.playwright.chromium.launch()
		except Exception as error:
			cls.playwright.stop()
			raise SkipTest(f"Playwright Chromium is unavailable: {type(error).__name__}")

	@classmethod
	def tearDownClass(cls):
		cls.browser.close()
		cls.playwright.stop()
		super().tearDownClass()

	def setUp(self):
		self.page = self.browser.new_page()

	def tearDown(self):
		self.page.close()

	def snapshot(self, html, ignores=()):
		self.page.set_content(html)
		return extract_snapshot(self.page, "body", ignores)

	def test_timer_changes_are_normalized_but_price_changes_are_detected(self):
		before = self.snapshot('<div class="countdown"><span>00:00:05</span></div><p>Price: $19.99</p>')
		after_timer = self.snapshot('<div class="countdown"><span>00:00:04</span></div><p>Price: $19.99</p>')
		after_price = self.snapshot('<div class="countdown"><span>00:00:04</span></div><p>Price: $24.99</p>')
		self.assertEqual(before.content_hash, after_timer.content_hash)
		self.assertNotEqual(after_timer.content_hash, after_price.content_hash)
		self.assertGreaterEqual(before.diagnostics["normalized_timer_regions"], 1)

	def test_stock_and_meaningful_text_changes_remain_detectable(self):
		in_stock = self.snapshot("<p>In stock</p><p>New job opening: Engineer</p>")
		out_stock = self.snapshot("<p>Out of stock</p><p>New job opening: Engineer</p>")
		new_job = self.snapshot("<p>Out of stock</p><p>New job opening: Designer</p>")
		self.assertNotEqual(in_stock.content_hash, out_stock.content_hash)
		self.assertNotEqual(out_stock.content_hash, new_job.content_hash)

	def test_ticker_motion_attributes_do_not_create_a_change(self):
		before = self.snapshot('<div class="ticker active" style="transform: translateX(0px);">Latest: 10</div>')
		after = self.snapshot('<div class="ticker current" style="transform: translateX(-8px);">Latest: 10</div>')
		content_change = self.snapshot('<div class="ticker current" style="transform: translateX(-8px);">Latest: 11</div>')
		self.assertEqual(before.content_hash, after.content_hash)
		self.assertNotEqual(after.content_hash, content_change.content_hash)

	def test_user_ignore_selector_excludes_only_the_selected_region(self):
		before = self.snapshot('<h1>Product</h1><div class="ticker">A</div><p>$20</p>', [".ticker"])
		after = self.snapshot('<h1>Product</h1><div class="ticker">B</div><p>$20</p>', [".ticker"])
		price_change = self.snapshot('<h1>Product</h1><div class="ticker">B</div><p>$21</p>', [".ticker"])
		self.assertEqual(before.content_hash, after.content_hash)
		self.assertNotEqual(after.content_hash, price_change.content_hash)
		self.assertEqual(after.diagnostics["configured_ignored_regions"], 1)

	def test_invalid_ignore_selectors_are_rejected(self):
		for selector in ("*", "body, .price", ".price:hover", ":has(.foo)", "div > span"):
			with self.subTest(selector=selector), self.assertRaises(ValueError):
				validate_ignore_selectors([selector])
		self.assertEqual(validate_ignore_selectors([" .countdown ", ".countdown"]), [".countdown"])

	def test_empty_document_is_reported_without_a_content_hash(self):
		self.page.set_content("<html><body></body></html>")
		with self.assertRaisesRegex(ValueError, "comparable content"):
			extract_snapshot(self.page, "body")


class WhatsAppServiceTests(SimpleTestCase):
	def meta_config(self, **overrides):
		values = {
			"WHATSAPP_ENABLED": True,
			"WHATSAPP_ACCESS_TOKEN": "test-access-token-never-logged",
			"WHATSAPP_PHONE_NUMBER_ID": "1234567890",
			"WHATSAPP_BUSINESS_ACCOUNT_ID": "9988776655",
			"WHATSAPP_GRAPH_API_VERSION": "v26.0",
			"WHATSAPP_TEMPLATE_NAME": "webwatch_change_alert",
			"WHATSAPP_TEMPLATE_LANGUAGE": "en",
		}
		values.update(overrides)
		return override_settings(**values)

	def test_phone_number_requires_e164_and_history_masks_it(self):
		self.assertEqual(validate_phone_number("+14155552671"), "+14155552671")
		self.assertEqual(mask_phone_number("+14155552671"), "+*******2671")
		with self.assertRaises(ValueError):
			validate_phone_number("415-555-2671")

	def test_template_payload_has_three_ordered_body_parameters(self):
		with self.meta_config():
			payload = build_message_payload(
				"+14155552671", website_name="Example", website_url="https://example.com/",
				detected_at="2026-10-28 10:00 UTC",
			)
		self.assertEqual(payload["type"], "template")
		self.assertEqual(payload["template"]["name"], "webwatch_change_alert")
		self.assertEqual(len(payload["template"]["components"][0]["parameters"]), 3)
		self.assertEqual(payload["template"]["components"][0]["parameters"][0]["text"], "Example")

	def test_successful_cloud_api_request_uses_https_bearer_and_timeout(self):
		response = Mock(status_code=200)
		response.json.return_value = {"messages": [{"id": "wamid.test123"}]}
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post", return_value=response) as post:
			result = send_message("+14155552671", website_name="Example", website_url="https://example.com/", detected_at="now")
		self.assertTrue(result["accepted"])
		self.assertEqual(result["message_id"], "wamid.test123")
		self.assertEqual(result["delivery_status"], "unconfirmed")
		args, kwargs = post.call_args
		self.assertEqual(args[0], "https://graph.facebook.com/v26.0/1234567890/messages")
		self.assertTrue(kwargs["headers"]["Authorization"].startswith("Bearer "))
		self.assertEqual(kwargs["json"]["to"], "14155552671")
		self.assertGreater(kwargs["timeout"][1], 0)

	def test_test_message_always_uses_hello_world_template_and_en_us(self):
		with self.meta_config():
			payload = build_message_payload(
				"+14155552671", website_name="ignored", website_url="ignored", detected_at="ignored", test=True,
			)
		self.assertEqual(payload["type"], "template")
		self.assertEqual(payload["to"], "14155552671")
		self.assertEqual(payload["template"], {"name": "hello_world", "language": {"code": "en_US"}})

	def test_plain_text_payload_is_available_without_a_template(self):
		with self.meta_config(WHATSAPP_TEMPLATE_NAME=""):
			payload = build_message_payload(
				"+14155552671", website_name="Example", website_url="https://example.com/", detected_at="now",
			)
		self.assertEqual(payload["type"], "text")
		self.assertIn("https://example.com/", payload["text"]["body"])

	def test_disabled_or_incomplete_configuration_does_not_call_meta(self):
		with self.meta_config(WHATSAPP_ENABLED=False), patch("monitor.services.whatsapp.requests.post") as post:
			result = send_message("+14155552671")
		self.assertFalse(result["accepted"])
		self.assertEqual(result["category"], "disabled")
		post.assert_not_called()
		with self.meta_config(WHATSAPP_ACCESS_TOKEN=""), patch("monitor.services.whatsapp.requests.post") as post:
			result = send_message("+14155552671")
		self.assertEqual(result["category"], "configuration_missing")
		post.assert_not_called()

	def test_test_message_service_uses_hello_world_on_the_configured_sender(self):
		response = Mock(status_code=200)
		response.json.return_value = {"messages": [{"id": "wamid.hello"}]}
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post", return_value=response) as post:
			result = send_test_message("+14155552671")
		self.assertTrue(result["accepted"])
		self.assertEqual(result["message_id"], "wamid.hello")
		self.assertEqual(post.call_args.kwargs["json"]["template"]["name"], "hello_world")

	def test_http_success_with_malformed_body_is_not_reported_as_accepted(self):
		response = Mock(status_code=200)
		response.json.return_value = {"messages": []}
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post", return_value=response):
			result = send_message("+14155552671")
		self.assertFalse(result["accepted"])
		self.assertIsNone(result["message_id"])
		self.assertEqual(result["category"], "provider_response_invalid")

	def test_invalid_number_and_provider_token_and_template_errors_are_safe(self):
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post") as post:
			result = send_message("555")
		self.assertEqual(result["category"], "invalid_recipient")
		post.assert_not_called()
		response = Mock(status_code=400)
		response.json.return_value = {"error": {"code": 190, "message": "private provider text"}}
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post", return_value=response):
			result = send_message("+14155552671")
		self.assertEqual(result["category"], "access_token_invalid")
		self.assertNotIn("private provider text", result["detail"])
		response.json.return_value = {"error": {"code": 132001, "message": "secret details"}}
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post", return_value=response):
			result = send_message("+14155552671")
		self.assertEqual(result["category"], "template_invalid")
		self.assertNotIn("secret details", result["detail"])

	def test_rate_limits_and_timeouts_are_classified_for_bounded_retries(self):
		response = Mock(status_code=429)
		response.json.return_value = {}
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post", return_value=response):
			self.assertTrue(send_message("+14155552671")["retryable"])
		with self.meta_config(), patch("monitor.services.whatsapp.requests.post", side_effect=__import__("requests").exceptions.Timeout()):
			self.assertEqual(send_message("+14155552671")["category"], "provider_timeout")


class WhatsAppSettingsApiTests(APITestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="whatsapp-owner", email="whatsapp-owner@example.com",
			password="Str0ng!WhatsAppPass",
		)
		self.client.force_authenticate(self.user)

	def test_settings_and_test_endpoints_require_authentication(self):
		self.client.force_authenticate(user=None)
		self.assertEqual(self.client.get("/api/notifications/whatsapp/settings/").status_code, status.HTTP_401_UNAUTHORIZED)
		self.assertEqual(self.client.post("/api/notifications/whatsapp/test/", {}, format="json").status_code, status.HTTP_401_UNAUTHORIZED)

	def test_settings_are_owner_scoped_and_update_only_supported_fields(self):
		response = self.client.get("/api/notifications/whatsapp/settings/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertFalse(response.data["enabled"])
		updated = self.client.patch(
			"/api/notifications/whatsapp/settings/",
			{"enabled": True, "phone_number": "+14155552671"}, format="json",
		)
		self.assertEqual(updated.status_code, status.HTTP_200_OK)
		self.assertEqual(updated.data["phone_number"], "+14155552671")
		self.assertFalse(hasattr(updated.data, "access_token"))
		self.assertEqual(self.client.patch(
			"/api/notifications/whatsapp/settings/", {"user_id": 999, "enabled": False}, format="json",
		).status_code, status.HTTP_400_BAD_REQUEST)

	def test_settings_reject_invalid_number_and_enabled_without_a_number(self):
		invalid = self.client.patch(
			"/api/notifications/whatsapp/settings/", {"enabled": False, "phone_number": "123"}, format="json",
		)
		self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
		missing = self.client.patch(
			"/api/notifications/whatsapp/settings/", {"enabled": True, "phone_number": ""}, format="json",
		)
		self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)

	def test_test_message_uses_saved_recipient_and_rejects_arbitrary_recipient(self):
		pref = UserPreference.objects.create(
			user=self.user, whatsapp_phone_number="+14155552671",
		)
		with patch("monitor.views.send_test_message", return_value={
			"accepted": True, "message_id": "wamid.mock", "delivery_status": "unconfirmed",
			"detail": "Meta accepted the message for processing; recipient delivery is not confirmed.",
		}) as send:
			response = self.client.post("/api/notifications/whatsapp/test/", {}, format="json")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		send.assert_called_once()
		self.assertEqual(send.call_args.args[0], pref.whatsapp_phone_number)
		self.assertTrue(response.data["accepted"])
		self.assertEqual(response.data["delivery_status"], "unconfirmed")
		self.assertEqual(WhatsAppDelivery.objects.get(user=self.user).status, "accepted")
		arbitrary = self.client.post(
			"/api/notifications/whatsapp/test/", {"phone_number": "+447700900123"}, format="json",
		)
		self.assertEqual(arbitrary.status_code, status.HTTP_400_BAD_REQUEST)

	def test_notification_history_contains_only_the_authenticated_users_rows(self):
		other = User.objects.create_user(username="wa-other", email="wa-other@example.com", password="Str0ng!WhatsAppOtherPass")
		WhatsAppDelivery.objects.create(user=self.user, idempotency_key="own-test", recipient_masked="+*******2671")
		WhatsAppDelivery.objects.create(user=other, idempotency_key="other-test", recipient_masked="+*******0123")
		response = self.client.get("/api/notifications/whatsapp/history/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data), 1)
		self.assertEqual(response.data[0]["recipient"], "+*******2671")


class WebsiteUrlSafetyTests(SimpleTestCase):
	def test_website_serializer_accepts_simple_dynamic_ignore_selectors(self):
		from .serializers import WebsiteSerializer

		serializer = WebsiteSerializer(data={
			"name": "Filtered site",
			"url": "https://example.com/",
			"ignore_selectors": [".countdown", "#live-ticker"],
		})
		self.assertTrue(serializer.is_valid(), serializer.errors)
		self.assertEqual(serializer.validated_data["ignore_selectors"], [".countdown", "#live-ticker"])

	def test_website_serializer_rejects_unsafe_ignore_selector_syntax(self):
		from .serializers import WebsiteSerializer

		serializer = WebsiteSerializer(data={
			"name": "Filtered site",
			"url": "https://example.com/",
			"ignore_selectors": [".price:hover"],
		})
		self.assertFalse(serializer.is_valid())
		self.assertIn("ignore_selectors", serializer.errors)

	def test_website_api_serializer_rejects_private_destinations(self):
		from .serializers import WebsiteSerializer

		serializer = WebsiteSerializer(data={
			"name": "Local target",
			"url": "http://127.0.0.1/",
		})
		self.assertFalse(serializer.is_valid())
		self.assertIn("url", serializer.errors)

	def test_rejects_local_and_private_website_destinations(self):
		for url in (
			"http://localhost/",
			"http://admin.local/",
			"http://127.0.0.1/",
			"http://169.254.169.254/latest/meta-data/",
			"http://[::1]/",
			"file:///C:/Windows/win.ini",
		):
			with self.subTest(url=url), self.assertRaises(ValueError):
				validate_public_web_url(url)

	def test_dns_resolution_rejects_a_hostname_returning_private_addresses(self):
		private_dns_answer = [
			(0, 0, 0, "", ("192.168.1.10", 443)),
		]
		with patch("monitor.url_safety.socket.getaddrinfo", return_value=private_dns_answer):
			with self.assertRaises(ValueError):
				validate_public_web_url("https://public-looking.example/", resolve=True)


class WebsiteMonitoringMetadataApiTests(APITestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="monitoring-metadata-user",
			email="monitoring-metadata@example.com",
			password="Str0ng!MetadataPass",
		)
		self.client.force_authenticate(self.user)

	def test_website_list_includes_next_due_time_from_check_interval(self):
		last_checked = timezone.now() - timedelta(minutes=3)
		website = Website.objects.create(
			name="Metadata site",
			url="https://metadata.example.com/",
			owner=self.user,
			status="monitoring",
			check_interval=10,
			last_checked=last_checked,
		)

		response = self.client.get(reverse("website-list"))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		website_data = next(item for item in response.data if item["id"] == website.pk)
		self.assertEqual(
			website_data["next_due_at"],
			last_checked + timedelta(minutes=10),
		)

	def test_stopped_website_has_no_next_due_time(self):
		Website.objects.create(
			name="Stopped metadata site",
			url="https://stopped-metadata.example.com/",
			owner=self.user,
			status="stopped",
		)

		response = self.client.get(reverse("website-list"))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIsNone(response.data[0]["next_due_at"])


class ManualWebsiteCheckApiTests(APITestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="manual-check-user",
			email="manual-check@example.com",
			password="Str0ng!ManualCheckPass",
		)
		self.client.force_authenticate(self.user)
		self.website = Website.objects.create(
			name="Manual check site",
			url="https://manual-check.example.com/",
			owner=self.user,
			status="monitoring",
		)

	@patch("monitor.views.enqueue_website_check", return_value=True)
	def test_check_now_uses_guarded_shared_dispatch_helper(self, enqueue_check):
		response = self.client.post(
			f"/api/websites/{self.website.pk}/check-now/"
		)

		self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
		self.assertTrue(response.data["queued"])
		enqueue_check.assert_called_once_with(self.website.pk)

	@patch("monitor.views.enqueue_website_check")
	def test_check_now_rejects_a_stopped_website(self, enqueue_check):
		self.website.status = "stopped"
		self.website.save(update_fields=["status"])

		response = self.client.post(f"/api/websites/{self.website.pk}/check-now/")

		self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
		enqueue_check.assert_not_called()


@override_settings(
	EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	DEFAULT_FROM_EMAIL="webwatch@example.com",
	WEBWATCH_DASHBOARD_URL="",
	WEBWATCH_EMAIL_BANNER_URL="",
)
class WebsiteNotificationTaskTests(TestCase):
	def setUp(self):
		self.website = Website.objects.create(
			name="Primary test website",
			url="https://example.com/",
			owner=User.objects.create_user(
				username="notification-user",
				email="owner@example.com",
				password="Str0ng!NotificationPass",
			),
			status="monitoring",
		)

	def run_check(self, content):
		with patch("monitor.tasks.deliver_change_notification.delay") as enqueue_notification:
			with patch("monitor.tasks.sync_playwright") as playwright_factory:
				browser = playwright_factory.return_value.__enter__.return_value.chromium.launch.return_value
				self.page = browser.new_page.return_value
				self.page.locator.return_value.inner_text.return_value = content
				result = check_website(self.website.pk)
			for call in enqueue_notification.call_args_list:
				try:
					deliver_change_notification.run(call.args[0])
				except Retry:
					pass
			return result

	def test_no_notification_when_content_is_unchanged(self):
		content = "unchanged page"
		self.website.content_hash = sha256(content.encode("utf-8")).hexdigest()
		self.website.save(update_fields=["content_hash"])

		result = self.run_check(content)

		self.assertFalse(result["changed"])
		self.assertEqual(len(mail.outbox), 0)
		self.assertEqual(WebsiteChange.objects.count(), 0)

	def test_successful_check_clears_previous_monitoring_error(self):
		content = "recovered page"
		self.website.content_hash = sha256(content.encode("utf-8")).hexdigest()
		self.website.status = "error"
		self.website.last_error = "Connection timed out while loading the website."
		self.website.save(update_fields=["content_hash", "status", "last_error"])

		result = self.run_check(content)

		self.assertFalse(result["changed"])
		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "monitoring")
		self.assertIsNone(self.website.last_error)
		self.assertEqual(len(mail.outbox), 0)

	def test_timeout_records_safe_error_without_changing_content_or_notifying(self):
		old_hash = "a" * 64
		old_last_changed = timezone.now() - timedelta(hours=1)
		self.website.content_hash = old_hash
		self.website.last_changed = old_last_changed
		self.website.status = "monitoring"
		self.website.save(update_fields=["content_hash", "last_changed", "status"])

		with patch("monitor.tasks.sync_playwright") as playwright_factory:
			page = playwright_factory.return_value.__enter__.return_value.chromium.launch.return_value.new_page.return_value
			page.goto.side_effect = PlaywrightTimeoutError("timed out ExternalPass!NeverLog")
			result = check_website(self.website.pk)

		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "error")
		self.assertEqual(
			self.website.last_error,
			"Connection timed out while loading the website.",
		)
		self.assertIsNotNone(self.website.last_checked)
		self.assertEqual(self.website.content_hash, old_hash)
		self.assertEqual(self.website.last_changed, old_last_changed)
		self.assertEqual(WebsiteChange.objects.count(), 0)
		self.assertEqual(WebsiteAlert.objects.count(), 0)
		self.assertEqual(len(mail.outbox), 0)
		self.assertNotIn("ExternalPass!NeverLog", str(result))

	def test_error_recovery_resumes_normal_change_detection_and_email(self):
		self.website.content_hash = "a" * 64
		self.website.status = "error"
		self.website.last_error = "Unable to connect to the website."
		self.website.save(update_fields=["content_hash", "status", "last_error"])

		result = self.run_check("recovered with new content")

		self.website.refresh_from_db()
		self.assertTrue(result["changed"])
		self.assertEqual(self.website.status, "monitoring")
		self.assertIsNone(self.website.last_error)
		self.assertEqual(WebsiteChange.objects.filter(website=self.website).count(), 1)
		self.assertEqual(len(mail.outbox), 1)

	def test_first_baseline_check_does_not_send_notification(self):
		self.website.check_claimed_at = timezone.now()
		self.website.save(update_fields=["check_claimed_at"])
		result = self.run_check("initial page")

		self.assertFalse(result["changed"])
		self.website.refresh_from_db()
		self.assertEqual(self.website.content_hash, sha256(b"initial page").hexdigest())
		self.assertEqual(self.website.status, "monitoring")
		self.assertIsNone(self.website.check_claimed_at)
		self.page.goto.assert_called_once_with(
			self.website.url,
			wait_until="load",
			timeout=30000,
		)
		self.page.evaluate.assert_called_once()
		self.assertEqual(len(mail.outbox), 0)
		self.assertEqual(WebsiteChange.objects.count(), 0)
		self.assertEqual(WebsiteAlert.objects.count(), 0)

	def test_old_snapshot_version_is_rebased_without_alert_or_email(self):
		self.website.snapshot_version = 1
		self.website.content_hash = "a" * 64
		self.website.last_value = "old page content"
		self.website.save(update_fields=["snapshot_version", "content_hash", "last_value"])

		result = self.run_check("new pipeline baseline")

		self.website.refresh_from_db()
		self.assertFalse(result["changed"])
		self.assertTrue(result["rebaselined"])
		self.assertEqual(self.website.snapshot_version, 2)
		self.assertEqual(WebsiteChange.objects.filter(website=self.website).count(), 0)
		self.assertEqual(len(mail.outbox), 0)

	def test_full_page_text_change_records_the_previous_and_new_value(self):
		self.run_check("Price: $10")
		changed_result = self.run_check("Price: $12")

		change = WebsiteChange.objects.get(website=self.website)
		self.assertTrue(changed_result["changed"])
		self.assertEqual(change.element_selector, "body")
		self.assertEqual(change.previous_value, "Price: $10")
		self.assertEqual(change.new_value, "Price: $12")
		self.assertEqual(WebsiteAlert.objects.filter(user=self.website.owner, change=change).count(), 1)
		self.assertFalse(self.run_check("Price: $12")["changed"])
		self.assertEqual(WebsiteAlert.objects.filter(change=change).count(), 1)
		self.assertEqual(len(mail.outbox), 1)

	def test_full_page_markup_change_is_detected_when_text_is_unchanged(self):

		with patch("monitor.tasks.deliver_change_notification.delay") as enqueue_notification:
			with patch("monitor.tasks.sync_playwright") as playwright_factory:
				page = playwright_factory.return_value.__enter__.return_value.chromium.launch.return_value.new_page.return_value
				page.locator.return_value.inner_text.return_value = "Apply"
				page.locator.return_value.evaluate_all.side_effect = [
					[{"tag": "body", "text": "Apply", "value": None, "controls": [], "attributes": [], "html": '<a href="/old">Apply</a>'}],
					[{"tag": "body", "text": "Apply", "value": None, "controls": [], "attributes": [], "html": '<a href="/new">Apply</a>'}],
				]
				check_website(self.website.pk)
				result = check_website(self.website.pk)
			for call in enqueue_notification.call_args_list:
				try:
					deliver_change_notification.run(call.args[0])
				except Retry:
					pass

		change = WebsiteChange.objects.get(website=self.website)
		self.assertTrue(result["changed"])
		self.assertEqual(change.element_selector, "body")
		self.assertEqual(change.previous_value, "Apply")
		self.assertEqual(change.new_value, "Apply")
		self.assertEqual(len(mail.outbox), 1)

	def test_selected_element_is_monitored_and_kept_after_check(self):
		self.website.element_selector = ".price"
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["element_selector", "content_hash"])

		result = self.run_check("updated price")

		self.assertTrue(result["changed"])
		self.page.locator.assert_any_call(".price")
		self.website.refresh_from_db()
		change = WebsiteChange.objects.get(website=self.website)
		self.assertEqual(self.website.element_selector, ".price")
		self.assertEqual(change.element_selector, ".price")
		self.assertEqual(len(mail.outbox), 1)

	def test_password_and_token_field_values_are_not_in_change_display_values(self):
		controls = [
			{
				"name": "password",
				"id": "password",
				"type": "password",
				"autocomplete": "current-password",
				"value": "ExternalPass!Sensitive",
			},
			{
				"name": "access_token",
				"id": "access-token",
				"type": "text",
				"autocomplete": "",
				"value": "token-secret-value",
			},
		]
		body = {
			"tag": "body",
			"text": "Account page",
			"value": None,
			"controls": controls,
			"attributes": [],
			"html": "<form></form>",
		}
		evaluated_results = iter([[body], controls])
		page = type("MockPage", (), {})()

		class MockLocator:
			def evaluate_all(self, _script):
				return next(evaluated_results)

			def inner_text(self):
				return "Account page"

		page.locator = lambda _selector: MockLocator()

		content, current_value, _legacy_hash = _extract_monitored_content(page, "body")

		self.assertIn("ExternalPass!Sensitive", content)
		self.assertNotIn("ExternalPass!Sensitive", current_value)
		self.assertNotIn("token-secret-value", current_value)

	def test_authenticated_site_alert_includes_safe_details_but_omits_page_values(self):
		self.website.requires_login = True
		self.website.save(update_fields=["requires_login"])
		change = WebsiteChange.objects.create(
			website=self.website,
			previous_hash="a" * 64,
			new_hash="b" * 64,
			previous_value="ExternalPass!Sensitive",
			new_value="token-secret-value",
		)

		send_change_notification(self.website, change)

		self.assertEqual(len(mail.outbox), 1)
		text_and_html = mail.outbox[0].body + mail.outbox[0].alternatives[0][0]
		self.assertIn(self.website.url, text_and_html)
		self.assertIn("Primary test website", text_and_html)
		self.assertNotIn("ExternalPass!Sensitive", text_and_html)
		self.assertNotIn("token-secret-value", text_and_html)

	def test_content_change_sends_notification(self):
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["content_hash"])

		result = self.run_check("updated page")

		change = WebsiteChange.objects.get(website=self.website)
		self.assertTrue(result["changed"])
		self.website.refresh_from_db()
		self.assertEqual(
			self.website.content_hash,
			sha256(b"updated page").hexdigest(),
		)
		self.assertEqual(change.new_hash, self.website.content_hash)
		self.assertEqual(WebsiteChange.objects.filter(website=self.website).count(), 1)
		self.assertEqual(len(mail.outbox), 1)

		self.assertEqual(
			mail.outbox[0].subject,
			"WebWatch Alert: Changes Detected on Primary test website",
		)
		self.assertEqual(mail.outbox[0].to, ["owner@example.com"])
		self.assertEqual(len(mail.outbox[0].alternatives), 1)
		self.assertEqual(mail.outbox[0].alternatives[0][1], "text/html")
		self.assertIn("Website Change Detected!", mail.outbox[0].body)
		self.assertIn("Primary test website", mail.outbox[0].body)
		self.assertIn(self.website.url, mail.outbox[0].body)
		self.assertIn("Current status: Monitoring", mail.outbox[0].body)
		self.assertIn("Last checked:", mail.outbox[0].body)
		self.assertIn("Check interval: 15 minutes", mail.outbox[0].body)
		self.assertIn("Change detected:", mail.outbox[0].body)
		self.assertIn("View Website", mail.outbox[0].body)
		html_message = mail.outbox[0].alternatives[0][0]
		self.assertIn('<h1 class="email-heading"', html_message)
		self.assertIn('src="cid:webwatch-email-banner"', html_message)
		self.assertIn('href="https://example.com/"', html_message)
		self.assertIn("What changed?", html_message)
		self.assertTrue(any(
			getattr(attachment, "get", lambda _key: None)("Content-ID") == "<webwatch-email-banner>"
			for attachment in mail.outbox[0].attachments
		))
		for private_or_technical_value in (
			change.previous_hash,
			change.new_hash,
			change.previous_value,
			change.new_value,
			"updated page",
			"ExternalPass!Sensitive",
			"token-secret-value",
		):
			if private_or_technical_value:
				self.assertNotIn(private_or_technical_value, mail.outbox[0].body + html_message)

		second_result = self.run_check("updated page")

		self.assertFalse(second_result["changed"])
		self.assertEqual(WebsiteChange.objects.filter(website=self.website).count(), 1)
		self.assertEqual(len(mail.outbox), 1)

	def test_real_change_queues_whatsapp_independently_of_email_delivery(self):
		UserPreference.objects.create(
			user=self.website.owner,
			whatsapp_notifications_enabled=True,
			whatsapp_phone_number="+14155552671",
		)
		self.run_check("baseline content")
		with patch("monitor.tasks.deliver_whatsapp_notification.delay") as queue_whatsapp:
			result = self.run_check("changed content")
		self.assertTrue(result["changed"])
		queue_whatsapp.assert_called_once()
		self.assertEqual(len(mail.outbox), 1)

	def test_unchanged_content_does_not_queue_whatsapp(self):
		UserPreference.objects.create(
			user=self.website.owner,
			whatsapp_notifications_enabled=True,
			whatsapp_phone_number="+14155552671",
		)
		self.run_check("same content")
		with patch("monitor.tasks.deliver_whatsapp_notification.delay") as queue_whatsapp:
			result = self.run_check("same content")
		self.assertFalse(result["changed"])
		queue_whatsapp.assert_not_called()

	def test_missing_recipient_does_not_crash_monitoring(self):
		self.website.owner.email = ""
		self.website.owner.save(update_fields=["email"])
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["content_hash"])

		result = self.run_check("updated page")

		self.assertTrue(result["changed"])
		self.assertNotIn("error", result)
		self.assertEqual(WebsiteChange.objects.count(), 1)
		self.assertEqual(len(mail.outbox), 0)

	def test_email_failure_does_not_undo_change_detection(self):
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["content_hash"])

		with patch("monitor.notifications.EmailMultiAlternatives.send", side_effect=OSError("mail unavailable")):
			with patch(
				"monitor.tasks.deliver_change_notification.retry",
				side_effect=Retry(),
			) as retry_notification:
				result = self.run_check("updated page")
		retry_notification.assert_called_once()
		self.assertEqual(retry_notification.call_args.kwargs["countdown"], 60)

		self.assertTrue(result["changed"])
		self.assertNotIn("notification_error", result)
		self.assertEqual(WebsiteChange.objects.count(), 1)
		self.assertEqual(WebsiteAlert.objects.count(), 1)
		change = WebsiteChange.objects.get(website=self.website)
		self.assertIsNone(change.notification_sent_at)
		self.assertEqual(change.notification_attempts, 1)
		self.assertIsNotNone(change.notification_next_attempt_at)
		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "monitoring")
		self.assertIsNone(self.website.last_error)
		self.assertEqual(
			self.website.content_hash,
			sha256(b"updated page").hexdigest(),
		)
		with patch(
			"monitor.tasks.timezone.now",
			return_value=change.notification_next_attempt_at + timedelta(seconds=1),
		):
			retried = deliver_change_notification.run(change.pk)
		self.assertTrue(retried["sent"])
		change.refresh_from_db()
		self.assertEqual(change.notification_attempts, 2)
		self.assertIsNotNone(change.notification_sent_at)
		self.assertEqual(len(mail.outbox), 1)

	def test_optional_website_details_render_without_a_broken_link(self):
		self.website.url = ""
		self.website.last_checked = None
		self.website.check_interval = None
		change = WebsiteChange.objects.create(
			website=self.website,
			previous_hash="a" * 64,
			new_hash="b" * 64,
		)

		send_change_notification(self.website, change)

		message = mail.outbox[-1]
		self.assertIn("Website URL: Unavailable", message.body)
		self.assertIn("Last checked: Not available", message.body)
		self.assertIn("Check interval: Not available", message.body)
		self.assertNotIn("href=\"\"", message.alternatives[0][0])

	@override_settings(WEBWATCH_EMAIL_BANNER_URL="https://assets.example.test/webwatch-banner.png")
	def test_configured_public_https_banner_url_is_used(self):
		change = WebsiteChange.objects.create(
			website=self.website,
			previous_hash="a" * 64,
			new_hash="b" * 64,
		)

		send_change_notification(self.website, change)

		html_message = mail.outbox[-1].alternatives[0][0]
		self.assertIn('src="https://assets.example.test/webwatch-banner.png"', html_message)
		self.assertFalse(any(
			getattr(attachment, "get", lambda _key: None)("Content-ID") == "<webwatch-email-banner>"
			for attachment in mail.outbox[-1].attachments
		))

	def test_disabled_email_preference_keeps_in_app_alert_and_does_not_resend_when_reenabled(self):
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["content_hash"])
		UserPreference.objects.create(user=self.website.owner, email_alerts_enabled=False)

		result = self.run_check("updated while email is disabled")
		change = WebsiteChange.objects.get(website=self.website)
		self.assertTrue(result["changed"])
		self.assertEqual(WebsiteAlert.objects.filter(user=self.website.owner, change=change).count(), 1)
		self.assertIsNotNone(change.notification_sent_at)
		self.assertEqual(len(mail.outbox), 0)

		UserPreference.objects.filter(user=self.website.owner).update(email_alerts_enabled=True)
		self.assertEqual(deliver_change_notification.run(change.pk)["skipped"], "Notification was already delivered.")
		self.assertEqual(len(mail.outbox), 0)

	def test_repeated_notification_task_does_not_send_a_duplicate(self):
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["content_hash"])
		self.run_check("updated page")
		change = WebsiteChange.objects.get(website=self.website)

		first = deliver_change_notification.run(change.pk)
		second = deliver_change_notification.run(change.pk)

		self.assertFalse(first["sent"])
		self.assertFalse(second["sent"])
		change.refresh_from_db()
		self.assertIsNotNone(change.notification_sent_at)
		self.assertEqual(len(mail.outbox), 1)

	def test_stopped_website_suppresses_pending_notification(self):
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["content_hash"])
		with patch("monitor.tasks.deliver_change_notification.delay"):
			with patch("monitor.tasks.sync_playwright") as playwright_factory:
				browser = playwright_factory.return_value.__enter__.return_value.chromium.launch.return_value
				browser.new_page.return_value.locator.return_value.inner_text.return_value = "updated page"
				check_website(self.website.pk)
		change = WebsiteChange.objects.get(website=self.website)
		self.website.status = "stopped"
		self.website.save(update_fields=["status"])

		result = deliver_change_notification.run(change.pk)

		self.assertFalse(result["sent"])
		self.assertIsNone(WebsiteChange.objects.get(pk=change.pk).notification_sent_at)
		self.assertEqual(len(mail.outbox), 0)

	def test_pending_notification_sweep_queues_only_due_owned_changes(self):
		self.website.content_hash = "a" * 64
		self.website.save(update_fields=["content_hash"])
		with patch("monitor.tasks.deliver_change_notification.delay") as enqueue_notification:
			with patch("monitor.tasks.sync_playwright") as playwright_factory:
				browser = playwright_factory.return_value.__enter__.return_value.chromium.launch.return_value
				browser.new_page.return_value.locator.return_value.inner_text.return_value = "updated page"
				check_website(self.website.pk)
			self.website.owner.email = ""
			self.website.owner.save(update_fields=["email"])
			self.assertEqual(queue_pending_change_notifications(), {"queued": 0})
			enqueue_notification.assert_called_once()


@skipUnless(
	os.environ.get("WEBWATCH_CHANGE_TEST_URL"),
	"Set WEBWATCH_CHANGE_TEST_URL to run the live Playwright change test.",
)
@override_settings(
	EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	DEFAULT_FROM_EMAIL="webwatch@example.com",
)
class LiveWebWatchChangeDetectionTests(TestCase):
	def test_successive_live_http_responses_create_one_change_and_owner_email(self):
		owner = User.objects.create_user(
			username="live-change-owner",
			email="live-owner@example.com",
			password="Str0ng!LiveChangePass",
		)
		website = Website.objects.create(
			name="Live HTTP test page",
			url=os.environ["WEBWATCH_CHANGE_TEST_URL"],
			owner=owner,
		)

		baseline_result = check_website(website.pk)
		website.refresh_from_db()
		baseline_hash = website.content_hash

		self.assertFalse(baseline_result["changed"])
		self.assertIsNotNone(baseline_hash)
		self.assertEqual(WebsiteChange.objects.filter(website=website).count(), 0)
		self.assertEqual(len(mail.outbox), 0)

		with patch("monitor.tasks.deliver_change_notification.delay") as enqueue_notification:
			changed_result = check_website(website.pk)
			for call in enqueue_notification.call_args_list:
				deliver_change_notification.run(call.args[0])
		website.refresh_from_db()
		changes = WebsiteChange.objects.filter(website=website)

		self.assertTrue(changed_result["changed"])
		self.assertNotEqual(website.content_hash, baseline_hash)
		self.assertEqual(changes.count(), 1)
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, [owner.email])


@override_settings(
	EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	DEFAULT_FROM_EMAIL="webwatch@example.com",
)
class TestEmailCommandTests(TestCase):
	def test_test_email_command_sends_to_requested_recipient(self):
		call_command("test_email", "recipient@example.com")

		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, ["recipient@example.com"])
		self.assertEqual(mail.outbox[0].subject, "WebWatch SMTP configuration test")

	@patch(
		"monitor.management.commands.test_email.send_mail",
		side_effect=OSError("SMTP rejected secret-password-value"),
	)
	def test_test_email_command_does_not_expose_smtp_exception_text(self, send_mail_mock):
		from django.core.management.base import CommandError

		with self.assertRaises(CommandError) as error:
			call_command("test_email", "recipient@example.com")

		self.assertIn("OSError", str(error.exception))
		self.assertNotIn("secret-password-value", str(error.exception))
		send_mail_mock.assert_called_once()


class ErrorWebsiteRetryTests(TestCase):
	def test_error_websites_are_retried_only_when_their_interval_is_due(self):
		owner = User.objects.create_user(
			username="retry-owner",
			email="retry@example.com",
			password="Str0ng!RetryOwner",
		)
		due_website = Website.objects.create(
			name="Due error site",
			url="https://due.example.com/",
			owner=owner,
			status="error",
			check_interval=15,
			last_checked=timezone.now() - timedelta(minutes=16),
		)
		not_due_website = Website.objects.create(
			name="Recent error site",
			url="https://recent.example.com/",
			owner=owner,
			status="error",
			check_interval=15,
			last_checked=timezone.now() - timedelta(minutes=2),
		)
		with patch("monitor.tasks.check_website.delay") as dispatch:
			check_due_websites()

		dispatch.assert_called_once_with(due_website.pk)
		self.assertNotEqual(due_website.pk, not_due_website.pk)

	def test_monitoring_website_without_previous_check_is_queued(self):
		owner = User.objects.create_user(
			username="first-check-owner",
			email="first-check@example.com",
			password="Str0ng!FirstCheckPass",
		)
		website = Website.objects.create(
			name="First check site",
			url="https://first-check.example.com/",
			owner=owner,
			status="monitoring",
			last_checked=None,
		)

		with patch("monitor.tasks.check_website.delay") as dispatch:
			result = check_due_websites()

		dispatch.assert_called_once_with(website.pk)
		self.assertEqual(result, {"queued": 1})

	def test_due_monitoring_website_is_queued_and_claim_prevents_duplicate_dispatch(self):
		owner = User.objects.create_user(
			username="due-monitoring-owner",
			email="due-monitoring@example.com",
			password="Str0ng!DueMonitoringPass",
		)
		website = Website.objects.create(
			name="Due monitoring site",
			url="https://due-monitoring.example.com/",
			owner=owner,
			status="monitoring",
			check_interval=5,
			last_checked=timezone.now() - timedelta(minutes=6),
		)
		with patch("monitor.tasks.check_website.delay") as dispatch:
			first_result = check_due_websites()
			second_result = check_due_websites()

		dispatch.assert_called_once_with(website.pk)
		self.assertEqual(first_result, {"queued": 1})
		self.assertEqual(second_result, {"queued": 0})
		self.assertIsNotNone(Website.objects.get(pk=website.pk).check_claimed_at)

	def test_scheduler_respects_each_website_interval_and_filters_status(self):
		owner = User.objects.create_user(
			username="interval-owner",
			email="interval@example.com",
			password="Str0ng!IntervalPass",
		)
		due_website = Website.objects.create(
			name="Due interval site",
			url="https://due-interval.example.com/",
			owner=owner,
			status="monitoring",
			check_interval=10,
			last_checked=timezone.now() - timedelta(minutes=11),
		)
		not_due_website = Website.objects.create(
			name="Long interval site",
			url="https://long-interval.example.com/",
			owner=owner,
			status="monitoring",
			check_interval=30,
			last_checked=timezone.now() - timedelta(minutes=11),
		)
		stopped_website = Website.objects.create(
			name="Stopped site",
			url="https://stopped.example.com/",
			owner=owner,
			status="stopped",
			last_checked=timezone.now() - timedelta(hours=1),
		)
		login_required_website = Website.objects.create(
			name="Login required site",
			url="https://login-required.example.com/",
			owner=owner,
			status="login_required",
			last_checked=timezone.now() - timedelta(hours=1),
		)

		with patch("monitor.tasks.check_website.delay") as dispatch:
			result = check_due_websites()

		dispatch.assert_called_once_with(due_website.pk)
		self.assertEqual(result, {"queued": 1})
		self.assertIsNone(Website.objects.get(pk=not_due_website.pk).check_claimed_at)
		self.assertIsNone(Website.objects.get(pk=stopped_website.pk).check_claimed_at)
		self.assertIsNone(Website.objects.get(pk=login_required_website.pk).check_claimed_at)

	def test_stale_dispatch_claim_is_recovered(self):
		owner = User.objects.create_user(
			username="stale-claim-owner",
			email="stale-claim@example.com",
			password="Str0ng!StaleClaimPass",
		)
		website = Website.objects.create(
			name="Stale claim site",
			url="https://stale-claim.example.com/",
			owner=owner,
			status="error",
			last_checked=timezone.now() - timedelta(minutes=20),
			check_claimed_at=timezone.now() - timedelta(minutes=16),
		)

		with patch("monitor.tasks.check_website.delay") as dispatch:
			result = check_due_websites()

		dispatch.assert_called_once_with(website.pk)
		self.assertEqual(result, {"queued": 1})

	def test_broker_publish_failure_releases_dispatch_claim(self):
		owner = User.objects.create_user(
			username="publish-failure-owner",
			email="publish-failure@example.com",
			password="Str0ng!PublishFailurePass",
		)
		website = Website.objects.create(
			name="Publish failure site",
			url="https://publish-failure.example.com/",
			owner=owner,
			status="monitoring",
		)

		with patch(
			"monitor.tasks.check_website.delay",
			side_effect=ConnectionError("broker unavailable"),
		):
			result = check_due_websites()

		self.assertEqual(result, {"queued": 0})
		self.assertIsNone(Website.objects.get(pk=website.pk).check_claimed_at)


class AccountDeletionApiTests(APITestCase):
	def setUp(self):
		cache.clear()
		self.user = User.objects.create_user(
			username="delete-me", email="delete-me@example.com", password="Str0ng!DeletePass", is_staff=True,
		)
		self.other_user = User.objects.create_user(
			username="keep-me", email="keep-me@example.com", password="Str0ng!KeepPass",
		)
		self.website = Website.objects.create(
			name="Private site", url="https://private.example.com/", owner=self.user, status="monitoring", requires_login=True,
		)
		self.other_website = Website.objects.create(
			name="Other user's site", url="https://other.example.com/", owner=self.other_user,
		)
		self.credential = WebsiteCredential.objects.create(
			website=self.website,
			username_encrypted="encrypted-user",
			password_encrypted="encrypted-password",
			login_url="https://private.example.com/login",
			session_state_encrypted="encrypted-session",
		)
		self.change = WebsiteChange.objects.create(website=self.website, new_hash="a" * 64)
		self.alert = WebsiteAlert.objects.create(user=self.user, change=self.change)
		self.preference = UserPreference.objects.create(user=self.user, theme="dark")
		self.activity = AccountActivity.objects.create(
			user=self.user, event_type="account_created", description="Account created",
		)
		self.identity = GoogleIdentity.objects.create(user=self.user, subject="google-subject-for-delete")
		self.token, _ = Token.objects.get_or_create(user=self.user)
		self.audit = DeveloperAuditLog.objects.create(
			actor=self.user, action="update", model_key="websites", record_id=str(self.website.pk), changed_fields=["name"],
		)
		self.url = reverse("auth-delete-account")

	def test_anonymous_delete_is_rejected(self):
		response = self.client.delete(self.url, {"confirmation": "DELETE"}, format="json")
		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
		self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

	def test_get_is_not_a_supported_deletion_method(self):
		self.client.force_authenticate(self.user)
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
		self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

	def test_missing_or_invalid_confirmation_does_not_delete_anything(self):
		self.client.force_authenticate(self.user)
		for data in ({}, {"confirmation": "delete"}, {"confirmation": "DELETE", "user_id": self.other_user.pk}):
			with self.subTest(data=data):
				response = self.client.delete(self.url, data, format="json")
				self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
				self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
				self.assertTrue(User.objects.filter(pk=self.other_user.pk).exists())
		self.assertFalse(DeletedAccountMarker.objects.exists())

	def test_confirmed_delete_cascades_only_authenticated_users_data(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
		response = self.client.delete(self.url, {"confirmation": "DELETE"}, format="json")

		self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
		self.assertFalse(Token.objects.filter(user_id=self.user.pk).exists())
		self.assertFalse(Website.objects.filter(pk=self.website.pk).exists())
		self.assertFalse(WebsiteCredential.objects.filter(pk=self.credential.pk).exists())
		self.assertFalse(WebsiteChange.objects.filter(pk=self.change.pk).exists())
		self.assertFalse(WebsiteAlert.objects.filter(pk=self.alert.pk).exists())
		self.assertFalse(UserPreference.objects.filter(pk=self.preference.pk).exists())
		self.assertFalse(AccountActivity.objects.filter(pk=self.activity.pk).exists())
		self.assertFalse(GoogleIdentity.objects.filter(pk=self.identity.pk).exists())
		self.assertTrue(Website.objects.filter(pk=self.other_website.pk, owner=self.other_user).exists())
		self.assertTrue(User.objects.filter(pk=self.other_user.pk).exists())
		self.assertTrue(DeletedAccountMarker.objects.filter(user_id=self.user.pk).exists())
		self.audit.refresh_from_db()
		self.assertIsNone(self.audit.actor_id)

	def test_stale_monitoring_and_notification_tasks_are_noops_after_deletion(self):
		website_id = self.website.pk
		change_id = self.change.pk
		self.client.force_authenticate(self.user)
		response = self.client.delete(self.url, {"confirmation": "DELETE"}, format="json")
		self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

		with patch("monitor.tasks.sync_playwright") as playwright_factory:
			monitor_result = check_website.run(website_id)
		playwright_factory.assert_not_called()
		notification_result = deliver_change_notification.run(change_id)
		self.assertFalse(monitor_result["changed"])
		self.assertEqual(notification_result["sent"], False)
		self.assertEqual(len(mail.outbox), 0)


class AuthenticationApiTests(APITestCase):
	def test_registration_creates_hashed_password_and_token(self):
		response = self.client.post(
			reverse("auth-register"),
			{
				"name": "Sam Example",
				"email": "Sam@example.com",
				"password": "Str0ng!RegistrationPass",
				"confirm_password": "Str0ng!RegistrationPass",
			},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		user = User.objects.get(email="sam@example.com")
		self.assertTrue(check_password("Str0ng!RegistrationPass", user.password))
		self.assertNotIn("password", response.data)
		self.assertNotIn("confirm_password", response.data)
		self.assertEqual(response.data["user"]["name"], "Sam Example")

	def test_registration_rejects_duplicate_email_case_insensitively(self):
		User.objects.create_user(
			username="existing-user",
			email="taken@example.com",
			password="Str0ng!ExistingPass",
		)

		response = self.client.post(
			reverse("auth-register"),
			{
				"name": "Another User",
				"email": "TAKEN@example.com",
				"password": "Str0ng!RegistrationPass",
				"confirm_password": "Str0ng!RegistrationPass",
			},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(
			response.data["email"],
			["An account with this email already exists. Please log in."],
		)

	def test_registration_handles_uniqueness_race_without_server_error(self):
		User.objects.create_user(
			username="racing-registration",
			email="race-user@example.com",
			password="Str0ng!ExistingPass",
		)
		original_filter = User.objects.filter
		validation_queries = 0

		def hide_concurrent_account_until_after_validation(*args, **kwargs):
			nonlocal validation_queries
			if kwargs.get("email__iexact") == "race-user@example.com":
				validation_queries += 1
				if validation_queries == 1:
					return User.objects.none()
			return original_filter(*args, **kwargs)

		with (
			patch.object(
				User.objects,
				"filter",
				side_effect=hide_concurrent_account_until_after_validation,
			),
			patch(
				"monitor.auth_views.RegistrationSerializer.save",
				side_effect=IntegrityError,
			) as save_user,
		):
			response = self.client.post(
				reverse("auth-register"),
				{
					"name": "Race User",
					"email": "race-user@example.com",
					"password": "Str0ng!RegistrationPass",
					"confirm_password": "Str0ng!RegistrationPass",
				},
				format="json",
			)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(
			response.data["email"],
			["An account with this email already exists. Please log in."],
		)
		self.assertEqual(validation_queries, 2)
		save_user.assert_called_once()

	def test_registration_sanitizes_email_when_generating_username(self):
		response = self.client.post(
			reverse("auth-register"),
			{
				"name": "Plus Address",
				"email": "plus+tag@example.com",
				"password": "Str0ng!RegistrationPass",
				"confirm_password": "Str0ng!RegistrationPass",
			},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(User.objects.get(email="plus+tag@example.com").username, "plus-tag")

	def test_login_and_me_return_safe_user_data(self):
		user = User.objects.create_user(
			username="login-user",
			email="login@example.com",
			password="Str0ng!LoginPass",
			first_name="Login User",
		)

		response = self.client.post(
			reverse("auth-login"),
			{"email": "LOGIN@example.com", "password": "Str0ng!LoginPass"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
		me_response = self.client.get(reverse("auth-me"))
		self.assertEqual(me_response.status_code, status.HTTP_200_OK)
		self.assertEqual(me_response.data, {
			"id": user.pk,
			"email": user.email,
			"name": "Login User",
			"google_linked": False,
		})
		self.assertNotIn("password", me_response.data)

	def test_invalid_password_is_rejected(self):
		User.objects.create_user(
			username="wrong-password-user",
			email="wrong@example.com",
			password="Str0ng!CorrectPass",
		)
		response = self.client.post(
			reverse("auth-login"),
			{"email": "wrong@example.com", "password": "incorrect"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertNotIn("token", response.data)

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token")
	def test_google_login_requires_credential_field(self, verify_token):
		response = self.client.post(
			reverse("auth-google"),
			{"id_token": "must-not-be-treated-as-credential"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data["detail"], "A Google credential is required.")
		verify_token.assert_not_called()

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token")
	def test_google_login_rejects_unverified_email(self, verify_token):
		verify_token.return_value = {
			"email": "unverified@example.com",
			"email_verified": False,
			"name": "Unverified User",
		}

		response = self.client.post(
			reverse("auth-google"),
			{"credential": "google-token-with-unverified-email"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data["code"], "google_email_unverified")
		self.assertFalse(User.objects.exists())

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token", side_effect=ValueError("invalid signature token-secret"))
	def test_google_login_reports_invalid_token_without_logging_credential(self, verify_token):
		credential = "sensitive-id-token-value"
		with self.assertLogs("monitor.auth_views", level="WARNING") as captured_logs:
			response = self.client.post(
				reverse("auth-google"),
				{"credential": credential},
				format="json",
			)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data["code"], "google_token_invalid")
		self.assertEqual(response.data["diagnostic"], "signature_verification_failed")
		self.assertIn("category=invalid_token", captured_logs.output[0])
		self.assertIn("reason=signature_verification_failed", captured_logs.output[0])
		self.assertNotIn(credential, "\n".join(captured_logs.output))
		self.assertNotIn("token-secret", "\n".join(captured_logs.output))
		self.assertNotIn("token-secret", str(response.data))

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token", side_effect=ValueError("Wrong recipient client-secret-value"))
	def test_google_login_reports_audience_mismatch_as_configuration_error(self, verify_token):
		credential = "sensitive-audience-token"
		with self.assertLogs("monitor.auth_views", level="WARNING") as captured_logs:
			response = self.client.post(
				reverse("auth-google"),
				{"credential": credential},
				format="json",
			)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data["code"], "google_audience_mismatch")
		self.assertEqual(response.data["diagnostic"], "audience_mismatch")
		self.assertIn("category=audience_mismatch", captured_logs.output[0])
		self.assertIn("reason=audience_mismatch", captured_logs.output[0])
		self.assertNotIn(credential, "\n".join(captured_logs.output))
		self.assertNotIn("client-secret-value", "\n".join(captured_logs.output))

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch(
		"monitor.auth_views.id_token.verify_oauth2_token",
		side_effect=ValueError("Token expired at 2026-10-18T00:00:00Z"),
	)
	def test_google_login_reports_expired_token_safely(self, verify_token):
		credential = "sensitive-expired-token"
		with self.assertLogs("monitor.auth_views", level="WARNING") as captured_logs:
			response = self.client.post(
				reverse("auth-google"),
				{"credential": credential},
				format="json",
			)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data["diagnostic"], "token_expired")
		self.assertIn("reason=token_expired", captured_logs.output[0])
		self.assertNotIn(credential, "\n".join(captured_logs.output))
		self.assertNotIn("2026-10-18", "\n".join(captured_logs.output))

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token", side_effect=TransportError("network diagnostic with secret"))
	def test_google_login_reports_verifier_transport_failure_safely(self, verify_token):
		with self.assertLogs("monitor.auth_views", level="WARNING") as captured_logs:
			response = self.client.post(
				reverse("auth-google"),
				{"credential": "sensitive-transport-token"},
				format="json",
			)

		self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
		self.assertEqual(response.data["code"], "google_verifier_unavailable")
		self.assertIn("category=verifier_unavailable", captured_logs.output[0])
		self.assertNotIn("network diagnostic with secret", "\n".join(captured_logs.output))

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch(
		"monitor.auth_views.id_token.verify_oauth2_token",
		side_effect=GoogleAuthError("issuer rejected; diagnostic secret"),
	)
	def test_google_login_reports_invalid_issuer_without_exception_details(self, verify_token):
		credential = "sensitive-issuer-token"
		with self.assertLogs("monitor.auth_views", level="WARNING") as captured_logs:
			response = self.client.post(
				reverse("auth-google"),
				{"credential": credential},
				format="json",
			)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(response.data["code"], "google_issuer_invalid")
		self.assertIn("category=issuer_rejected", captured_logs.output[0])
		self.assertIn("exception_type=GoogleAuthError", captured_logs.output[0])
		self.assertNotIn(credential, "\n".join(captured_logs.output))
		self.assertNotIn("diagnostic secret", "\n".join(captured_logs.output))

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token")
	def test_google_login_rejects_collision_with_password_based_account(self, verify_token):
		user = User.objects.create_user(
			username="google-existing",
			email="google@example.com",
			password="Str0ng!GooglePass",
		)
		verify_token.return_value = {
			"email": "GOOGLE@example.com",
			"email_verified": True,
			"name": "Google User",
		}

		response = self.client.post(
			reverse("auth-google"),
			{"credential": "verified-google-id-token"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
		self.assertEqual(User.objects.count(), 1)
		self.assertNotIn("token", response.data)
		self.assertEqual(User.objects.get(pk=user.pk).email, "google@example.com")
		verify_token.assert_called_once()

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token")
	def test_google_login_reuses_user_from_verified_claim_not_submitted_email(self, verify_token):
		verify_token.return_value = {
			"email": "verified-google@example.com",
			"email_verified": True,
			"name": "Verified Google User",
		}
		url = reverse("auth-google")

		first_response = self.client.post(
			url,
			{
				"credential": "verified-google-id-token",
				"email": "untrusted-frontend@example.com",
			},
			format="json",
		)
		second_response = self.client.post(
			url,
			{"credential": "verified-google-id-token"},
			format="json",
		)

		user = User.objects.get(email="verified-google@example.com")
		self.assertEqual(first_response.status_code, status.HTTP_200_OK)
		self.assertEqual(second_response.status_code, status.HTTP_200_OK)
		self.assertEqual(User.objects.count(), 1)
		self.assertEqual(first_response.data["user"]["email"], user.email)
		self.assertEqual(second_response.data["user"]["id"], user.pk)
		verify_token.assert_called()

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token")
	def test_google_login_creates_account_from_verified_identity(self, verify_token):
		verify_token.return_value = {
			"email": "new-google@example.com",
			"email_verified": True,
			"name": "New Google User",
		}
		response = self.client.post(
			reverse("auth-google"),
			{"credential": "verified-google-id-token"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		user = User.objects.get(email="new-google@example.com")
		self.assertFalse(user.has_usable_password())
		self.assertEqual(response.data["user"]["id"], user.pk)
		self.assertTrue(response.data["token"])
		self.assertEqual(Token.objects.get(user=user).key, response.data["token"])
		self.assertEqual(verify_token.call_args.args[2], "test-google-client-id")
		self.assertEqual(verify_token.call_args.args[0], "verified-google-id-token")
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
		me_response = self.client.get(reverse("auth-me"))
		self.assertEqual(me_response.status_code, status.HTTP_200_OK)
		self.assertEqual(me_response.data["email"], "new-google@example.com")


class DeveloperApiTests(APITestCase):
	def setUp(self):
		self.staff = User.objects.create_user(
			username="developer-staff",
			email="developer@example.com",
			password="Str0ng!DeveloperPass",
			is_staff=True,
		)
		self.regular = User.objects.create_user(
			username="developer-regular",
			email="regular@example.com",
			password="Str0ng!RegularPass",
		)
		self.website = Website.objects.create(
			name="Inspectable website",
			url="https://inspectable.example.com/",
			owner=self.regular,
			status="stopped",
		)

	def as_staff(self):
		self.client.force_authenticate(self.staff)

	def test_anonymous_and_regular_users_are_denied(self):
		anonymous_response = self.client.get(reverse("developer-overview"))
		self.client.force_authenticate(self.regular)
		regular_response = self.client.get(reverse("developer-overview"))

		self.assertEqual(anonymous_response.status_code, status.HTTP_401_UNAUTHORIZED)
		self.assertEqual(regular_response.status_code, status.HTTP_403_FORBIDDEN)

	def test_staff_can_read_real_overview_and_allowlisted_models(self):
		self.as_staff()

		overview = self.client.get(reverse("developer-overview"))
		tables = self.client.get(reverse("developer-tables"))

		self.assertEqual(overview.status_code, status.HTTP_200_OK)
		self.assertEqual(overview.data["counts"]["websites"], 1)
		self.assertTrue(overview.data["database"]["healthy"])
		self.assertFalse(overview.data["recent_errors_available"])
		self.assertEqual(tables.status_code, status.HTTP_200_OK)
		self.assertIn("websites", [table["key"] for table in tables.data["tables"]])

	def test_unsupported_model_is_not_accessible(self):
		self.as_staff()

		response = self.client.get(reverse(
			"developer-records",
			kwargs={"model_key": "auth-token"},
		))

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

	def test_record_browser_search_filter_sort_and_page_limits_are_allowlisted(self):
		Website.objects.create(
			name="Second monitored site",
			url="https://second.example.com/",
			owner=self.regular,
			status="monitoring",
		)
		self.as_staff()

		filtered = self.client.get(reverse(
			"developer-records",
			kwargs={"model_key": "websites"},
		), {"q": "inspectable", "filter": "status", "value": "stopped", "sort": "name", "direction": "asc"})
		invalid_filter = self.client.get(reverse(
			"developer-records",
			kwargs={"model_key": "websites"},
		), {"filter": "owner", "value": "1"})
		oversized_page = self.client.get(reverse(
			"developer-records",
			kwargs={"model_key": "websites"},
		), {"page_size": "101"})

		self.assertEqual(filtered.status_code, status.HTTP_200_OK)
		self.assertEqual(filtered.data["count"], 1)
		self.assertEqual(filtered.data["results"][0]["id"], self.website.pk)
		self.assertEqual(invalid_filter.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(oversized_page.status_code, status.HTTP_400_BAD_REQUEST)

	def test_explorer_omits_sensitive_user_and_website_fields(self):
		GoogleIdentity.objects.create(user=self.regular, subject="private-google-subject")
		self.as_staff()

		website_response = self.client.get(reverse(
			"developer-record-detail",
			kwargs={"model_key": "websites", "record_id": self.website.pk},
		))
		user_response = self.client.get(reverse(
			"developer-record-detail",
			kwargs={"model_key": "users", "record_id": self.regular.pk},
		))
		identity_response = self.client.get(reverse(
			"developer-record-detail",
			kwargs={"model_key": "google-identities", "record_id": self.regular.google_identity.pk},
		))

		self.assertEqual(website_response.status_code, status.HTTP_200_OK)
		self.assertNotIn("last_value", website_response.data["record"])
		self.assertNotIn("content_hash", website_response.data["record"])
		self.assertEqual(user_response.status_code, status.HTTP_200_OK)
		self.assertNotIn("password", user_response.data["record"])
		self.assertNotIn("is_superuser", user_response.data["record"])
		self.assertEqual(identity_response.status_code, status.HTTP_200_OK)
		self.assertNotIn("subject", identity_response.data["record"])

	def test_invalid_and_protected_edits_are_rejected(self):
		self.as_staff()
		website_url = reverse(
			"developer-record-detail",
			kwargs={"model_key": "websites", "record_id": self.website.pk},
		)

		invalid_value = self.client.patch(website_url, {"status": "not-a-status"}, format="json")
		protected_field = self.client.patch(website_url, {"owner": self.staff.pk}, format="json")

		self.assertEqual(invalid_value.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(protected_field.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(DeveloperAuditLog.objects.count(), 0)

	def test_successful_edit_audits_field_names_without_values(self):
		self.as_staff()
		url = reverse(
			"developer-record-detail",
			kwargs={"model_key": "websites", "record_id": self.website.pk},
		)

		response = self.client.patch(url, {"name": "Repaired name"}, format="json")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["record"]["name"], "Repaired name")
		audit = DeveloperAuditLog.objects.get()
		self.assertEqual(audit.actor, self.staff)
		self.assertEqual(audit.action, "update")
		self.assertEqual(audit.changed_fields, ["name"])
		self.assertNotIn("Repaired name", str(audit.changed_fields))

	def test_user_records_cannot_be_deleted_in_developer_explorer(self):
		self.as_staff()
		url = reverse(
			"developer-record-detail",
			kwargs={"model_key": "users", "record_id": self.staff.pk},
		)

		response = self.client.delete(url, {"confirm": True, "record_id": str(self.staff.pk)}, format="json")

		self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
		self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())

	def test_website_delete_requires_exact_confirmation_and_audits_safe_cascade(self):
		change = WebsiteChange.objects.create(website=self.website, new_hash="b" * 64)
		alert = WebsiteAlert.objects.create(user=self.regular, change=change)
		credential = WebsiteCredential.objects.create(
			website=self.website,
			username_encrypted="encrypted-user",
			password_encrypted="encrypted-pass",
			login_url="https://inspectable.example.com/login",
		)
		self.as_staff()
		url = reverse(
			"developer-record-detail",
			kwargs={"model_key": "websites", "record_id": self.website.pk},
		)
		detail = self.client.get(url)
		unconfirmed = self.client.delete(url, {"confirm": True, "record_id": "wrong-id"}, format="json")
		missing_backup_confirmation = self.client.delete(
			url,
			{"confirm": True, "record_id": str(self.website.pk)},
			format="json",
		)

		self.assertIn("encrypted website login credentials", " ".join(detail.data["deletion"]["consequences"]))
		self.assertEqual(unconfirmed.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(missing_backup_confirmation.status_code, status.HTTP_400_BAD_REQUEST)
		response = self.client.delete(
			url,
			{"confirm": True, "backup_confirmed": True, "record_id": str(self.website.pk)},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertFalse(Website.objects.filter(pk=self.website.pk).exists())
		self.assertFalse(WebsiteCredential.objects.filter(pk=credential.pk).exists())
		self.assertFalse(WebsiteChange.objects.filter(pk=change.pk).exists())
		self.assertFalse(WebsiteAlert.objects.filter(pk=alert.pk).exists())
		audit = DeveloperAuditLog.objects.get(action="delete")
		self.assertEqual(audit.model_key, "websites")
		self.assertEqual(audit.record_id, str(self.website.pk))

	def test_staff_can_view_audit_log_with_actor_and_changed_field_names(self):
		DeveloperAuditLog.objects.create(
			actor=self.staff,
			action="update",
			model_key="websites",
			record_id=str(self.website.pk),
			changed_fields=["name"],
		)
		self.as_staff()

		response = self.client.get(reverse("developer-audit-logs"))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["results"][0]["actor_email"], self.staff.email)
		self.assertEqual(response.data["results"][0]["changed_fields"], ["name"])


@override_settings(
	EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	DEFAULT_FROM_EMAIL="webwatch@example.com",
)
class AuthenticatedOwnerNotificationFlowTests(APITestCase):
	def run_changed_website(self, owner, name, url, content):
		website = Website.objects.create(
			name=name,
			url=url,
			owner=owner,
			content_hash="a" * 64,
			status="monitoring",
		)
		with patch("monitor.tasks.deliver_change_notification.delay") as enqueue_notification:
			with patch("monitor.tasks.sync_playwright") as playwright_factory:
				browser = playwright_factory.return_value.__enter__.return_value.chromium.launch.return_value
				browser.new_page.return_value.locator.return_value.inner_text.return_value = content
				result = check_website(website.pk)
			for call in enqueue_notification.call_args_list:
				try:
					deliver_change_notification.run(call.args[0])
				except Retry:
					pass
		return website, result

	def test_registered_user_receives_change_notification_at_registered_email(self):
		response = self.client.post(
			reverse("auth-register"),
			{
				"name": "Registered Owner",
				"email": "registered-owner@example.com",
				"password": "Str0ng!RegistrationPass",
				"confirm_password": "Str0ng!RegistrationPass",
			},
			format="json",
		)
		owner = User.objects.get(pk=response.data["user"]["id"])

		website, result = self.run_changed_website(
			owner,
			"Registered owner site",
			"https://registered.example.com/",
			"updated registered content",
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(owner.email, "registered-owner@example.com")
		self.assertTrue(result["changed"])
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, [owner.email])
		self.assertEqual(website.owner_id, owner.pk)

	@override_settings(GOOGLE_CLIENT_ID="test-google-client-id")
	@patch("monitor.auth_views.id_token.verify_oauth2_token")
	def test_google_user_receives_notification_at_verified_google_email(self, verify_token):
		verify_token.return_value = {
			"email": "verified-owner@example.com",
			"email_verified": True,
			"name": "Google Owner",
		}
		response = self.client.post(
			reverse("auth-google"),
			{
				"credential": "mock-google-id-token",
				"email": "untrusted-frontend@example.com",
			},
			format="json",
		)
		owner = User.objects.get(pk=response.data["user"]["id"])

		website, result = self.run_changed_website(
			owner,
			"Google owner site",
			"https://google-owner.example.com/",
			"updated Google-owner content",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(owner.email, "verified-owner@example.com")
		self.assertTrue(result["changed"])
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, [owner.email])
		self.assertEqual(website.owner_id, owner.pk)
		verify_token.assert_called_once()

	def test_each_changed_website_notifies_only_its_owners_email(self):
		first_owner = User.objects.create_user(
			username="first-notification-owner",
			email="kalaivendhan1714@gmail.com",
			password="Str0ng!FirstOwnerPass",
		)
		second_owner = User.objects.create_user(
			username="second-notification-owner",
			email="sridharun35@gmail.com",
			password="Str0ng!SecondOwnerPass",
		)

		first_website, first_result = self.run_changed_website(
			first_owner,
			"First owner's site",
			"https://first-owner.example.com/",
			"first owner's changed content",
		)
		second_website, second_result = self.run_changed_website(
			second_owner,
			"Second owner's site",
			"https://second-owner.example.com/",
			"second owner's changed content",
		)

		self.assertTrue(first_result["changed"])
		self.assertTrue(second_result["changed"])
		self.assertEqual(first_website.owner_id, first_owner.pk)
		self.assertEqual(second_website.owner_id, second_owner.pk)
		self.assertEqual(
			[message.to for message in mail.outbox],
			[[first_owner.email], [second_owner.email]],
		)
		self.assertEqual(mail.outbox[0].from_email, "webwatch@example.com")
		self.assertNotIn(second_owner.email, mail.outbox[0].to)
		self.assertNotIn(first_owner.email, mail.outbox[1].to)

	def test_logout_revokes_token(self):
		user = User.objects.create_user(
			username="logout-user",
			email="logout@example.com",
			password="Str0ng!LogoutPass",
		)
		self.client.force_authenticate(user)
		login_response = self.client.post(
			reverse("auth-login"),
			{"email": user.email, "password": "Str0ng!LogoutPass"},
			format="json",
		)
		self.client.force_authenticate(user=None)
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {login_response.data['token']}")

		logout_response = self.client.post(reverse("auth-logout"))

		self.assertEqual(logout_response.status_code, status.HTTP_204_NO_CONTENT)
		self.assertEqual(self.client.get(reverse("auth-me")).status_code, status.HTTP_401_UNAUTHORIZED)


class WhatsAppChangeTaskTests(TestCase):
	def setUp(self):
		self.owner = User.objects.create_user(
			username="wa-change-owner", email="wa-owner@example.com", password="Str0ng!WAOwnerPass",
		)
		self.other = User.objects.create_user(
			username="wa-change-other", email="wa-other@example.com", password="Str0ng!WAOtherPass",
		)
		self.website = Website.objects.create(
			name="Owner site", url="https://example.com/", owner=self.owner, status="monitoring",
		)
		self.change = WebsiteChange.objects.create(
			website=self.website, previous_hash="a" * 64, new_hash="b" * 64,
			previous_value="old", new_value="new",
		)
		UserPreference.objects.create(
			user=self.owner, whatsapp_notifications_enabled=True, whatsapp_phone_number="+14155552671",
		)
		UserPreference.objects.create(
			user=self.other, whatsapp_notifications_enabled=True, whatsapp_phone_number="+447700900123",
		)

	def test_change_is_sent_once_to_the_website_owner(self):
		with patch("monitor.tasks.send_message", return_value={"accepted": True, "message_id": "wamid.once"}) as send:
			first = deliver_whatsapp_notification.run(self.change.pk)
			second = deliver_whatsapp_notification.run(self.change.pk)
		self.assertTrue(first["accepted"])
		self.assertIn("skipped", second)
		send.assert_called_once()
		self.assertEqual(send.call_args.args[0], "+14155552671")
		delivery = WhatsAppDelivery.objects.get(website_change=self.change)
		self.assertEqual(delivery.user, self.owner)
		self.assertEqual(delivery.recipient_masked, "+*******2671")
		self.assertEqual(delivery.status, "accepted")
		self.assertEqual(delivery.provider_message_id, "wamid.once")
		self.assertIsNotNone(delivery.accepted_at)

	def test_disabled_preference_does_not_send(self):
		UserPreference.objects.filter(user=self.owner).update(whatsapp_notifications_enabled=False)
		with patch("monitor.tasks.send_message") as send:
			result = deliver_whatsapp_notification.run(self.change.pk)
		self.assertFalse(result.get("accepted", False))
		send.assert_not_called()
		self.assertFalse(WhatsAppDelivery.objects.filter(website_change=self.change).exists())

	def test_failed_whatsapp_delivery_is_recorded_without_raising_into_email_flow(self):
		with patch("monitor.tasks.send_message", return_value={
			"accepted": False, "category": "access_token_invalid", "detail": "Meta rejected the access token.", "retryable": False,
		}) as send:
			result = deliver_whatsapp_notification.run(self.change.pk)
		self.assertFalse(result.get("accepted", False))
		send.assert_called_once()
		delivery = WhatsAppDelivery.objects.get(website_change=self.change)
		self.assertEqual(delivery.status, "failed")
		self.assertEqual(delivery.error_category, "access_token_invalid")
		self.assertNotIn("Bearer", delivery.error_detail)

	def test_transient_whatsapp_error_is_retried_with_a_queued_deduplicated_record(self):
		with patch("monitor.tasks.send_message", return_value={
			"accepted": False, "category": "rate_limited", "detail": "Meta temporarily rate-limited the request.", "retryable": True,
		}):
			with patch("monitor.tasks.deliver_whatsapp_notification.retry", side_effect=Retry()) as retry_task:
				with self.assertRaises(Retry):
					deliver_whatsapp_notification.run(self.change.pk)
		self.assertEqual(retry_task.call_args.kwargs["countdown"], 30)
		delivery = WhatsAppDelivery.objects.get(website_change=self.change)
		self.assertEqual(delivery.status, "queued")
		self.assertEqual(delivery.attempts, 1)

	def test_pending_whatsapp_sweep_requeues_due_owner_delivery(self):
		delivery = WhatsAppDelivery.objects.create(
			user=self.owner,
			website=self.website,
			website_change=self.change,
			idempotency_key="whatsapp:change:sweep-test",
			status="queued",
			next_attempt_at=timezone.now() - timedelta(seconds=1),
		)
		with patch("monitor.tasks.deliver_whatsapp_notification.delay") as enqueue:
			result = queue_pending_whatsapp_notifications()
		self.assertEqual(result["queued"], 1)
		enqueue.assert_called_once_with(self.change.pk)


class WebsiteOwnershipApiTests(APITestCase):
	def setUp(self):
		self.owner = User.objects.create_user(
			username="owner-user",
			email="owner@example.com",
			password="Str0ng!OwnerPass",
		)
		self.other_user = User.objects.create_user(
			username="other-user",
			email="other@example.com",
			password="Str0ng!OtherPass",
		)
		self.website = Website.objects.create(
			name="Owned website",
			url="https://owned.example.com/",
			owner=self.owner,
			status="monitoring",
		)
		self.other_website = Website.objects.create(
			name="Private website",
			url="https://private.example.com/",
			owner=self.other_user,
		)

	def test_unauthenticated_website_access_is_rejected(self):
		response = self.client.get(reverse("website-list"))

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

	def test_authenticated_list_contains_only_the_users_websites(self):
		self.client.force_authenticate(self.owner)

		response = self.client.get(reverse("website-list"))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual([item["id"] for item in response.data], [self.website.pk])

	def test_owner_can_see_only_safe_last_error_from_website_api(self):
		self.website.status = "error"
		self.website.last_error = "Connection timed out while loading the website."
		self.website.save(update_fields=["status", "last_error"])
		self.client.force_authenticate(self.owner)

		response = self.client.get(
			reverse("website-detail", kwargs={"pk": self.website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["last_error"], self.website.last_error)
		self.assertNotIn("password", response.data)

	def test_create_assigns_request_user_and_ignores_submitted_owner(self):
		self.client.force_authenticate(self.owner)

		response = self.client.post(
			reverse("website-list"),
			{
				"name": "New website",
				"url": "https://new.example.com/",
				"owner": self.other_user.pk,
			},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		created = Website.objects.get(pk=response.data["id"])
		self.assertEqual(created.owner, self.owner)

	def test_other_users_website_is_not_retrievable_or_mutable(self):
		self.client.force_authenticate(self.owner)
		url = reverse("website-detail", kwargs={"pk": self.other_website.pk})

		self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
		self.assertEqual(
			self.client.patch(url, {"name": "stolen"}, format="json").status_code,
			status.HTTP_404_NOT_FOUND,
		)

	def test_owner_cannot_be_changed_through_api(self):
		self.client.force_authenticate(self.owner)

		response = self.client.patch(
			reverse("website-detail", kwargs={"pk": self.website.pk}),
			{"owner": self.other_user.pk},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.website.refresh_from_db()
		self.assertEqual(self.website.owner, self.owner)

	def test_change_history_is_private_to_the_website_owner(self):
		WebsiteChange.objects.create(
			website=self.other_website,
			previous_hash="a" * 64,
			new_hash="b" * 64,
		)
		self.client.force_authenticate(self.owner)

		response = self.client.get(
			reverse("website-changes", kwargs={"pk": self.other_website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

	def test_unauthenticated_user_cannot_delete_website(self):
		response = self.client.delete(
			reverse("website-detail", kwargs={"pk": self.website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
		self.assertTrue(Website.objects.filter(pk=self.website.pk).exists())

	def test_owner_can_delete_website_and_cascade_only_its_history(self):
		owned_change = WebsiteChange.objects.create(
			website=self.website,
			previous_hash="a" * 64,
			new_hash="b" * 64,
		)
		other_change = WebsiteChange.objects.create(
			website=self.other_website,
			previous_hash="c" * 64,
			new_hash="d" * 64,
		)
		self.client.force_authenticate(self.owner)

		response = self.client.delete(
			reverse("website-detail", kwargs={"pk": self.website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(Website.objects.filter(pk=self.website.pk).exists())
		self.assertFalse(WebsiteChange.objects.filter(pk=owned_change.pk).exists())
		self.assertTrue(Website.objects.filter(pk=self.other_website.pk).exists())
		self.assertTrue(WebsiteChange.objects.filter(pk=other_change.pk).exists())

	def test_user_cannot_delete_another_users_website(self):
		self.client.force_authenticate(self.owner)

		response = self.client.delete(
			reverse("website-detail", kwargs={"pk": self.other_website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertTrue(Website.objects.filter(pk=self.other_website.pk).exists())

	@patch("monitor.views.enqueue_website_check", return_value=True)
	def test_owner_can_queue_check_now_for_owned_website(self, enqueue_check):
		self.client.force_authenticate(self.owner)

		response = self.client.post(
			reverse("website-check-now", kwargs={"pk": self.website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
		self.assertEqual(
			response.data,
			{
				"message": "Website check started.",
				"website_id": self.website.pk,
				"queued": True,
			},
		)
		enqueue_check.assert_called_once_with(self.website.pk)

	@patch("monitor.views.enqueue_website_check")
	def test_unauthenticated_user_cannot_queue_check_now(self, enqueue_check):
		response = self.client.post(
			reverse("website-check-now", kwargs={"pk": self.website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
		enqueue_check.assert_not_called()

	@patch("monitor.views.enqueue_website_check")
	def test_user_cannot_queue_check_now_for_another_users_website(self, enqueue_check):
		self.client.force_authenticate(self.owner)

		response = self.client.post(
			reverse("website-check-now", kwargs={"pk": self.other_website.pk})
		)

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
		enqueue_check.assert_not_called()

	def test_start_and_stop_actions_respect_website_ownership(self):
		self.client.force_authenticate(self.owner)
		start_url = reverse("website-start", kwargs={"pk": self.other_website.pk})
		stop_url = reverse("website-stop", kwargs={"pk": self.other_website.pk})

		self.assertEqual(self.client.post(start_url).status_code, status.HTTP_404_NOT_FOUND)
		self.assertEqual(self.client.post(stop_url).status_code, status.HTTP_404_NOT_FOUND)
		self.other_website.refresh_from_db()
		self.assertEqual(self.other_website.status, "stopped")

		self.assertEqual(
			self.client.post(reverse("website-start", kwargs={"pk": self.website.pk})).status_code,
			status.HTTP_200_OK,
		)
		self.assertEqual(
			self.client.post(reverse("website-stop", kwargs={"pk": self.website.pk})).status_code,
			status.HTTP_200_OK,
		)


@override_settings(WEBSITE_CREDENTIAL_ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
class WebsiteCredentialApiTests(APITestCase):
	def setUp(self):
		self.owner = User.objects.create_user(
			username="credential-owner",
			email="credential-owner@example.com",
			password="Str0ng!CredentialOwner",
		)
		self.other_user = User.objects.create_user(
			username="credential-other",
			email="credential-other@example.com",
			password="Str0ng!CredentialOther",
		)
		self.website = Website.objects.create(
			name="Private portal",
			url="https://portal.example.com/account",
			owner=self.owner,
			requires_login=True,
		)
		self.other_website = Website.objects.create(
			name="Other portal",
			url="https://other.example.com/",
			owner=self.other_user,
			requires_login=True,
		)
		self.credentials_url = reverse("website-credentials", kwargs={"pk": self.website.pk})
		self.payload = {
			"login_url": "https://portal.example.com/login",
			"username": "private-user@example.com",
			"password": "ExternalPass!NotForWebWatch",
			"username_selector": "#email",
			"password_selector": "#password",
			"submit_selector": "button.login",
			"success_selector": "#account-menu",
			"success_url": "",
		}
		self.client.force_authenticate(self.owner)
		login_test_patcher = patch(
			"monitor.views.test_website_login",
			return_value={
				"success": True,
				"message": "Login successful.",
				"storage_state": {"cookies": [], "origins": []},
			},
		)
		self.login_test = login_test_patcher.start()
		self.addCleanup(login_test_patcher.stop)

	def test_owner_can_create_encrypted_credentials_without_password_response(self):
		response = self.client.post(self.credentials_url, self.payload, format="json")

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		credential = WebsiteCredential.objects.get(website=self.website)
		self.assertNotEqual(credential.username_encrypted, self.payload["username"])
		self.assertNotEqual(credential.password_encrypted, self.payload["password"])
		self.assertEqual(credential.get_username(), self.payload["username"])
		self.assertEqual(credential.get_password(), self.payload["password"])
		self.assertEqual(credential.login_status, "connected")
		self.assertNotIn("password", response.data)
		self.assertNotIn(self.payload["password"], str(response.data))
		self.assertNotIn("session_state_encrypted", response.data)
		self.assertEqual(credential.get_session_state(), {"cookies": [], "origins": []})
		self.assertNotEqual(credential.session_state_encrypted, str({"cookies": [], "origins": []}))

		get_response = self.client.get(self.credentials_url)
		self.assertEqual(get_response.status_code, status.HTTP_200_OK)
		self.assertTrue(get_response.data["configured"])
		self.assertEqual(get_response.data["username"], self.payload["username"])
		self.assertNotIn("password", get_response.data)
		self.assertNotIn(self.payload["password"], str(get_response.data))

	def test_user_cannot_create_or_read_another_users_credentials(self):
		other_url = reverse("website-credentials", kwargs={"pk": self.other_website.pk})

		response = self.client.post(other_url, self.payload, format="json")

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertFalse(WebsiteCredential.objects.filter(website=self.other_website).exists())
		self.assertEqual(self.client.get(other_url).status_code, status.HTTP_404_NOT_FOUND)

	def test_owner_can_update_credentials_without_reading_saved_password(self):
		self.client.post(self.credentials_url, self.payload, format="json")
		response = self.client.patch(
			self.credentials_url,
			{"username": "updated@example.com", "password": "ReplacementPass!"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		credential = WebsiteCredential.objects.get(website=self.website)
		self.assertEqual(credential.get_username(), "updated@example.com")
		self.assertEqual(credential.get_password(), "ReplacementPass!")
		self.assertNotIn("password", response.data)

	def test_failed_update_preserves_existing_credentials_and_session(self):
		self.client.post(self.credentials_url, self.payload, format="json")
		credential = WebsiteCredential.objects.get(website=self.website)
		old_username = credential.get_username()
		old_password = credential.get_password()
		old_session = credential.get_session_state()
		self.login_test.return_value = {
			"success": False,
			"message": "Login failed. Check the username and password.",
		}

		response = self.client.put(
			self.credentials_url,
			{**self.payload, "username": "replacement@example.com", "password": "BadReplacement!"},
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		credential.refresh_from_db()
		self.assertEqual(credential.get_username(), old_username)
		self.assertEqual(credential.get_password(), old_password)
		self.assertEqual(credential.get_session_state(), old_session)

	def test_owner_can_delete_credentials(self):
		self.client.post(self.credentials_url, self.payload, format="json")

		response = self.client.delete(self.credentials_url)

		self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(WebsiteCredential.objects.filter(website=self.website).exists())
		self.assertEqual(self.client.get(self.credentials_url).data, {"configured": False})

	def test_unauthenticated_user_cannot_access_credentials(self):
		self.client.force_authenticate(user=None)

		self.assertEqual(self.client.get(self.credentials_url).status_code, status.HTTP_401_UNAUTHORIZED)
		self.assertEqual(
			self.client.post(self.credentials_url, self.payload, format="json").status_code,
			status.HTTP_401_UNAUTHORIZED,
		)

	@patch("monitor.views.test_website_login", return_value={"success": True, "message": "Login successful."})
	def test_test_login_uses_transient_credentials_and_never_returns_them(self, login_test):
		response = self.client.post(
			reverse("website-test-credentials", kwargs={"pk": self.website.pk}),
			self.payload,
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data, {"success": True, "message": "Login successful."})
		self.assertFalse(WebsiteCredential.objects.filter(website=self.website).exists())
		login_test.assert_called_once()

	def test_test_login_rejects_another_users_website(self):
		response = self.client.post(
			reverse("website-test-credentials", kwargs={"pk": self.other_website.pk}),
			self.payload,
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

	def test_unauthenticated_user_cannot_test_website_login(self):
		self.client.force_authenticate(user=None)

		response = self.client.post(
			reverse("website-test-credentials", kwargs={"pk": self.website.pk}),
			self.payload,
			format="json",
		)

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(
	WEBSITE_CREDENTIAL_ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
	EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	DEFAULT_FROM_EMAIL="webwatch@example.com",
)
class WebsiteLoginTaskTests(TestCase):
	def setUp(self):
		self.website = Website.objects.create(
			name="Protected portal",
			url="https://portal.example.com/account",
			owner=User.objects.create_user(
				username="protected-owner",
				email="protected@example.com",
				password="Str0ng!ProtectedOwner",
			),
			requires_login=True,
			status="monitoring",
		)

	def add_credentials(self):
		credential = WebsiteCredential(
			website=self.website,
			login_url="https://portal.example.com/login",
		)
		credential.set_username("protected-user@example.com")
		credential.set_password("ExternalPass!TestOnly")
		credential.save()
		return credential

	def test_missing_credentials_marks_login_required_without_launching_browser(self):
		with patch("monitor.tasks.sync_playwright") as playwright_factory:
			result = check_website(self.website.pk)

		playwright_factory.assert_not_called()
		self.assertIn("credentials are not configured", result["error"])
		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "login_required")
		self.assertEqual(
			self.website.last_error,
			"Website login credentials are not configured.",
		)
		self.assertIsNotNone(self.website.last_checked)
		self.assertEqual(WebsiteChange.objects.count(), 0)

	def test_authenticated_check_continues_through_existing_hash_logic(self):
		self.add_credentials()
		self.website.status = "error"
		self.website.last_error = "Unable to connect to the website."
		self.website.save(update_fields=["status", "last_error"])
		with patch("monitor.tasks.sync_playwright") as playwright_factory:
			page = playwright_factory.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
			page.locator.return_value.inner_text.return_value = "protected account page"
			with patch("monitor.tasks.login_page", return_value={"success": True, "message": "Login successful."}) as login:
				with patch("monitor.tasks.is_login_page", return_value=False):
					result = check_website(self.website.pk)

		login.assert_called_once()
		page.goto.assert_called_once_with(
			self.website.url,
			wait_until="domcontentloaded",
			timeout=30000,
		)
		self.assertFalse(result["changed"])
		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "monitoring")
		self.assertIsNone(self.website.last_error)
		self.assertEqual(
			self.website.content_hash,
			sha256(b"protected account page").hexdigest(),
		)
		self.assertEqual(WebsiteChange.objects.count(), 0)

	def test_failed_login_creates_no_change_and_sends_no_email(self):
		self.add_credentials()
		with patch("monitor.tasks.sync_playwright"):
			with patch("monitor.tasks.login_page", return_value={"success": False, "message": "Unable to verify login."}):
				result = check_website(self.website.pk)

		self.assertFalse(result["changed"])
		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "login_required")
		self.assertEqual(self.website.last_error, "Unable to verify login.")
		self.assertEqual(WebsiteChange.objects.count(), 0)
		self.assertEqual(len(mail.outbox), 0)

	def test_technical_login_failure_is_safe_and_creates_no_change_or_email(self):
		self.add_credentials()
		safe_error = "Connection timed out while loading the login page."
		technical_login_failure = {
			"success": False,
			"message": safe_error,
			"technical_error": True,
			"error_type": "TimeoutError",
		}
		with patch("monitor.tasks.sync_playwright"):
			with patch("monitor.tasks.login_page", return_value=technical_login_failure):
				result = check_website(self.website.pk)

		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "error")
		self.assertEqual(self.website.last_error, safe_error)
		self.assertNotIn("ExternalPass!TestOnly", str(result))
		self.assertEqual(WebsiteChange.objects.count(), 0)
		self.assertEqual(len(mail.outbox), 0)

	@override_settings(WEBSITE_CREDENTIAL_ENCRYPTION_KEY="")
	def test_missing_encryption_key_marks_protected_check_as_technical_error(self):
		credential = WebsiteCredential(
			website=self.website,
			login_url="https://portal.example.com/login",
			username_encrypted="not-decrypted-in-this-test",
			password_encrypted="not-decrypted-in-this-test",
		)
		credential.save()

		result = check_website(self.website.pk)

		self.assertIn("encryption is not configured", result["error"])
		self.website.refresh_from_db()
		self.assertEqual(self.website.status, "error")
		self.assertEqual(WebsiteChange.objects.count(), 0)


class SingleDomainDeploymentTests(TestCase):
	def test_root_loads_frontend_html(self):
		response = self.client.get("/")
		self.assertEqual(response.status_code, 200)
		self.assertIn("text/html", response["Content-Type"])
		self.assertIn("WebWatch", response.content.decode("utf-8"))

	def test_client_routes_serve_frontend_on_refresh(self):
		for route in ("/dashboard", "/websites", "/alerts", "/profile", "/settings", "/history", "/login", "/register"):
			with self.subTest(route=route):
				response = self.client.get(route)
				self.assertEqual(response.status_code, 200)
				self.assertIn("text/html", response["Content-Type"])
				self.assertIn("WebWatch", response.content.decode("utf-8"))

	@override_settings(DEBUG=False)
	def test_api_404_returns_json_not_spa_html(self):
		response = self.client.get("/api/unknown-endpoint/")
		self.assertEqual(response.status_code, 404)
		self.assertIn("application/json", response["Content-Type"])

	def test_healthz_endpoint_remains_available(self):
		response = self.client.get("/healthz/")
		self.assertIn(response.status_code, (200, 503))
		self.assertIn("application/json", response["Content-Type"])

	def test_admin_route_redirects_or_renders_admin_login(self):
		response = self.client.get("/admin/")
		self.assertIn(response.status_code, (200, 302))
