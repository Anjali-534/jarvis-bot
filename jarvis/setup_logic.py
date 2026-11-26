"""Everything the setup wizard and the Settings window DO, without drawing anything (so it can be tested).

The GUI (jarvis/gui/setup_window.py) only collects values and shows results; validation, key tests, voice
samples, microphone checks, saving and "reset everything" all live here.
"""
import json
import logging
import re
import shutil
import threading
from pathlib import Path

import config
from jarvis import settings_store, vault

log = logging.getLogger(__name__)

GENDERS = {"female": "Female", "male": "Male", "neutral": "Prefer not to say (neutral)"}
LANGUAGES = {"english": "English", "hinglish": "Hinglish (Hindi + English, in English letters)"}
PERSONALITIES = {"friend": "Friend (casual, warm)", "professional": "Professional (polite assistant)"}

# Microsoft's free neural voices (edge-tts; needs internet). The offline Windows voices are added at runtime.
VOICES = [
    ("en-IN-PrabhatNeural", "Prabhat (Indian English, male)", "male"),
    ("en-IN-NeerjaNeural", "Neerja (Indian English, female)", "female"),
    ("hi-IN-MadhurNeural", "Madhur (Hindi, male)", "male"),
    ("hi-IN-SwaraNeural", "Swara (Hindi, female)", "female"),
    ("en-US-GuyNeural", "Guy (US English, male)", "male"),
    ("en-US-JennyNeural", "Jenny (US English, female)", "female"),
    ("en-GB-RyanNeural", "Ryan (British English, male)", "male"),
    ("en-GB-SoniaNeural", "Sonia (British English, female)", "female"),
]
SAMPLE_TEXT = "Hi {name}! I'm {assistant}. This is how I sound."

AI_PROVIDERS = {
    "groq": {"label": "Groq (free, fastest, recommended)", "key_url": "https://console.groq.com/keys",
             "settings": {"AI_PROVIDER": "openai", "AI_BASE_URL": "https://api.groq.com/openai/v1",
                          "AI_MODEL": "qwen/qwen3.8-27b", "AI_REASONING_EFFORT": "none"},
             "key_pattern": r"^gsk_[A-Za-z0-9]{20,}$"},
    "gemini": {"label": "Google Gemini (free)", "key_url": "https://aistudio.google.com/apikey",
               "settings": {"AI_PROVIDER": "gemini", "AI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta",
                            "AI_MODEL": "gemini-3.5-flash-lite", "AI_REASONING_EFFORT": ""},
               "key_pattern": r"^(?:AIza[0-9A-Za-z_-]{20,}|AQ\.[0-9A-Za-z_.-]{20,})$"},
    "openai": {"label": "OpenAI (paid)", "key_url": "https://platform.openai.com/api-keys",
               "settings": {"AI_PROVIDER": "openai", "AI_BASE_URL": "https://api.openai.com/v1",
                            "AI_MODEL": "gpt-4o-mini", "AI_REASONING_EFFORT": ""},
               "key_pattern": r"^sk-[A-Za-z0-9_-]{20,}$"},
}
KEY_PAGES = {
    "waqi": "https://aqicn.org/data-platform/token/",
    "tavily": "https://app.tavily.com",
    "telegram": "https://t.me/BotFather",
    "google": "https://console.cloud.google.com/apis/credentials",
    "spotify": "https://developer.spotify.com/dashboard",
}
NAME = re.compile(r"^[\w .'-]{1,40}$", re.UNICODE)


class Problem(ValueError):
    """A value the user must fix: str(problem) is shown next to the field."""


