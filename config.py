"""Configuration: environment variables, then settings.json + the Windows Credential Manager, then a legacy .env.

Where your data lives:
* the installed app: %APPDATA%\\Jarvis (settings.json, memory, notes, contacts, routines, cache, logs, tokens)
* developer mode (--dev, JARVIS_DEV=1, or running from a checkout that already has .env or data/): the repo's
  data/ folder, so an existing setup keeps working. The first dev run migrates .env: secrets go to the
  Credential Manager (and are blanked in .env), everything else to data/settings.json.
JARVIS_HOME=<folder> uses another data folder (a clean test user, a portable install).
Never hard-code secrets or personal details here.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

from jarvis import __version__ as VERSION  # noqa: F401  (one version number, used everywhere)

FROZEN = bool(getattr(sys, "frozen", False))            # the PyInstaller build
REPO_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(getattr(sys, "_MEIPASS", REPO_DIR))     # bundled files: COMMANDS.md, routines.example.json, icons
TESTING = os.getenv("JARVIS_TESTING", "").strip() == "1"


def _dev_mode():
    flag = os.getenv("JARVIS_DEV", "").strip().lower()
    if flag in ("1", "true", "yes") or "--dev" in sys.argv:
        return True
    if flag in ("0", "false", "no"):
        return False  # behave like the installed app, even from a checkout
    if FROZEN or os.getenv("JARVIS_HOME"):
        return False
    return (REPO_DIR / ".env").exists() or (REPO_DIR / "data").is_dir()


DEV = _dev_mode()
APPDATA_DIR = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / "Jarvis"
DATA_DIR = Path(os.getenv("JARVIS_HOME")) if os.getenv("JARVIS_HOME") else (REPO_DIR / "data" if DEV else APPDATA_DIR)
SETTINGS_FILE = DATA_DIR / "settings.json"
ENV_FILE = (REPO_DIR / ".env") if DEV else (DATA_DIR / ".env")  # legacy / power-user overrides


def migrate_env(env_file=None, data_dir=None):
    """First dev run: .env -> settings.json (+ secrets -> Credential Manager, blanked in .env). Returns what moved."""
    from jarvis import settings_store, vault

    env_file, data_dir = Path(env_file or ENV_FILE), Path(data_dir or DATA_DIR)
    if not env_file.exists() or (data_dir / "settings.json").exists():
        return None
    try:
        from dotenv import dotenv_values

        values = {k: v for k, v in dotenv_values(env_file).items() if v not in (None, "")}
    except ImportError:
        return None
    secrets, settings = {}, {}
    for key, value in values.items():
        (secrets if key in vault.SECRET_KEYS else settings)[key] = value
    moved = []
    for key, value in secrets.items():
        try:
            vault.set(key, value)
            moved.append(key)
        except Exception:
            settings.setdefault("_vault_failed", []).append(key)  # keeps working from .env
    settings["_migrated_from_env"] = datetime.now().isoformat(timespec="seconds")
    settings_store.save(settings, data_dir)
    if moved:  # blank the plain-text copies now that they're in the Credential Manager
        lines = env_file.read_text(encoding="utf-8").splitlines()
        out = [f"{line.split('=', 1)[0]}=  # moved to Windows Credential Manager ({vault.service()})"
               if "=" in line and line.split("=", 1)[0].strip() in moved else line for line in lines]
        env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"settings": sorted(settings), "secrets": moved}


def _load():
    """Fill os.environ (only names the real environment doesn't set) from settings.json, the vault, then .env."""
    if TESTING:
        return
    from jarvis import settings_store, vault

    if DEV:
        try:
            migrate_env()
        except Exception as exc:  # a broken migration must never stop Jarvis starting
            print(f"[config] .env migration failed: {exc}", file=sys.stderr)
    for key, value in settings_store.load(DATA_DIR).items():
        if not key.startswith("_") and value is not None:
            os.environ.setdefault(key, settings_store.as_env(value))
    for key, value in vault.all_secrets().items():
        os.environ.setdefault(key, value)
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
    except ImportError:
        pass


_load()


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
    """A file setting: "data/x" means the data folder; other relative paths are relative to it too."""
    raw = os.path.expanduser(os.path.expandvars(_str(name, default)))
    if raw.replace("\\", "/").startswith("data/"):
        raw = raw[5:]
    path = Path(raw)
    return path if path.is_absolute() else DATA_DIR / path


ASSISTANT_NAME = _str("ASSISTANT_NAME", "Jarvis")
USER_NAME = _str("USER_NAME")  # empty until the setup wizard asks
# friend = casual and warm, a bit witty; professional = the polite assistant
PERSONALITY = _str("PERSONALITY", "friend").lower()
NICKNAME = _str("NICKNAME", USER_NAME)  # what Jarvis calls you in friend mode
# male / female / neutral: the Hindi verb forms Jarvis uses for you (aa gaya / aa gayi; neutral avoids them)
USER_GENDER = _str("USER_GENDER", "neutral").lower()
LANGUAGE = _str("LANGUAGE", "english").lower()  # english, or hinglish (casual Hindi-English in Roman letters)

WAKE_WORD_ENABLED = _bool("WAKE_WORD_ENABLED", True)
WAKE_WORDS = [w.strip().lower() for w in _str("WAKE_WORDS", "jarvis,hey jarvis,ok jarvis").split(",") if w.strip()]
FOLLOW_UP_SECONDS = _int("FOLLOW_UP_SECONDS", 20)

SPEECH_LANGUAGE = _str("SPEECH_LANGUAGE", "en-IN")
VOICE_NAME = _str("VOICE_NAME", "david")   # pyttsx3 (Windows) voice: the offline fallback
VOICE_RATE = _int("VOICE_RATE", 185)
# edge = Microsoft's free neural voices via edge-tts (needs internet; falls back to pyttsx3); pyttsx3 = offline only
TTS_ENGINE = _str("TTS_ENGINE", "edge").lower()
TTS_VOICE = _str("TTS_VOICE", "en-IN-PrabhatNeural")  # male; clearest male voice in the Hinglish tests
TTS_RATE = _str("TTS_RATE", "+5%")                 # edge-tts speaking rate, e.g. -10%, +0%, +15%
TTS_TIMEOUT = float(_str("TTS_TIMEOUT", "4"))      # seconds to wait for edge-tts before using pyttsx3

DEFAULT_LOCATION = _str("DEFAULT_LOCATION")  # your city or pincode (the setup wizard asks); empty = ask


def _coordinate(name):
    try:
        return float(os.getenv(name, "").strip())
    except ValueError:
        return None


HOME_LAT, HOME_LON = _coordinate("HOME_LAT"), _coordinate("HOME_LON")  # saved when the wizard checks your city
COUNTRY_CODE = _str("COUNTRY_CODE", "in")
WAQI_TOKEN = _str("WAQI_TOKEN")
RED_ZONE_THRESHOLD = _int("RED_ZONE_THRESHOLD", 200)

AI_API_KEY = _str("AI_API_KEY") or _str("OPENAI_API_KEY")
AI_BASE_URL = _str("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = _str("AI_MODEL", "gpt-4o-mini")
# gemini = Google's native Gemini API (works with both AQ. and AIza keys); openai = any OpenAI-compatible API.
# Empty: gemini if AI_BASE_URL is a generativelanguage.googleapis.com URL, otherwise openai.
AI_PROVIDER = _str("AI_PROVIDER").lower()
AI_TIMEOUT = _int("AI_TIMEOUT", 15)
# Reasoning models (Groq's gpt-oss / qwen3): "low" or "none" keeps answers fast. Empty: not sent.
AI_REASONING_EFFORT = _str("AI_REASONING_EFFORT").lower()
# If AI_MODEL is retired, switch to the first of these the provider still offers (then any chat model)
AI_MODEL_FALLBACKS = [m.strip() for m in _str(
    "AI_MODEL_FALLBACKS", "qwen/qwen3.8-27b,openai/gpt-oss-20b,openai/gpt-oss-120b").split(",") if m.strip()]
ENV_FILE = BASE_DIR / ".env"  # Jarvis rewrites only its AI_MODEL line after switching models


def _float(name, default):
    try:
        return float(os.getenv(name, "").strip())
    except ValueError:
        return default


FILE_INDEX_HOURS = _float("FILE_INDEX_HOURS", 6.0)    # how often the file-name index is rebuilt
TYPE_CONFIRM_CHARS = _int("TYPE_CONFIRM_CHARS", 200)   # "type ..." longer than this asks first

NOTES_FILE = _path("NOTES_FILE", "data/notes.txt")
SCREENSHOT_DIR = _path("SCREENSHOT_DIR", "~/Pictures/Jarvis Screenshots")

# WhatsApp: contacts file and how long to wait for the chat to open before pressing Enter
CONTACTS_FILE = _path("CONTACTS_FILE", "data/contacts.json")
WHATSAPP_SEND_DELAY = _float("WHATSAPP_SEND_DELAY", 3.0)

# VS Code: folders scanned for projects (separate several with ;) and manual aliases
def _default_project_roots():
    """Your Desktop and Documents (wherever Windows keeps them, OneDrive included)."""
    try:
        from win32com.shell import shell, shellcon

        roots = [shell.SHGetKnownFolderPath(shellcon.FOLDERID_Desktop),
                 shell.SHGetKnownFolderPath(shellcon.FOLDERID_Documents)]
    except Exception:
        roots = [str(Path.home() / "Desktop"), str(Path.home() / "Documents")]
    return ";".join(roots)


PROJECT_ROOTS = [
    Path(os.path.expandvars(os.path.expanduser(p.strip())))
    for p in (_str("PROJECT_ROOTS") or ("" if TESTING else _default_project_roots())).split(";")
    if p.strip()
]
PROJECTS_FILE = _path("PROJECTS_FILE", "data/projects.json")

# Laptop control: the installed-apps index, the file-name index, routines and the action log
APP_INDEX_FILE = _path("APP_INDEX_FILE", "data/app_index.json")      # refreshed daily and on "refresh apps"
FILE_INDEX_FILE = _path("FILE_INDEX_FILE", "data/file_index.json")   # names + modified dates only, never contents
ROUTINES_FILE = _path("ROUTINES_FILE", "data/routines.json")
# Telegram phone remote: a bot token from @BotFather turns it on; only the paired chat can control Jarvis
TELEGRAM_BOT_TOKEN = _str("TELEGRAM_BOT_TOKEN")
TELEGRAM_FILE = _path("TELEGRAM_FILE", "data/telegram.json")         # the paired chat id (git-ignored)
TELEGRAM_ALLOW_KEYBOARD = _bool("TELEGRAM_ALLOW_KEYBOARD", False)    # typing / keys / clipboard from the phone
TELEGRAM_CONFIRM_SECONDS = _int("TELEGRAM_CONFIRM_SECONDS", 60)      # [Yes] [No] buttons expire after this
TELEGRAM_NOTIFY = _bool("TELEGRAM_NOTIFY", False)  # reminders, timers, calendar, memory dates, low battery -> phone
TELEGRAM_MAX_FILE_MB = _int("TELEGRAM_MAX_FILE_MB", 50)               # Telegram bots can send up to 50 MB
MEMORY_FILE = _path("MEMORY_FILE", "data/memory.json")               # long-term memory (git-ignored)
MEMORY_REMINDER_HOUR = _int("MEMORY_REMINDER_HOUR", 9)               # dated memories are spoken at this hour         # created from routines.example.json
ROUTINES_EXAMPLE = BASE_DIR / "routines.example.json"

# Background mode
HOTKEY = _str("HOTKEY", "ctrl+alt+j")          # wakes Jarvis instantly; empty to disable
NOTIFY = _bool("NOTIFY", True)                  # Windows toasts for reminders and timers
LOG_DIR = _path("LOG_DIR", "data/logs")

# Google (Gmail + Calendar): OAuth client from Google Cloud Console, and where the sign-in token is saved
GOOGLE_CREDENTIALS_FILE = _path("GOOGLE_CREDENTIALS_FILE", "data/google_credentials.json")
GOOGLE_TOKEN_FILE = _path("GOOGLE_TOKEN_FILE", "data/google_token.json")
TIMEZONE = "Asia/Kolkata"                       # calendar times are spoken in IST
CALENDAR_REMINDERS = _bool("CALENDAR_REMINDERS", True)  # spoken + toast reminder before each event
CALENDAR_REMINDER_MINUTES = _int("CALENDAR_REMINDER_MINUTES", 10)

# Spotify: client ID of your app at developer.spotify.com (PKCE flow, no client secret needed)
SPOTIFY_CLIENT_ID = _str("SPOTIFY_CLIENT_ID")
SPOTIFY_REDIRECT_URI = _str("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_TOKEN_FILE = _path("SPOTIFY_TOKEN_FILE", "data/spotify_token.json")

# Speed: offline wake word, voice activity detection, speech-to-text engine, caching
WAKE_SENSITIVITY = _float("WAKE_SENSITIVITY", 0.5)      # openWakeWord "hey jarvis" score needed (0-1)
STT_ENGINE = _str("STT_ENGINE", "google").lower()        # google | whisper (faster-whisper, local)
LISTEN_TIMEOUT = _float("LISTEN_TIMEOUT", 5.0)           # seconds to wait for you to start talking
PHRASE_LIMIT = _float("PHRASE_LIMIT", 7.0)               # longest single command, seconds
END_SILENCE_MS = _int("END_SILENCE_MS", 600)             # this much silence ends the command
ONE_SEC_AFTER = _float("ONE_SEC_AFTER", 1.5)             # say "One sec" if a skill takes longer than this
CACHE_FILE = _path("CACHE_FILE", "data/cache.json")
TTS_CACHE_DIR = _path("TTS_CACHE_DIR", "data/tts_cache")  # short phrases spoken often, as mp3
CACHE_MINUTES = _int("CACHE_MINUTES", 10)                # weather and AQI
# Daily briefing ("good morning Jarvis")
BRIEFING_SECTIONS = [s.strip().lower() for s in _str(
    "BRIEFING_SECTIONS", "weather,aqi,calendar,email,reminders,news").split(",") if s.strip()]
BRIEFING_TIME = _str("BRIEFING_TIME")           # HH:MM: brief me automatically once a day when idle; empty = off
BRIEFING_MUSIC = _str("BRIEFING_MUSIC")         # e.g. "lofi beats": played on Spotify after the briefing
SAMPLES_DIR = _path("SAMPLES_DIR", "data/samples")       # "record samples" saves WAVs here for the benchmark

# Natural-language actions: when no command pattern matches, the AI may pick a skill (tool calling)
AI_ROUTING = _bool("AI_ROUTING", True)
AI_ROUTER_MODEL = _str("AI_ROUTER_MODEL")             # empty = AI_MODEL
AI_ROUTER_TIMEOUT = _float("AI_ROUTER_TIMEOUT", 4.0)  # seconds; on timeout it's treated as normal chat

# Backup AI: if the main AI (Groq) is rate-limited, down, times out or rejects its key/model, the same request
# is retried once on Gemini. After Groq's DAILY limit, Gemini goes first until midnight IST. Empty key = no backup.
GEMINI_API_KEY = _str("GEMINI_API_KEY")
GEMINI_MODEL = _str("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_ROUTER_TIMEOUT = _float("GEMINI_ROUTER_TIMEOUT", 8.0)  # Gemini is slower at picking an action than Groq

# Live web browsing ("search the web for X", "petrol price today"): answered by voice from web pages
SEARCH_PROVIDER = _str("SEARCH_PROVIDER", "auto").lower()  # auto = tavily, groq, ddgs; or a list, e.g. "ddgs"
TAVILY_API_KEY = _str("TAVILY_API_KEY")                     # optional: https://app.tavily.com (free tier)
SEARCH_BUDGET = _float("SEARCH_BUDGET", 8.0)                # seconds for searching + reading pages
SEARCH_CACHE_MINUTES = _int("SEARCH_CACHE_MINUTES", 30)
