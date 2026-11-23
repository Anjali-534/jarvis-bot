"""Every test runs offline, without real apps, and without the user's real data files."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import jarvis.skills  # noqa: E402,F401
from jarvis import geo, google_auth, http, router  # noqa: E402
from jarvis.skills import calendar, spotify, vscode, whatsapp  # noqa: E402

PURE_SKILLS = {"calculator"}  # real handlers that may return None to fall through


@pytest.fixture
def recorded_skills(monkeypatch):
    """Replace every handler with one that just reports its name (routing tests)."""
    for sk in router.SKILLS:
        if sk.name not in PURE_SKILLS:
            monkeypatch.setattr(sk, "handler", lambda ctx, cmd, m, name=sk.name: f"<{name}>")
    monkeypatch.setattr(router.FALLBACK, "handler", lambda ctx, cmd, m: "<ai>")
    monkeypatch.setattr(router.FALLBACK, "name", "ai")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected real side effect: {args[:1]}")

    for name in ("get_json", "get_text", "post_json", "post_stream"):
        monkeypatch.setattr(http, name, forbidden)
    for name in ("open_uri", "open_web", "press_enter"):
        monkeypatch.setattr(whatsapp, name, forbidden)
    monkeypatch.setattr(whatsapp, "sleep", lambda seconds: None)
    monkeypatch.setattr(vscode, "shell", forbidden)
    monkeypatch.setattr(vscode, "git", forbidden)
    # Google and Spotify: no real sign-in, API call, app launch or media key
    monkeypatch.setattr(google_auth, "service", forbidden)
    monkeypatch.setattr(google_auth, "run_flow", forbidden)
    for name in ("open_uri", "media_key", "run_flow"):
        monkeypatch.setattr(spotify, name, forbidden)
    monkeypatch.setattr(spotify, "sleep", lambda seconds: None)
    monkeypatch.setattr(calendar, "schedule_alert", forbidden)

    monkeypatch.setattr(config, "USER_NAME", "Anjali")
    monkeypatch.setattr(config, "WAQI_TOKEN", "")
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")  # tests that need Gemini switch it on
    monkeypatch.setattr(config, "AI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config, "AI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(config, "WHATSAPP_SEND_DELAY", 3.0)
    monkeypatch.setattr(config, "NOTIFY", False)          # no real toasts popping up during tests
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "data" / "logs")
    monkeypatch.setattr(config, "RED_ZONE_THRESHOLD", 200)
    monkeypatch.setattr(config, "CONTACTS_FILE", tmp_path / "data" / "contacts.json")
    monkeypatch.setattr(config, "PROJECTS_FILE", tmp_path / "data" / "projects.json")
    monkeypatch.setattr(config, "PROJECT_ROOTS", [])
    monkeypatch.setattr(config, "GOOGLE_CREDENTIALS_FILE", tmp_path / "data" / "google_credentials.json")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_FILE", tmp_path / "data" / "google_token.json")
    monkeypatch.setattr(config, "SPOTIFY_TOKEN_FILE", tmp_path / "data" / "spotify_token.json")
    monkeypatch.setattr(config, "SPOTIFY_CLIENT_ID", "")
    monkeypatch.setattr(config, "CALENDAR_REMINDERS", True)
    monkeypatch.setattr(config, "CACHE_FILE", tmp_path / "data" / "cache.json")
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(config, "AI_ROUTING", False)  # intent tests switch it on
    monkeypatch.setattr(config, "AI_ROUTER_MODEL", "")
    monkeypatch.setattr(config, "BRIEFING_TIME", "")
    monkeypatch.setattr(config, "BRIEFING_MUSIC", "")
    monkeypatch.setattr(config, "BRIEFING_SECTIONS", ["weather", "aqi", "calendar", "email", "reminders", "news"])  # never rewrite the real .env
    from jarvis.skills import ai as ai_skill

    monkeypatch.setattr(ai_skill, "_switch_pending", False)
    monkeypatch.setattr(config, "SAMPLES_DIR", tmp_path / "data" / "samples")
    monkeypatch.setattr(config, "ONE_SEC_AFTER", 0)   # tests that need "One sec." turn it on
    monkeypatch.setattr(config, "CALENDAR_REMINDER_MINUTES", 10)
    monkeypatch.setattr(vscode, "_cache", None)
    geo._CACHE.clear()
    yield
    from jarvis.skills import notes

    notes.cancel_all_timers()  # a reminder must not fire into a later test (or pop a real toast)
    calendar.stop_reminders()
    from jarvis.skills import briefing

    briefing.stop_scheduler()
