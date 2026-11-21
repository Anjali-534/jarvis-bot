"""Configuration loaded from environment variables / .env (never hard-code secrets here)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:  # python-dotenv missing: plain environment variables still work
    pass

BASE_DIR = Path(__file__).resolve().parent


def _str(name, default=""):
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _bool(name, default):
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "y")


def _int(name, default):
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


def _path(name, default):
    path = Path(os.path.expanduser(_str(name, default)))
    return path if path.is_absolute() else BASE_DIR / path


ASSISTANT_NAME = _str("ASSISTANT_NAME", "Jarvis")
USER_NAME = _str("USER_NAME", "Anjali")

WAKE_WORD_ENABLED = _bool("WAKE_WORD_ENABLED", True)
WAKE_WORDS = [w.strip().lower() for w in _str("WAKE_WORDS", "jarvis,hey jarvis,ok jarvis").split(",") if w.strip()]
FOLLOW_UP_SECONDS = _int("FOLLOW_UP_SECONDS", 20)

SPEECH_LANGUAGE = _str("SPEECH_LANGUAGE", "en-IN")
VOICE_NAME = _str("VOICE_NAME", "zira")
VOICE_RATE = _int("VOICE_RATE", 170)

DEFAULT_LOCATION = _str("DEFAULT_LOCATION", "New Delhi")
COUNTRY_CODE = _str("COUNTRY_CODE", "in")
WAQI_TOKEN = _str("WAQI_TOKEN")
RED_ZONE_THRESHOLD = _int("RED_ZONE_THRESHOLD", 200)

AI_API_KEY = _str("AI_API_KEY") or _str("OPENAI_API_KEY")
AI_BASE_URL = _str("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = _str("AI_MODEL", "gpt-4o-mini")

def _float(name, default):
    try:
        return float(os.getenv(name, "").strip())
    except ValueError:
        return default


NOTES_FILE = _path("NOTES_FILE", "data/notes.txt")
SCREENSHOT_DIR = _path("SCREENSHOT_DIR", "~/Pictures/Jarvis Screenshots")

# WhatsApp: contacts file and how long to wait for the chat to open before pressing Enter
CONTACTS_FILE = _path("CONTACTS_FILE", "data/contacts.json")
WHATSAPP_SEND_DELAY = _float("WHATSAPP_SEND_DELAY", 3.0)

# VS Code: folders scanned for projects (separate several with ;) and manual aliases
PROJECT_ROOTS = [
    Path(os.path.expandvars(os.path.expanduser(p.strip())))
    for p in _str("PROJECT_ROOTS", r"C:\Users\ADMIN\OneDrive\Desktop\ANJALI").split(";")
    if p.strip()
]
PROJECTS_FILE = _path("PROJECTS_FILE", "data/projects.json")
