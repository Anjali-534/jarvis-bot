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

# real handlers that may return None to fall through ("start chrome" isn't a routine, "open chrome" isn't a file)
PURE_SKILLS = {"calculator", "run routine", "open file", "find file", "recall memory", "forget memory"}


@pytest.fixture
def recorded_skills(monkeypatch):
    """Replace every handler with one that just reports its name (routing tests)."""
    for sk in router.SKILLS:
        if sk.name not in PURE_SKILLS:
            monkeypatch.setattr(sk, "handler", lambda ctx, cmd, m, name=sk.name: f"<{name}>")
    monkeypatch.setattr(router.FALLBACK, "handler", lambda ctx, cmd, m: "<ai>")
    monkeypatch.setattr(router.FALLBACK, "name", "ai")


def _no_real_laptop_control(monkeypatch, tmp_path, forbidden):
    """Windows, keys, clipboard, files, settings and media: all faked. Tests opt in with the `win` fixture."""
    from jarvis import actions, desktop
    from jarvis.skills import app_index, files, keyboard_control, media_session, routines, settings_control

    for name in ("list_windows", "active_window", "focus", "show", "close", "is_alive", "type_text", "get_clipboard",
                 "set_clipboard", "start", "start_app_id", "monitor_count"):
        monkeypatch.setattr(desktop, name, forbidden)
    monkeypatch.setattr(desktop, "focused_is_password", lambda: False)
    monkeypatch.setattr(config, "APP_INDEX_FILE", tmp_path / "data" / "app_index.json")
    monkeypatch.setattr(config, "FILE_INDEX_FILE", tmp_path / "data" / "file_index.json")
    monkeypatch.setattr(config, "ROUTINES_FILE", tmp_path / "data" / "routines.json")
    monkeypatch.setattr(config, "MEMORY_FILE", tmp_path / "data" / "memory.json")
    monkeypatch.setattr(config, "MEMORY_REMINDER_HOUR", 9)
    monkeypatch.setattr(config, "TYPE_CONFIRM_CHARS", 200)
    monkeypatch.setattr(app_index, "scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_index, "scan_store_apps", lambda: [])
    monkeypatch.setattr(app_index, "sleep", lambda s: None)
    app_index.reset_cache()
    monkeypatch.setattr(files, "known_folders", lambda: {})
    monkeypatch.setattr(files, "index_roots", lambda: [])
    for name in ("open_path", "trash", "restore_from_bin"):
        monkeypatch.setattr(files, name, forbidden)
    files.reset_index()
    monkeypatch.setattr(keyboard_control, "scroll_by", forbidden)
    monkeypatch.setattr(media_session, "sessions", lambda: [])  # no media session: the media keys are used
    for name in ("radio_states", "set_radio", "get_theme", "set_theme", "set_dnd", "empty_bin", "clear_temp",
                 "get_master", "set_master", "set_mic_mute", "output_devices", "set_output",
                 "open_settings_page", "battery_saver_threshold", "set_battery_saver_threshold", "saved_networks",
                 "connect_wifi", "wifi_details"):
        monkeypatch.setattr(settings_control, name, forbidden)
    monkeypatch.setattr(settings_control, "audio_sessions", lambda: [])  # no app is playing sound
    monkeypatch.setattr(routines, "sleep", lambda s: None)
    routines._ran.clear()
    actions.clear()


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
    # Personality: the original professional wording, always the first variant (friend tests switch it on)
    from jarvis import persona

    monkeypatch.setattr(config, "PERSONALITY", "professional")
    monkeypatch.setattr(config, "NICKNAME", "Anjali")
    monkeypatch.setattr(persona, "_session_mode", None)
    monkeypatch.setattr(persona, "choose", lambda options: options[0])
    monkeypatch.setattr(persona, "AVOID_REPEAT", False)
    monkeypatch.setattr(config, "TTS_ENGINE", "pyttsx3")  # no neural voice downloads
    monkeypatch.setattr(config, "TTS_VOICE", "en-IN-PrabhatNeural")  # Jarvis is male, the user female
    monkeypatch.setattr(config, "VOICE_NAME", "david")
    monkeypatch.setattr(config, "USER_GENDER", "female")
    monkeypatch.setattr(config, "TTS_CACHE_DIR", tmp_path / "data" / "tts_cache")
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
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")  # backup tests switch it on
    from jarvis.skills import browse

    for name in ("ddgs_text", "ddgs_backend", "tavily_search", "download"):  # no real web search in tests
        monkeypatch.setattr(browse, name, forbidden)
    monkeypatch.setattr(browse.webbrowser, "open", forbidden)
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "auto")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(config, "SEARCH_BUDGET", 8.0)
    monkeypatch.setattr(config, "SEARCH_CACHE_MINUTES", 30)
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(config, "GEMINI_ROUTER_TIMEOUT", 8.0)
    monkeypatch.setattr(ai_skill, "_on_backup", False)
    monkeypatch.setattr(ai_skill, "_backup_pending", False)
    monkeypatch.setattr(config, "SAMPLES_DIR", tmp_path / "data" / "samples")
    monkeypatch.setattr(config, "ONE_SEC_AFTER", 0)   # tests that need "One sec." turn it on
    monkeypatch.setattr(config, "CALENDAR_REMINDER_MINUTES", 10)
    monkeypatch.setattr(vscode, "_cache", None)
    geo._CACHE.clear()
    _no_real_laptop_control(monkeypatch, tmp_path, forbidden)
    yield
    from jarvis.skills import notes

    notes.cancel_all_timers()  # a reminder must not fire into a later test (or pop a real toast)
    calendar.stop_reminders()
    from jarvis.skills import briefing

    briefing.stop_scheduler()