# ---------------------------------------------------------------- step 1: you
def validate_profile(values, geocode=None):
    """{"USER_NAME", "NICKNAME", "USER_GENDER", "DEFAULT_LOCATION", "LANGUAGE"} -> (settings, errors).

    The city/pincode is checked on the map: HOME_LAT/HOME_LON are saved, so Jarvis never has to look it up again.
    """
    from jarvis import geo

    geocode = geocode or geo.geocode
    errors, clean = {}, {}
    name = (values.get("USER_NAME") or "").strip()
    if not name:
        errors["USER_NAME"] = "Please type your name."
    elif not NAME.match(name):
        errors["USER_NAME"] = "Just letters, spaces, dots and dashes, please (up to 40)."
    else:
        clean["USER_NAME"] = name
    nickname = (values.get("NICKNAME") or "").strip() or name
    if nickname and not NAME.match(nickname):
        errors["NICKNAME"] = "Just letters, spaces, dots and dashes, please."
    elif nickname:
        clean["NICKNAME"] = nickname
    gender = (values.get("USER_GENDER") or "neutral").strip().lower()
    if gender not in GENDERS:
        errors["USER_GENDER"] = "Pick one of the options."
    else:
        clean["USER_GENDER"] = gender
    language = (values.get("LANGUAGE") or "english").strip().lower()
    if language not in LANGUAGES:
        errors["LANGUAGE"] = "Pick English or Hinglish."
    else:
        clean["LANGUAGE"] = language
    place_text = (values.get("DEFAULT_LOCATION") or "").strip()
    if not place_text:
        errors["DEFAULT_LOCATION"] = "Type your city or pincode (for weather and air quality)."
    elif re.fullmatch(r"\d+", place_text) and len(place_text) != 6:
        errors["DEFAULT_LOCATION"] = "An Indian pincode has 6 digits."
    else:
        try:
            place = geocode(place_text)
        except Exception as exc:  # offline: keep it, Jarvis will look it up later
            log.info("[setup] couldn't check %s: %s", place_text, exc)
            place = None
            clean["DEFAULT_LOCATION"] = place_text
            clean["_location_unchecked"] = True
        else:
            if place is None:
                errors["DEFAULT_LOCATION"] = f"I couldn't find {place_text} on the map. Try a nearby city or a pincode."
            else:
                clean.update(DEFAULT_LOCATION=place_text, HOME_LAT=round(place.lat, 4), HOME_LON=round(place.lon, 4))
                clean["_place_name"] = place.name
    return clean, errors


# ---------------------------------------------------------------- step 2: the assistant
def validate_assistant(values):
    errors, clean = {}, {}
    name = (values.get("ASSISTANT_NAME") or "Jarvis").strip()
    if not NAME.match(name) or len(name) < 2:
        errors["ASSISTANT_NAME"] = "A short name, please (2 to 40 letters)."
    else:
        clean["ASSISTANT_NAME"] = name
        clean["WAKE_WORDS"] = ",".join(dict.fromkeys([name.lower(), f"hey {name.lower()}", f"ok {name.lower()}"]))
    personality = (values.get("PERSONALITY") or "friend").lower()
    if personality not in PERSONALITIES:
        errors["PERSONALITY"] = "Pick friend or professional."
    else:
        clean["PERSONALITY"] = personality
    voice = (values.get("TTS_VOICE") or "").strip()
    offline = voice.startswith("offline:")
    if offline:
        clean.update(TTS_ENGINE="pyttsx3", VOICE_NAME=voice.split(":", 1)[1])
    elif voice in {v for v, _, _ in VOICES} or re.fullmatch(r"[a-z]{2}-[A-Z]{2}-\w+Neural", voice):
        clean.update(TTS_ENGINE="edge", TTS_VOICE=voice)
    else:
        errors["TTS_VOICE"] = "Pick a voice."
    clean["WAKE_WORD_ENABLED"] = bool(values.get("WAKE_WORD_ENABLED", True))
    if not clean["WAKE_WORD_ENABLED"] and name.lower() != "jarvis":
        pass  # no wake word: the hotkey still works
    if name.lower() != "jarvis" and clean.get("WAKE_WORD_ENABLED"):
        clean["_wake_note"] = ("The offline wake word only knows 'Hey Jarvis'. For other names Jarvis listens "
                               "for the name in what you say (a bit slower).")
    return clean, errors


def voice_gender(voice):
    for name, _, gender in VOICES:
        if name == voice:
            return gender
    return "male" if any(m in voice.lower() for m in ("david", "mark", "ravi", "hemant", "guy", "ryan")) else "female"


