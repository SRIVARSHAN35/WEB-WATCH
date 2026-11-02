# WebWatch WhatsApp notifications

WebWatch sends alerts with Meta's official WhatsApp Cloud API. It uses the website owner's saved recipient number, sends in a Celery task after an actual change, and keeps email delivery independent. API tokens stay in the backend environment and are never returned by the API.

## Configure Meta

1. In [Meta for Developers](https://developers.facebook.com/), create or select an app and add the WhatsApp product.
2. In the app's WhatsApp API Setup page, copy the **Phone Number ID**, **WhatsApp Business Account ID**, and temporary access token. Add a recipient number to Meta's test-recipient list and complete its verification. A test sender number is not necessarily the recipient number saved in WebWatch.
3. For production, attach an owned business phone number and use a properly managed long-lived System User token with the `whatsapp_business_messaging` permission. Keep token rotation and storage in a deployment secret manager; never commit it.
4. Choose a supported Graph API version in Meta's documentation and put its version string (for example `v26.0`) in the backend environment.
5. For alerts initiated outside a WhatsApp customer-service window, create and get approval for a **utility** template. This integration sends three body text parameters in this order: website name, website URL, detected time. A compatible body is:

   `WebWatch alert: A change was detected on {{1}}. Website: {{2}}. Detected at: {{3}}. Open WebWatch to review the changes.`

   Set the exact approved template name and language. Meta validates the actual template at send time; putting a name in `.env` does not mean that the template is approved. If no template is configured, WebWatch sends a text message, which Meta only permits when messaging policy and the customer-service window allow it. Meta template errors are recorded without retrying.

## Backend environment

Copy the placeholder keys from `backend/.env.example` into the private `backend/.env` and fill them locally. `WHATSAPP_BUSINESS_ACCOUNT_ID` is retained for account identification; message sending uses the Phone Number ID. The service requires the integration flag, token, Phone Number ID, and an explicit supported Graph API version.

```env
WHATSAPP_ENABLED=True
WHATSAPP_ACCESS_TOKEN=<Meta access token>
WHATSAPP_PHONE_NUMBER_ID=<Meta phone number ID>
WHATSAPP_BUSINESS_ACCOUNT_ID=<Meta WABA ID>
WHATSAPP_GRAPH_API_VERSION=<supported version, for example v26.0>
WHATSAPP_TEMPLATE_NAME=<approved template name, or leave blank>
WHATSAPP_TEMPLATE_LANGUAGE=en
```

Restart Django and the Celery worker after changing environment values. Configure Redis as the broker using the existing Celery settings.

## Apply database migration and run locally

From PowerShell:

```powershell
cd C:\Windows\System32\webwatchapp\backend
.\venv312\Scripts\python.exe manage.py migrate
.\venv312\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Start the Redis or Memurai Windows service already configured for the project. If `redis-server.exe` is installed and configured for local development, you can run it in a separate terminal:

```powershell
redis-server.exe
```

Check the service with `redis-cli.exe ping` (it should reply `PONG`). Then in another backend terminal start the Windows-compatible Celery worker:

```powershell
cd C:\Windows\System32\webwatchapp\backend
.\venv312\Scripts\celery.exe -A config worker -l info -P solo
```

Start Celery Beat in another backend terminal. It periodically checks for due website checks, retries email notifications, and requeues pending WhatsApp deliveries:

```powershell
cd C:\Windows\System32\webwatchapp\backend
.\venv312\Scripts\celery.exe -A config beat -l info
```

Start the frontend in another terminal:

```powershell
cd C:\Windows\System32\webwatchapp\frontend
npm run dev
```

## User setup and test

Sign in to WebWatch and open **Settings → WhatsApp alerts**. Enter an international E.164 number (leading `+`, country code, then digits), enable alerts and save. The recipient must be able to receive WhatsApp messages and, for Meta's test sender, must be on Meta's verified test-recipient list. The recipient is per user and may differ from the sender.

Use **Send test message** after saving the number. It sends Meta's `hello_world` template in `en_US`, independent of the alert template. The API accepts no recipient in this request; it uses only the authenticated user's saved setting. The test route is limited to three requests per hour per account. A success response means Meta accepted the message for processing; recipient delivery is unconfirmed until a verified delivery-status webhook is implemented.

API routes (all require WebWatch authentication):

- `GET` / `PATCH /api/notifications/whatsapp/settings/` — read or update the signed-in user's `enabled` and `phone_number` fields.
- `POST /api/notifications/whatsapp/test/` — send to the signed-in user's saved number; send an empty JSON object, not a recipient.
- `GET /api/notifications/whatsapp/history/` — return the signed-in user's last 30 masked delivery records.

For example, after authenticating, save settings with:

```powershell
curl.exe -X PATCH http://127.0.0.1:8000/api/notifications/whatsapp/settings/ `
  -H "Authorization: Token <your WebWatch API token>" `
  -H "Content-Type: application/json" `
  -d '{"enabled":true,"phone_number":"+14155552671"}'
```

Automated tests mock all Cloud API requests and never send a real message. To run the relevant tests:

```powershell
cd C:\Windows\System32\webwatchapp\backend
.\venv312\Scripts\python.exe manage.py test monitor.tests.WhatsAppServiceTests monitor.tests.WhatsAppSettingsApiTests monitor.tests.WhatsAppChangeTaskTests
```

## Production considerations

- Use HTTPS for the deployed site and API, managed secrets for the Meta token, a production WSGI/ASGI server, and a protected Redis deployment.
- Obtain consent for WhatsApp notifications, honor opt-outs, and follow Meta's current WhatsApp Business Messaging policy and template rules.
- Configure a Meta webhook over public HTTPS and verify its challenge/signature before storing delivery/read/failure statuses. This change records Meta acceptance and message ID but does not implement webhook receipt.
- The database deduplicates repeated Celery tasks per detected change. Meta's send endpoint does not provide an application-level exactly-once guarantee; a worker crash after Meta accepts a message but before WebWatch records the response can make a retry ambiguous.
- Review retention and access for saved recipient numbers and notification history. Account deletion cascades the preferences and delivery records.
