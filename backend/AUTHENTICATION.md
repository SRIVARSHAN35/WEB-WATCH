# Authentication setup

## Development data

Migration `monitor.0004_website_owner` preserves all existing websites and change-history records. It assigns websites that existed before authentication to an inactive account named `webwatch-legacy` (or a suffixed variant if that username already exists). The account has an unusable password, a blank email, and cannot sign in. Migration `monitor.0005_clear_legacy_owner_email` blanks the placeholder email on databases that already applied the first ownership migration, so alerts are skipped until reassignment. Create or use an administrator, then reassign those websites to real users in Django admin before deleting the placeholder account. No website or history rows are deleted.

## Manual accounts

Registration and login use Django's built-in user/password hashing and DRF token authentication. The browser keeps the token in tab-scoped `sessionStorage`, never the password. A token is revoked on logout. Run the backend migrations before starting the API:

```powershell
python manage.py migrate
```

The Google ID-token verifier dependency is listed in `requirements-auth.txt`; install it into the same virtual environment as Django:

```powershell
pip install -r requirements-auth.txt
```

## Developer console

The developer console is available at `/developer/login` and uses the existing WebWatch email/password login endpoint. Access to every `/api/developer/` request is checked by Django REST Framework's `IsAdminUser`; a frontend route or a successful normal login does not grant developer access. Create a developer account with Django's existing staff workflow from the `backend` directory:

```powershell
venv312\Scripts\python.exe manage.py createsuperuser
```

The console never exposes password hashes, website login credentials or browser sessions, monitored page contents or hashes, or Google subject identifiers. It can inspect Users, Websites, Website alerts, Website changes, Activity history, Notification settings, and Google connections. Editing is limited to user first/last names, website metadata/status/check interval, alert read state, and notification settings. User deletion, audit-log modification, activity modification, Google-identity editing, and website credential editing are disabled.

Website deletion also removes its encrypted website credentials/session, change history, and linked alerts. Change-history deletion removes linked alerts. The console requires an exact record ID, a confirmation, and acknowledgement that a current backup exists. It does not create backups or alter Redis/Celery queues. Keep a current database backup before any production deletion; for MySQL, use the organization's verified backup process or `mysqldump --single-transaction --routines --triggers` and verify restore procedures. Developer changes and deletions are recorded in `DeveloperAuditLog`; audit entries contain the actor, operation, record key, field names, and timestamp, not old/new values.

## Account deletion

An authenticated user can permanently delete their WebWatch account with `DELETE /api/auth/delete-account/` and JSON body `{"confirmation":"DELETE"}`. The endpoint uses the caller's DRF token identity, requires exact confirmation, and is limited to five requests per user per hour. Django's user deletion cascades to that user's token, Google sign-in identity, websites and encrypted monitoring credentials, change history, alerts, preferences, and activity. Other users' data is not selected or changed. The API does not accept a user ID or email to choose an account.

### Local developer console access

There are no built-in developer credentials or public developer-registration flow. From the backend directory, run `python manage.py bootstrap_developer`. The command prompts for an email and a new password without echoing it. It creates a staff account if the email is new, or sets a new password and enables `is_staff` on the matching active account. It does not grant `is_superuser`. Use this trusted local management command instead of adding a public endpoint that can grant developer privileges.

The integration stores only a Google identity subject for Google sign-in; it does not store Google access or refresh tokens, so there is no remote Google token to revoke. Deleting a WebWatch account does not delete or revoke the user's Google account. Existing developer audit entries retain their non-sensitive action metadata but the deleted actor relation is cleared (`actor_email` is shown as `Deleted account`). A `DeletedAccountMarker` retains only the numeric user ID and timestamp so stale Celery monitoring/notification jobs can stop; it contains no email, name, token, or website credential. Monitoring workers check the marker before making further page requests and before saving changes; queued jobs for deleted website/change rows become no-ops. A network request already in flight at the instant deletion commits cannot be recalled, but its result is discarded and cannot recreate deleted application data.

Migration `monitor.0016_developerauditlog` adds only the audit table and preserves existing application data. Apply migrations before launching the updated backend:

```powershell
venv312\Scripts\python.exe manage.py migrate
venv312\Scripts\python.exe manage.py runserver
```

Run the frontend separately from `frontend` with `npm run dev`, then open `http://localhost:5173/developer/login`. Recent application errors are shown as unavailable because this project has no persistent application error log configured. The Django admin link opens `/admin/` on the backend origin.

## Google sign-in

