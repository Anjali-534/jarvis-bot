"""The setup wizard's logic (jarvis/setup_logic.py), not its drawing: validation, key tests, saving, reset, and
that Jarvis still does the everyday things with no keys at all."""
import json
from datetime import datetime

import pytest
from helpers import make_ctx
from test_multi_user import MemoryKeyring  # noqa: F401  (the fake Credential Manager)

import config
from jarvis import geo, settings_store, setup_logic as logic, vault
from jarvis.router import handle


@pytest.fixture
def keyring(monkeypatch):
    import keyring as real

    fake = MemoryKeyring()
    monkeypatch.setenv("JARVIS_NO_VAULT", "0")
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(real, name, getattr(fake, name))
    return fake


class Response:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}

    def json(self):
        return self._data


PUNE = geo.Place("Pune", 18.5204, 73.8567)


# ---------------------------------------------------------------- step 1
def test_a_good_profile(monkeypatch):
    clean, errors = logic.validate_profile({"USER_NAME": " Sam ", "NICKNAME": "", "USER_GENDER": "male",
                                            "DEFAULT_LOCATION": "Pune", "LANGUAGE": "hinglish"},
                                           geocode=lambda q: PUNE)
    assert errors == {}
    assert clean["USER_NAME"] == "Sam" and clean["NICKNAME"] == "Sam"  # the nickname defaults to the name
    assert clean["HOME_LAT"] == 18.5204 and clean["HOME_LON"] == 73.8567 and clean["_place_name"] == "Pune"


@pytest.mark.parametrize("values, field, message", [
    ({"USER_NAME": ""}, "USER_NAME", "type your name"),
    ({"USER_NAME": "x" * 50}, "USER_NAME", "up to 40"),
    ({"USER_NAME": "Sam", "USER_GENDER": "other"}, "USER_GENDER", "Pick one"),
    ({"USER_NAME": "Sam", "LANGUAGE": "french"}, "LANGUAGE", "English or Hinglish"),
    ({"USER_NAME": "Sam", "DEFAULT_LOCATION": ""}, "DEFAULT_LOCATION", "city or pincode"),
    ({"USER_NAME": "Sam", "DEFAULT_LOCATION": "1100"}, "DEFAULT_LOCATION", "6 digits"),
    ({"USER_NAME": "Sam", "DEFAULT_LOCATION": "Atlantis"}, "DEFAULT_LOCATION", "couldn't find Atlantis"),
])
def test_profile_problems(values, field, message):
    clean, errors = logic.validate_profile(values, geocode=lambda q: None)
    assert message.lower() in errors[field].lower()


def test_offline_city_is_kept_and_checked_later():
    def offline(query):
        raise ConnectionError("no internet")

    clean, errors = logic.validate_profile({"USER_NAME": "Sam", "DEFAULT_LOCATION": "Pune"}, geocode=offline)
    assert errors == {} and clean["DEFAULT_LOCATION"] == "Pune" and "HOME_LAT" not in clean


# ---------------------------------------------------------------- step 2
def test_assistant_name_sets_the_wake_words():
    clean, errors = logic.validate_assistant({"ASSISTANT_NAME": "Friday", "TTS_VOICE": "en-US-JennyNeural",
                                              "PERSONALITY": "professional", "WAKE_WORD_ENABLED": True})
    assert errors == {} and clean["WAKE_WORDS"] == "friday,hey friday,ok friday"
    assert clean["TTS_ENGINE"] == "edge" and clean["TTS_VOICE"] == "en-US-JennyNeural"
    assert "only knows 'Hey Jarvis'" in clean["_wake_note"]


def test_offline_voice_and_bad_choices():
    clean, _ = logic.validate_assistant({"TTS_VOICE": "offline:zira", "PERSONALITY": "friend"})
    assert clean["TTS_ENGINE"] == "pyttsx3" and clean["VOICE_NAME"] == "zira"
    _, errors = logic.validate_assistant({"ASSISTANT_NAME": "J", "TTS_VOICE": "nope", "PERSONALITY": "rude"})
    assert set(errors) == {"ASSISTANT_NAME", "TTS_VOICE", "PERSONALITY"}


