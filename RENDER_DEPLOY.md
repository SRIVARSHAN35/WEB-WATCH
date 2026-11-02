# Deploy WebWatch on Render

The root `render.yaml` defines a React static site, a Django API service, and a Render Key Value instance for Celery. The Django service runs Gunicorn and the Celery worker/beat process together so they use the same SQLite file on the attached persistent disk.

## Database and persistence

- The deployed app uses a new SQLite database at `/var/data/db.sqlite3`.
- Render mounts a persistent disk at `/var/data`; the API service uses a paid Starter plan because Render does not attach persistent disks to free web services.
- The database starts empty. The local `.env`, MySQL database, and `db.sqlite3` are not included in Git or copied to Render. Do not point a public service at a local-only database. If existing user and monitor records need to be transferred, export and import them as a separate, reviewed step.
- SQLite is suitable for a small single-instance deployment. Do not scale this service to multiple instances. For sustained concurrent monitoring, move to a managed database with multi-process support.
- The free Key Value queue is in-memory and can lose queued jobs after a restart. Periodic Celery Beat schedules will enqueue subsequent checks again; use a paid persistent Key Value plan if queued tasks must survive restarts.

## Create the Render services

1. In the Render Dashboard, choose **New > Blueprint** and connect `SRIVARSHAN35/webwatchapp` on branch `main`.
2. Review the services and plan before deploying. The API's Starter service and 1 GB persistent disk are paid; the static site and free Key Value service are separate resources.
3. Deploy the Blueprint. The API service applies Django migrations on startup, then starts the web server and Celery worker/beat.
4. The Blueprint expects these generated service URLs. If Render assigns a different hostname, update `CORS_ALLOWED_ORIGINS_EXTRA` on the API and `VITE_API_BASE_URL` on the static site, then redeploy the static site:
   - Frontend: `https://webwatch-monitoring-ui.onrender.com`
   - API: `https://webwatch-monitoring-api.onrender.com/api`

## Set secrets and OAuth

The Blueprint generates `SECRET_KEY` and `WEBSITE_CREDENTIAL_ENCRYPTION_KEY`. Enter the following in Render's Environment settings; keep their values out of Git and chat:

- `GOOGLE_CLIENT_ID` on the API and `VITE_GOOGLE_CLIENT_ID` on the static site. They must contain the same Web OAuth client ID.
- In Google Cloud Console, add `https://webwatch-monitoring-ui.onrender.com` as an Authorized JavaScript origin. The current GIS callback flow does not need an Authorized redirect URI. If the consent screen is in Testing, add the Google accounts that need to sign in as test users.
- To enable email alerts, configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL` on the API service. Do not use these settings to set the notification recipient; recipients come from the WebWatch account.
- WhatsApp remains disabled unless you set the WhatsApp Cloud API values and explicitly set `WHATSAPP_ENABLED=True` on the API.

The API health check is `https://webwatch-monitoring-api.onrender.com/healthz/`. It checks that Django can connect to the deployed database.

## Runtime notes

The browser monitor installs Chromium during the API build. If Playwright processes exceed the selected service's memory, increase the API service plan in Render. Keep the service to one instance while SQLite is in use.