Create a Google OAuth client with the Google Identity Services JavaScript API enabled. Set the same web client ID in the backend environment as `GOOGLE_CLIENT_ID` and in the Vite environment as `VITE_GOOGLE_CLIENT_ID`. Add the frontend origin (normally `http://localhost:5173` or `http://127.0.0.1:5173`) to the OAuth client's authorized JavaScript origins. This ID-token flow verifies the signed Google credential on the backend with Google's published keys; it does not require a client secret. Never accept an email from the browser as proof of identity.

Users who created a password-based WebWatch account can sign in normally, choose **Link Google** in the dashboard, and complete Google sign-in with the same verified email. WebWatch stores Google's stable subject identifier on the account. A Google account already linked to another WebWatch account, or a different Google account already linked to this one, is rejected. Apply the generated migration with `python manage.py migrate` before starting the updated backend.

## Change notification email

The recipient is the owner email on each website. A website currently has one owner; shared ownership is not part of the data model. Configure the SMTP sender independently with `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL`. Keep the SMTP password in the runtime environment, not source control. Without an owner email, the notification remains pending and is skipped with a safe log entry; an email failure does not undo a saved website change.

Change emails are delivered by the existing Celery worker. `WebsiteChange` records track send attempts and successful delivery; failed attempts are retried with backoff, and Beat requeues due pending notifications. Migration `monitor.0012` marks pre-existing history as historical so applying it cannot send duplicate alerts for old changes. Apply migrations before starting the updated backend or worker. SMTP timeout defaults to 30 seconds and can be set with `EMAIL_TIMEOUT`. `WEBWATCH_DASHBOARD_URL` is optional; a dashboard link is included only when it is configured as a valid HTTP or HTTPS URL.

Change notifications use a responsive HTML template plus a plain-text alternative. The original banner asset is bundled at `monitor/static/monitor/email_banner.png` and is embedded in the message by default, so it also works in local development. For production, optionally host that exact image at a publicly accessible HTTPS URL and set `WEBWATCH_EMAIL_BANNER_URL`; the template then uses that URL instead of a CID attachment. Email content includes website metadata and a generic change summary, never monitored page contents or hashes.

The placeholder-only `backend/.env.example` lists the email-related variables. Copy or merge those keys into the local `backend/.env` without replacing existing database or provider configuration. Leave `WEBWATCH_EMAIL_BANNER_URL` empty for inline-image development. In production, set it to the HTTPS URL of the hosted banner; set `WEBWATCH_DASHBOARD_URL` to a valid public dashboard URL if the footer link is desired.

To safely exercise a branded change notification without sending real email, run the in-memory test for the monitoring notification flow:

```powershell
cd backend
venv312\Scripts\python.exe manage.py test monitor.tests.WebsiteNotificationTaskTests.test_content_change_sends_notification
```

The test mocks Playwright and uses Django's locmem email backend; it checks the HTML and plain-text messages in the test outbox.

For real SMTP delivery, configure these variables in the backend **and Celery worker** process environments. Use your provider's SMTP host, port, account, and sender values locally; never commit a populated `.env` file or share its password.

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=<smtp-host>
EMAIL_PORT=<smtp-port>
EMAIL_USE_TLS=True
EMAIL_HOST_USER=<smtp-user>
EMAIL_HOST_PASSWORD=<smtp-password>
DEFAULT_FROM_EMAIL=<from-address>
EMAIL_TIMEOUT=30
# Optional; omit or leave empty to omit the dashboard link from alerts.
WEBWATCH_DASHBOARD_URL=<valid-dashboard-url>
# Optional; public HTTPS image URL. If blank, the bundled image is CID-embedded.
WEBWATCH_EMAIL_BANNER_URL=
# Set the public frontend origin for password-reset links. It falls back to
# WEBWATCH_DASHBOARD_URL, then localhost:5173 only while Django DEBUG is enabled.
WEBWATCH_PASSWORD_RESET_URL=<valid-frontend-url>
```

Password reset requests always return the same generic response to avoid revealing whether an email belongs to an account. The backend uses Django's password-reset token generator and validates the token before accepting a new password. Email submission failures are logged by exception type only; the reset URL, token, recipient, and SMTP credentials are never written to logs. A successful backend handoff does not prove inbox delivery.

Run a one-off delivery test to an inbox you can check:

```powershell
cd backend
.\venv\Scripts\python.exe manage.py test_email <recipient-address>
```

The command uses Django's configured email backend, sends only a simple test message, and omits exception details that could contain provider data. Success means the configured backend accepted the message; also check the recipient's inbox, spam/quarantine folder, and provider delivery logs. Automated tests use Django's in-memory backend and do not send real email.

## MySQL database

Django 6.1.2 requires MySQL 8.4 or later. The active `MYSQL84` service was verified on MySQL 8.4.11. Django uses MySQL when `DB_ENGINE=django.db.backends.mysql` is set. Configure `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` in the backend process environment as well. Keep `DB_PASSWORD` in a local secret store or runtime environment; never commit it or a populated `.env` file. When `DB_ENGINE` is unset, settings retain the existing SQLite development database at `backend/db.sqlite3`.

Create a dedicated database and grant the application account access to that database before switching the application. For a local MySQL server, an administrator can create the database with:

```sql
CREATE DATABASE webwatch CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