def test_voice_genders():
    assert logic.voice_gender("en-IN-NeerjaNeural") == "female" and logic.voice_gender("hi-IN-MadhurNeural") == "male"


# ---------------------------------------------------------------- step 3
def test_level_meter():
    import array

    assert logic.level(b"") == 0.0
    assert logic.level(array.array("h", [0] * 320).tobytes()) == 0.0
    loud = logic.level(array.array("h", [12000, -12000] * 160).tobytes())
    quiet = logic.level(array.array("h", [300, -300] * 160).tobytes())
    assert 0 < quiet < loud <= 1.0


# ---------------------------------------------------------------- step 4
def test_ai_settings_per_provider():
    groq = logic.ai_settings("groq", " gsk_" + "a" * 40 + " ")
    assert groq["AI_BASE_URL"] == "https://api.groq.com/openai/v1" and groq["AI_API_KEY"] == "gsk_" + "a" * 40
    gemini = logic.ai_settings("gemini", "AIza" + "b" * 35)
    assert gemini["AI_PROVIDER"] == "gemini"
    assert logic.ai_settings("skip", "") == {"AI_API_KEY": ""}
    with pytest.raises(logic.Problem, match="doesn't look like a Groq key"):
        logic.ai_settings("groq", "sk-" + "c" * 40)
    with pytest.raises(logic.Problem, match="Paste your API key"):
        logic.ai_settings("openai", "")


