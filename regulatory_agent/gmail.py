"""Google OAuth desktop sign-in and Gmail API mailbox transport.

No account password is read by this transport. Authentication is delegated to
Google's maintained OAuth client, including state, PKCE and token refreshing.
"""
import base64
import email.policy
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Covers read, send and marking processed messages read; no permanent deletion.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/"


def token_path() -> Path:
    return Path(os.getenv("GOOGLE_TOKEN_FILE") or ".secrets/google-token.json")


def save_credentials(credentials, path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Never print tokens. Replace atomically so interruption cannot truncate them.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=path.parent,
                                         prefix=".oauth-", delete=False) as file:
            temporary = Path(file.name)
            file.write(credentials.to_json())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def load_credentials():
    path = token_path()
    if not path.is_file():
        raise ValueError("Google sign-in is not configured. Run: python -m regulatory_agent authorize-google")
    try:
        # Read actual granted scopes; do not replace missing scopes with defaults.
        credentials = Credentials.from_authorized_user_file(str(path))
        if not credentials.has_scopes(SCOPES):
            raise ValueError("Google permission is incomplete. Run authorize-google again.")
        if not credentials.valid:
            if not credentials.refresh_token:
                raise ValueError("Google sign-in has no refresh token. Run authorize-google again.")
            credentials.refresh(GoogleRequest())
            save_credentials(credentials, path)
        return credentials
    except RefreshError:
        raise ValueError("Google sign-in expired or was revoked. Run authorize-google again.") from None
    except (json.JSONDecodeError, KeyError):
        raise ValueError("The saved Google sign-in is invalid. Run authorize-google again.") from None


def authorize(client_file: Path | None = None, *, log=print, open_browser=True):
    path = client_file or Path(os.getenv("GOOGLE_CLIENT_FILE") or ".secrets/google-client.json")
    if not path.is_file():
        raise ValueError(f"Save your Google Desktop app OAuth client JSON to {path.resolve()}. See GOOGLE-SETUP.md.")
    try:
        config = json.loads(path.read_text(encoding="utf8"))
    except (ValueError, OSError):
        raise ValueError("Could not read the Google OAuth client JSON.") from None
    if "installed" not in config:
        raise ValueError("Create a Desktop app OAuth client, rather than a Web application or service account.")
    log("Opening Google sign-in. Select the agent's Gmail account and grant the requested Gmail permission.")
    flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(
        host="127.0.0.1", port=0, open_browser=open_browser, timeout_seconds=300,
        authorization_prompt_message="Complete Google sign-in in the browser window.",
        success_message="Google authorization received. You may close this tab and return to the agent.",
        access_type="offline", prompt="consent", login_hint=os.getenv("AGENT_EMAIL", ""),
    )
    if not credentials or not credentials.valid or not credentials.has_scopes(SCOPES):
        raise ValueError("Google sign-in did not grant the required Gmail permission.")
    if not credentials.refresh_token:
        raise ValueError("Google did not grant background access. Run authorize-google and grant permission again.")
    # Validate the actual mailbox before persisting this sign-in.
    mailbox = GmailMailbox(credentials=credentials, persist=False)
    try:
        address = mailbox.address
    finally:
        mailbox.close()
    save_credentials(credentials, token_path())
    log(f"Google sign-in connected: {address}")
    log("Ready. Start the worker with: python -m regulatory_agent email")
    return address


class GmailMailbox:
    def __init__(self, *, credentials=None, session=None, persist=True):
        self.credentials = credentials if credentials is not None else load_credentials()
        self.session = session if session is not None else AuthorizedSession(self.credentials)
        self.persist = persist
        self.saved = self.credentials.to_json()
        try:
            self.address = self.request("GET", "profile")["emailAddress"]
            expected = os.getenv("AGENT_EMAIL", "").strip()
            if not expected:
                raise ValueError("Set AGENT_EMAIL in .env before connecting Google sign-in.")
            if self.address.casefold() != expected.casefold():
                raise ValueError(f"Google sign-in used {self.address}; the agent is configured for {expected}. Sign in to the agent account.")
        except Exception:
            self.session.close()
            raise

    def request(self, method, endpoint, **kwargs):
        try:
            response = self.session.request(method, BASE_URL + endpoint, timeout=60, **kwargs)
        except RefreshError:
            raise ValueError("Google sign-in expired or was revoked. Run authorize-google again.") from None
        except Exception:
            # Exception text can contain bearer credentials or request bodies.
            raise RuntimeError("Could not reach the Gmail API. Check your connection and Google sign-in.") from None
        if not response.ok:
            status = response.status_code
            hint = ""
            if status == 401:
                hint = " Run authorize-google again."
            elif status == 403:
                hint = " Enable Gmail API in the OAuth client's project and check Gmail permissions and account policy."
            elif status == 429:
                hint = " Gmail is rate limiting this account; retry later."
            raise RuntimeError(f"Gmail API request failed (HTTP {status})." + hint)
        if self.persist:
            current = self.credentials.to_json()
            if current != self.saved:
                save_credentials(self.credentials, token_path())
                self.saved = current
        return response.json()

    def unread(self):
        # Search only inbox/unread mail, matching the IMAP worker's behavior.
        result = self.request("GET", "messages", params={"labelIds":["INBOX", "UNREAD"], "maxResults":10})
        return [message["id"] for message in result.get("messages", [])]

    def key(self, message_id):
        return f"gmail-api/{self.address.casefold()}/{message_id}"

    def fetch(self, message_id):
        result = self.request("GET", "messages/" + quote(message_id, safe=""), params={"format":"raw"})
        raw = result["raw"]
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

    def mark_read(self, message_id):
        self.request("POST", "messages/" + quote(message_id, safe="") + "/modify", json={"removeLabelIds":["UNREAD"]})

    def send(self, message):
        raw = base64.urlsafe_b64encode(message.as_bytes(policy=email.policy.SMTP)).decode("ascii")
        return self.request("POST", "messages/send", json={"raw":raw})

    def close(self):
        self.session.close()