def offline_voices():
    """[("offline:<name>", label)] for the Windows (SAPI) voices, which work without internet."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        found = []
        for v in voices:
            short = re.sub(r"^Microsoft\s+|\s+Desktop.*$|\s+-.*$", "", v.name).strip()
            found.append((f"offline:{short.lower()}", f"{short} (offline Windows voice)"))
        engine.stop()
        return found
    except Exception:
        log.debug("no offline voices", exc_info=True)
        return []


def play_sample(voice, name="", assistant="Jarvis", stop=None):
    """Say the sample sentence in this voice (neural via edge-tts, or an offline Windows voice)."""
    text = SAMPLE_TEXT.format(name=name or "there", assistant=assistant)
    if voice.startswith("offline:"):
        import pyttsx3

        engine = pyttsx3.init()
        wanted = voice.split(":", 1)[1]
        for v in engine.getProperty("voices"):
            if wanted in v.name.lower():
                engine.setProperty("voice", v.id)
                break
        engine.say(text)
        engine.runAndWait()
        return True
    from jarvis.tts import EdgeTTS, Player

    audio = EdgeTTS(voice=voice).synth(text, timeout=10)
    return Player().play(audio, stop or threading.Event())


# ---------------------------------------------------------------- step 3: microphone
def microphones():
    from jarvis import audio

    return [name for _, name in audio.input_devices()]


def level(pcm):
    """0..1 loudness of 16-bit PCM (for the live meter)."""
    import array
    import math

    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return max(0.0, min(1.0, math.log10(1 + rms) / 4.0))


class MicTest:
    """Open the chosen microphone, report levels, and record a few seconds for a speech test."""

    def __init__(self, device_name=""):
        self.device_name = device_name
        self.stream = None

    def open(self):
        from jarvis import audio

        old = config.MIC_DEVICE
        config.MIC_DEVICE = self.device_name
        try:
            self.stream = audio.MicStream()
        finally:
            config.MIC_DEVICE = old
        return self

    def read_level(self):
        return level(self.stream.read())

    def record(self, seconds=3.0):
        frames = int(seconds * 1000 / 20)
        return b"".join(self.stream.read() for _ in range(frames))

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def transcribe(pcm, language="en-IN"):
    from jarvis import stt

    return (stt.make(language=language).transcribe(pcm, language=language) or "").strip()


# ---------------------------------------------------------------- step 4: AI
def ai_settings(provider, key):
    """The settings for a provider + key (validated). Raises Problem."""
    if provider in ("", "skip", None):
        return {"AI_API_KEY": ""}
    if provider not in AI_PROVIDERS:
        raise Problem("Pick Groq, Gemini or OpenAI, or skip.")
    key = (key or "").strip()
    if not key:
        raise Problem("Paste your API key (or skip this step).")
    if not re.match(AI_PROVIDERS[provider]["key_pattern"], key):
        raise Problem(f"That doesn't look like a {AI_PROVIDERS[provider]['label'].split(' (')[0]} key. "
                      "Copy the whole key again.")
    return {**AI_PROVIDERS[provider]["settings"], "AI_API_KEY": key}


def test_ai_key(provider, key, get=None):
    """(ok, message): ask the provider for its model list with this key."""
    from jarvis import http

    get = get or (lambda url, headers: http.session().get(url, headers=headers, timeout=10))
    try:
        settings = ai_settings(provider, key)
    except Problem as exc:
        return False, str(exc)
    base = settings["AI_BASE_URL"]
    headers = ({"x-goog-api-key": key} if settings["AI_PROVIDER"] == "gemini" else {"Authorization": f"Bearer {key}"})
    try:
        response = get(f"{base}/models", headers)
    except Exception as exc:
        return False, f"Couldn't reach {provider}: {exc.__class__.__name__}. Check your internet."
    if response.status_code in (401, 403):
        return False, "The key was rejected. Copy it again, or make a new one."
    if response.status_code >= 400:
        return False, f"{provider} answered with an error ({response.status_code}). Try again in a minute."
    return True, "The key works."


# ---------------------------------------------------------------- step 5: optional connections
def test_telegram_token(token, get=None):
    from jarvis import http

    token = (token or "").strip()
    if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", token):
        return False, "A bot token looks like 123456789:AAH... Copy it from @BotFather."
    get = get or (lambda url: http.session().get(url, timeout=10))
    try:
        response = get(f"https://api.telegram.org/bot{token}/getMe")
        data = response.json()
    except Exception as exc:
        return False, f"Couldn't reach Telegram: {exc.__class__.__name__}."
    if not data.get("ok"):
        return False, "Telegram rejected that token."
    return True, f"Connected to @{data['result'].get('username', 'your bot')}."


def test_waqi_token(token, get=None):
    from jarvis import http

    token = (token or "").strip()
    if not re.fullmatch(r"[0-9a-f]{20,64}", token):
        return False, "A WAQI token is a long string of letters a-f and numbers."
    get = get or (lambda url, params: http.session().get(url, params=params, timeout=10))
    try:
        data = get("https://api.waqi.info/feed/here/", {"token": token}).json()
    except Exception as exc:
        return False, f"Couldn't reach WAQI: {exc.__class__.__name__}."
    return (True, "The token works.") if data.get("status") == "ok" else (False, "WAQI rejected that token.")


def test_tavily_key(key, post=None):
    from jarvis import http

    key = (key or "").strip()
    if not key.startswith("tvly-"):
        return False, "A Tavily key starts with tvly-."
    post = post or (lambda url, payload: http.post_json(url, payload, timeout=15))
    try:
        response = post("https://api.tavily.com/search", {"api_key": key, "query": "test", "max_results": 1})
    except Exception as exc:
        return False, f"Couldn't reach Tavily: {exc.__class__.__name__}."
    return (True, "The key works.") if response.status_code < 400 else (False, "Tavily rejected that key.")


def validate_spotify_client_id(client_id):
    client_id = (client_id or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", client_id):
        return False, "A Spotify client ID is 32 letters a-f and numbers, from your app's dashboard."
    return True, "Looks right. Say 'connect spotify' after setup to sign in."


def import_google_credentials(source):
    """Copy an OAuth client JSON (Desktop app) into the data folder. Returns (ok, message)."""
    source = Path(source)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "That isn't a readable JSON file."
    client = data.get("installed")
    if not client or not client.get("client_id") or not client.get("client_secret"):
        return False, "That's not a Desktop app OAuth client. Create one of type 'Desktop app' and download it."
    target = config.GOOGLE_CREDENTIALS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True, "Saved. Say 'connect google' after setup to sign in."


# ---------------------------------------------------------------- saving, startup, reset
def save(values):
    """Save settings (secrets to the Credential Manager). Private keys (starting with _) are dropped."""
    clean = {k: v for k, v in values.items() if not k.startswith("_")}
    start = clean.pop("START_WITH_WINDOWS", None)
    settings_store.update(clean)
    if start is not None:
        set_start_with_windows(bool(start))
    return clean


def set_start_with_windows(on):
    from jarvis import startup

    try:
        if on and not startup.is_installed():
            startup.install()
        elif not on and startup.is_installed():
            startup.uninstall()
        return True
    except Exception as exc:
        log.warning("[setup] couldn't change start with Windows: %s", exc)
        return False


def finish(values):
    saved = save(values)
    settings_store.mark_setup_done()
    return saved


def current_values():
    """What the Settings window shows: settings.json plus whether each secret is set (masked, never the value)."""
    values = settings_store.load()
    for key in vault.SECRET_KEYS:
        secret = vault.get(key)
        values[f"_has_{key}"] = bool(secret)
        values[f"_mask_{key}"] = vault.mask(secret)
    return values


def reset_everything(data_dir=None):
    """Delete every setting, memory, note, token and secret Jarvis has (asks first in the GUI)."""
    data_dir = Path(data_dir or config.DATA_DIR)
    for key in vault.SECRET_KEYS:
        vault.delete(key)
    set_start_with_windows(False)
    removed = 0
    if data_dir.exists():
        for item in data_dir.iterdir():
            try:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
                removed += 1
            except OSError as exc:
                log.warning("[setup] couldn't remove %s: %s", item, exc)
    return removed


def open_data_folder():
    import os

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.startfile(str(config.DATA_DIR))


def open_page(url):
    import webbrowser

    webbrowser.open(url)


def needs_setup():
    """First run: no SETUP_DONE in settings.json (a migrated dev setup counts as done)."""
    return not settings_store.done_setup()
