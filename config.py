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
# friend = casual Delhi Hinglish, warm and a bit witty; professional = the polite assistant
PERSONALITY = _str("PERSONALITY", "friend").lower()
NICKNAME = _str("NICKNAME", USER_NAME)  # what Jarvis calls you in friend mode
USER_GENDER = _str("USER_GENDER", "female").lower()  # Hindi verb forms when Jarvis talks to you (aa gayi / aa gaya)

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

DEFAULT_LOCATION = _str("DEFAULT_LOCATION", "New Delhi")
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

# Live web browsing ("search the web for X", "petrol price in delhi today"): answered by voice from web pages
SEARCH_PROVIDER = _str("SEARCH_PROVIDER", "auto").lower()  # auto = tavily, groq, ddgs; or a list, e.g. "ddgs"
TAVILY_API_KEY = _str("TAVILY_API_KEY")                     # optional: https://app.tavily.com (free tier)
SEARCH_BUDGET = _float("SEARCH_BUDGET", 8.0)                # seconds for searching + reading pages
SEARCH_CACHE_MINUTES = _int("SEARCH_CACHE_MINUTES", 30)
