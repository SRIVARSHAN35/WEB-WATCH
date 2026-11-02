
import base64
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Permission required to send email using Gmail API
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# File locations
BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "gmail_token.json"


def get_credentials():
    """Authenticate with Google and save the token locally."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            str(TOKEN_FILE), SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return creds


def send_test_email(recipient):
    """Send one test email through the Gmail API."""
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"OAuth credentials file not found: {CREDENTIALS_FILE}"
        )

    service = build("gmail", "v1", credentials=get_credentials())

    message = EmailMessage()
    message["To"] = recipient
    message["From"] = "me"
    message["Subject"] = "WebWatch Email Test"
    message.set_content(
        "Success! WebWatch can send email using Gmail OAuth 2.0."
    )

    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode("utf-8")

    result = service.users().messages().send(
        userId="me",
        body={"raw": encoded_message},
    ).execute()

    print("Email submitted successfully!")
    print("Gmail message ID:", result["id"])
    print("Recipient:", recipient)


if __name__ == "__main__":
    # Change this address if you want to receive the test elsewhere.
    send_test_email("sridharun35@gmail.com")