def test_key_test_button():
    key = "gsk_" + "a" * 40
    seen = []
    assert logic.test_ai_key("groq", key, get=lambda url, headers: seen.append((url, headers)) or Response(200)) == \
        (True, "The key works.")
    assert seen == [("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})]
    assert logic.test_ai_key("groq", key, get=lambda url, headers: Response(401))[0] is False
    assert "Check your internet" in logic.test_ai_key("groq", key, get=lambda url, headers: 1 / 0)[1]
    gemini = []
    logic.test_ai_key("gemini", "AIza" + "b" * 35, get=lambda url, headers: gemini.append(headers) or Response(200))
    assert "x-goog-api-key" in gemini[0]


# ---------------------------------------------------------------- step 5
def test_telegram_waqi_tavily_spotify_checks():
    token = "123456789:" + "A" * 35
    assert logic.test_telegram_token("nope")[0] is False
    ok, message = logic.test_telegram_token(token, get=lambda url: Response(200, {"ok": True,
                                                                                  "result": {"username": "my_bot"}}))
    assert ok and message == "Connected to @my_bot."
    assert logic.test_telegram_token(token, get=lambda url: Response(401, {"ok": False}))[0] is False
    assert logic.test_waqi_token("a" * 40, get=lambda url, params: Response(200, {"status": "ok"}))[0]
    assert logic.test_waqi_token("not a token")[0] is False
    assert logic.test_tavily_key("tvly-abc", post=lambda url, payload: Response(200))[0]
    assert logic.test_tavily_key("abc")[0] is False
    assert logic.validate_spotify_client_id("0123456789abcdef0123456789abcdef")[0]
    assert logic.validate_spotify_client_id("short")[0] is False


def test_google_credentials_import(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CREDENTIALS_FILE", tmp_path / "data" / "google_credentials.json")
    good = tmp_path / "client.json"
    good.write_text(json.dumps({"installed": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s"}}))
    assert logic.import_google_credentials(good)[0] and config.GOOGLE_CREDENTIALS_FILE.exists()
    web = tmp_path / "web.json"
    web.write_text(json.dumps({"web": {"client_id": "x"}}))
    assert "Desktop app" in logic.import_google_credentials(web)[1]


# ---------------------------------------------------------------- saving and reset
def test_finish_saves_settings_and_secrets(keyring, monkeypatch):
    started = []
    monkeypatch.setattr(logic, "set_start_with_windows", started.append)
    assert logic.needs_setup()
    logic.finish({"USER_NAME": "Sam", "AI_API_KEY": "gsk_" + "a" * 40, "START_WITH_WINDOWS": True,
                  "_place_name": "Pune"})
    saved = settings_store.load()
    assert saved["USER_NAME"] == "Sam" and "SETUP_DONE" in saved and "_place_name" not in saved
    assert "AI_API_KEY" not in json.dumps(saved) and vault.get("AI_API_KEY") == "gsk_" + "a" * 40
    assert started == [True] and not logic.needs_setup()


def test_settings_window_shows_secrets_masked(keyring):
    vault.set("AI_API_KEY", "gsk_abcdefghijklmnopqrstuvwx")  # fake. secret-check: ignore
    values = logic.current_values()
    assert values["_has_AI_API_KEY"] and values["_mask_AI_API_KEY"] == "gsk_...uvwx"
    assert "gsk_abcdefghijklmnopqrstuvwx" not in json.dumps(values)  # fake. secret-check: ignore


def test_reset_everything(keyring, monkeypatch, tmp_path):
    monkeypatch.setattr(logic, "set_start_with_windows", lambda on: True)
    data = config.DATA_DIR
    data.mkdir(parents=True, exist_ok=True)
    (data / "memory.json").write_text("{}")
    (data / "logs").mkdir()
    settings_store.update({"USER_NAME": "Sam", "TELEGRAM_BOT_TOKEN": "123:abc"})
    assert logic.reset_everything() == 3
    assert list(data.iterdir()) == [] and vault.all_secrets() == {} and logic.needs_setup()


# ---------------------------------------------------------------- the GUI module loads (no window drawn)
def test_the_window_module_imports_and_lists_every_step():
    from jarvis.gui import setup_window

    assert [s.title for s in setup_window.SECTIONS] == ["About you", "Your assistant", "Microphone",
                                                        "AI brain (optional)", "Optional connections",
                                                        "Start with Windows"]
    assert len(setup_window.TAB_NAMES) == len(setup_window.SECTIONS) and setup_window.SAVED == 10


# ---------------------------------------------------------------- no keys at all
@pytest.mark.parametrize("command, skill", [
    ("what time is it", "time"), ("what's the date today", "date"), ("what is 25 times 4", "calculator"),
    ("take a note buy milk", "take note"), ("set a timer for 5 minutes", "timer"),
    ("remind me in 10 minutes to drink water", "reminder"), ("list my routines", "list routines"),
    ("remember that i like cold coffee", "remember"), ("tell me a joke", "joke"),
])
def test_everyday_things_work_without_any_keys(command, skill):
    assert not config.AI_API_KEY and not config.WAQI_TOKEN and not config.TAVILY_API_KEY
    ctx = make_ctx()
    assert handle(ctx, command) == skill
    assert ctx.voice.said and not any("went wrong" in s for s in ctx.voice.said)


def test_weather_and_aqi_use_free_apis_without_keys(monkeypatch):
    from jarvis import http

    def get_json(url, params=None, **kwargs):
        assert "key" not in (params or {}) and "token" not in (params or {})
        if "air-quality" in url:
            return {"hourly": {"time": [datetime.now().strftime("%Y-%m-%dT%H:00")] * 24, "pm2_5": [40.0] * 24,
                               "pm10": [80.0] * 24, "nitrogen_dioxide": [20.0] * 24}}
        return {"current": {"temperature_2m": 24, "apparent_temperature": 25, "relative_humidity_2m": 50,
                            "weather_code": 0, "wind_speed_10m": 5},
                "daily": {"time": [datetime.now().strftime("%Y-%m-%d")] * 2, "temperature_2m_max": [30, 31],
                          "temperature_2m_min": [20, 21], "precipitation_probability_max": [10, 20],
                          "weather_code": [0, 1]}}

    monkeypatch.setattr(http, "get_json", get_json)
    ctx = make_ctx()
    assert handle(ctx, "what's the weather") == "weather"
    assert handle(ctx, "what's the aqi") == "aqi"
    assert not any("went wrong" in s for s in ctx.voice.said)


def test_ai_only_questions_say_how_to_add_a_key(monkeypatch):
    from jarvis.skills import browse

    monkeypatch.setattr(browse, "search", lambda query, **kwargs: None)  # nothing found online either
    ctx = make_ctx()
    handle(ctx, "write a poem about rain")
    assert "Settings, AI brain" in ctx.voice.last
