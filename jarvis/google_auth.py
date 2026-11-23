"""Google sign-in shared by the Gmail and Calendar skills.

Desktop OAuth flow: the client file you download from Google Cloud Console lives in
data/google_credentials.json and the sign-in token is saved to data/google_token.json
(both git-ignored, paths set in config). Say "connect google" to sign in.
"""
import functools
import logging

import config
from jarvis.router import SpokenError

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]

TIMEOUT = 6
NOT_CONNECTED = "Your Google account isn't connected yet. Say connect google to set it up."
EXPIRED = "Your Google sign-in has expired or was revoked. Say connect google to sign in again."
MISSING_LIBS = "The Google libraries aren't installed. Run pip install -r requirements.txt, then try again."

SETUP_STEPS = """
=== Connect Jarvis to Google (Gmail + Calendar), one-time setup ===
1. Open https://console.cloud.google.com/ and create a new project (top bar > project picker > New project),
   for example "Jarvis".
2. APIs & Services > Library: search for and ENABLE both "Gmail API" and "Google Calendar API".
3. APIs & Services > OAuth consent screen (Google Auth Platform):
   - User type: External. App name: Jarvis. Use your own email for the support and developer contact.
   - Audience / Test users: ADD YOUR OWN GMAIL ADDRESS as a test user.
4. APIs & Services > Credentials > Create credentials > OAuth client ID > Application type: Desktop app.
5. Download the JSON and save it as:
       {path}
6. Say "connect google" again. A browser opens: pick your account, click Continue on the
   "Google hasn't verified this app" screen (it's your own app), and allow access.
Jarvis can then read your inbox, save email DRAFTS (it never sends) and manage calendar events.
"""


class NotConnected(SpokenError):
    """Google isn't connected; the message tells the user what to say."""


class _Never(Exception):
    """Placeholder exception type when an optional library isn't installed."""


def libraries_ok():
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
    except ImportError:
        return False
    return True


def setup_steps():
    return SETUP_STEPS.format(path=config.GOOGLE_CREDENTIALS_FILE)


def has_client_file():
    return config.GOOGLE_CREDENTIALS_FILE.exists()


def is_connected():
    """True if a sign-in token has been saved (it may still need a refresh)."""
    return config.GOOGLE_TOKEN_FILE.exists()


def _save(creds):
    path = config.GOOGLE_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def load_credentials():
    """The saved credentials, refreshed if needed, or None if not connected / revoked."""
    if not is_connected():
        return None
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_FILE), SCOPES)
    except (ValueError, OSError) as exc:
        log.warning("[google] token file unreadable: %s", exc)
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            log.warning("[google] token refresh failed: %s", exc)
            raise SpokenError(EXPIRED) from exc
        _save(creds)
        return creds
    return None


def service(name, version):
    """An authorised Google API client, e.g. service("gmail", "v1"). Raises NotConnected."""
    if not libraries_ok():
        raise SpokenError(MISSING_LIBS)
    creds = load_credentials()
    if creds is None:
        raise NotConnected(NOT_CONNECTED)
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    authed = AuthorizedHttp(creds, http=httplib2.Http(timeout=TIMEOUT))  # 6 s, like other APIs
    return build(name, version, http=authed, cache_discovery=False)


def run_flow():
    """Open the browser for Google sign-in and save the token. Blocks until done (5 minute limit)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300,
                                  success_message="Jarvis is connected to Google. You can close this tab.")
    _save(creds)
    return creds


def disconnect():
    if config.GOOGLE_TOKEN_FILE.exists():
        config.GOOGLE_TOKEN_FILE.unlink()


def _error_types():
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        HttpError = _Never
    try:
        from google.auth.exceptions import RefreshError, TransportError
    except ImportError:
        RefreshError = TransportError = _Never
    try:
        from httplib2 import HttpLib2Error
    except ImportError:
        HttpLib2Error = _Never
    return HttpError, RefreshError, (TransportError, HttpLib2Error, OSError, TimeoutError)


def friendly_errors(fn):
    """Turn Google API failures inside a skill into clear spoken messages."""

    @functools.wraps(fn)
    def wrapper(ctx, command, match):
        HttpError, RefreshError, network = _error_types()
        try:
            return fn(ctx, command, match)
        except SpokenError:
            raise
        except RefreshError:
            return EXPIRED
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", 0)
            log.warning("[google] HTTP %s: %s", status, exc)
            if status in (401, 403):
                reason = str(exc).lower()
                if "has not been used" in reason or "disabled" in reason:
                    return ("Google says that API isn't enabled for your project. Enable the Gmail API and the "
                            "Google Calendar API in Google Cloud Console, then try again.")
                return f"Google refused that request. {EXPIRED}"
            if status == 404:
                return "Google couldn't find that item. It may have been deleted already."
            if status == 429:
                return "Google says I'm making too many requests. Please try again in a minute."
            return f"Google returned an error, code {status}. Please try again later."
        except network as exc:
            log.warning("[google] network error: %s", exc)
            return "I couldn't reach Google right now. Please check your internet and try again."

    return wrapper