After configuring the environment, verify the application account can connect before running migrations:

```powershell
python manage.py check
python manage.py showmigrations
python manage.py migrate
```

To move an existing SQLite installation, preserve `db.sqlite3`, make a fixture with `python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude admin.logentry --indent 2`, switch the DB environment to MySQL, run migrations there, and load that fixture. Django's JSON fixture serialization may reduce datetime precision; compare the imported timestamps with SQLite and restore any precision lost before considering the transfer complete. Confirm user/token, website, and change-history counts, ownership links, hashes, status, intervals, and timestamps before treating MySQL as the only active database. Do not load SQLite's migration table; migrations should be established by `migrate` on the MySQL database.

The MySQL test runner creates a separate database (by default, `test_webwatch`) and does not run the suite against the live application database. The application account must be allowed to create and remove that isolated test database. If the account is intentionally restricted to `webwatch.*`, have a DBA provision a separate test schema and grant the test account permissions limited to that schema before running the suite; never point tests at `webwatch`.

## Monitored website credentials

Website login credentials are distinct from WebWatch account credentials. They are stored in the one-to-one `WebsiteCredential` row encrypted with Fernet authenticated encryption. Both username and password are encrypted at rest. API responses expose the username and selector configuration but never the password or ciphertext; website credentials are not registered in Django admin. The browser keeps an entered password only in the login modal's component memory, does not prefill it when reopening the modal, and sends it only in authenticated API request bodies.

Set `WEBSITE_CREDENTIAL_ENCRYPTION_KEY` in the backend and Celery worker environments before saving or using credentials. Generate a Fernet key locally with:

```powershell
$env:WEBSITE_CREDENTIAL_ENCRYPTION_KEY = (& .\venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

Persist the generated value in the deployment secret manager so every web and worker process uses exactly the same key. Never commit it, print it in logs, or put it in the database. Losing or changing this key makes already-saved website credentials permanently undecryptable; back it up securely and plan key rotation with an explicit decrypt/re-encrypt operation. Migrate the credential table with `python manage.py migrate` after configuring MySQL; existing websites do not need credentials unless `requires_login` is enabled.

For each website, configure the login URL, username/email, password, and CSS selectors. The initial defaults are `input[type="email"]`, `input[type="password"]`, and `button[type="submit"]`; adjust them to match that website. Login verification uses an optional success selector or success URL, otherwise a post-submit URL change is required. Test Login does not save credentials; Save Credentials is a separate action. Production API traffic carrying credentials must use HTTPS.

Playwright uses an in-memory browser context and closes it after each check. Cookie/storage state is not written to disk or sent to the frontend. CAPTCHA, Cloudflare challenges, OTP, MFA, and 2FA are not bypassed; checks needing additional authentication stay in `login_required` with a safe message. Login failures do not create change-history records or send change notifications. Only after a verified login does the worker visit the monitored URL and continue through the existing hash/change/email flow.

## Monitoring checks (Windows development)

Each website can monitor `body` or a CSS selector such as `#job-title` or `.price`. The default `body` target compares page text and form values. A specific element target also detects changes to its HTML and attributes. Change history stores the previous and new displayed values (up to 5,000 characters each). The **View Changes** dialog refreshes every three seconds while open.

Redis must be running. Keep these processes running in separate PowerShell windows from the backend directory so scheduled checks continue:

```powershell
.\venv312\Scripts\celery.exe -A config worker --pool=solo --loglevel=INFO
```

```powershell
.\venv312\Scripts\celery.exe -A config beat --loglevel=INFO
```

Beat queues due checks once per minute; each website's configured interval controls how often it is checked. Use **Check Now** to queue an immediate check. The change appears in an open **View Changes** dialog within its next refresh after the check finishes.
