"""Core tests: no network, no microphone, no real side effects (see conftest.py)."""
import time
from datetime import datetime, timedelta

import pytest
from helpers import FakeVoice, StreamResponse, make_ctx, openai_stream

import config
from jarvis import geo, http, router
from jarvis.geo import Place
from jarvis.router import handle
from jarvis.skills import aqi, general, system
from jarvis.timeparse import find_clock, parse_duration
from main import run, strip_wake_word


# ---------------------------------------------------------------- routing table
ROUTES = [
    # AQI and its speech-recognition variants
    ("what's the aqi", "aqi"), ("aqi in rohini", "aqi"), ("a q i", "aqi"), ("air quality of delhi", "aqi"),
    ("how bad is the pollution today", "aqi"), ("is there smog", "aqi"), ("hello what's the aqi", "aqi"),
    ("pm2.5", "pm2.5"), ("pm 2.5 in dwarka", "pm2.5"), ("pm2 point 5", "pm2.5"), ("what is the pm 25 level", "pm2.5"),
    ("pm10", "pm10"), ("pm 10 in noida", "pm10"),
    ("no2", "no2"), ("no 2 level", "no2"), ("n o 2", "no2"), ("nitrogen dioxide in gurugram", "no2"),
    ("safe", "safe outside"), ("is it safe to go out", "safe outside"), ("pollution safe", "safe outside"),
    ("do i need a mask", "safe outside"), ("is it safe for a morning walk", "safe outside"),
    ("is it safe to eat street food", "ai"), ("is it safe to download this file", "ai"),
    ("red zones", "red zones"), ("which are the most polluted areas", "red zones"),
    ("my area is rohini", "set location"), ("i live in dwarka sector 10", "set location"), ("110085", "set location"),
    # tricky ones
    ("quit chrome", "close"), ("close vs code", "close"), ("search pollution in delhi", "google"),
    ("cancel shutdown", "cancel shutdown"), ("turn off the lights", "ai"), ("what is python", "wikipedia"),
    # general
    ("exit", "exit"), ("goodbye", "exit"), ("bye jarvis", "exit"), ("go to sleep", "sleep"), ("help", "help"),
    ("what can you do", "help"), ("hello", "greet"), ("who are you", "who are you"), ("how are you", "how are you"),
    ("thank you", "thanks"), ("what time is it", "time"), ("what's the date today", "date"),
    ("what is 5 plus 3", "calculator"), ("tell me a joke", "joke"),
    # notes / reminders
    ("take a note buy milk", "take note"), ("remember that the meeting is at 5", "take note"),
    ("read my notes", "read notes"), ("clear my notes", "clear notes"),
    ("remind me in 10 minutes to drink water", "reminder"), ("set a timer for 5 minutes", "timer"),
    ("what are my reminders", "list reminders"), ("cancel my reminders", "cancel reminders"),
    # system
    ("volume up", "volume up"), ("turn down the volume", "volume down"), ("mute", "mute"),
    ("pause the music", "play pause"), ("next song", "next track"), ("brightness 70", "brightness"),
    ("take a screenshot", "screenshot"), ("battery status", "battery"), ("system status", "system status"),
    ("lock the laptop", "lock"), ("shut down the laptop", "power"), ("restart in 5 minutes", "power"),
    ("turn off my computer", "power"), ("put the laptop to sleep", "sleep pc"),
    # web / apps
    ("play believer on youtube", "play youtube"), ("search lofi music on youtube", "youtube search"),
    ("google weather in mumbai", "google"), ("news", "news"), ("news about cricket", "news"),
    ("open chrome and notepad", "open"), ("open github.com", "open"),
    # weather / knowledge / ai
    ("what's the weather", "weather"), ("weather in mumbai tomorrow", "weather"), ("will it rain today", "rain"),
    ("do i need an umbrella", "rain"), ("who is sachin tendulkar", "wikipedia"), ("tell me about the taj mahal", "wikipedia"),
    ("write a poem about rain", "ai"), ("forget our conversation", "forget conversation"),
    # whatsapp
    ("send a whatsapp to mom saying i'll be late", "whatsapp message"),
    ("message dhruv on whatsapp that the demo is at 5", "whatsapp message"), ("whatsapp mom", "whatsapp message"),
    ("call mom on whatsapp", "whatsapp call"), ("add contact riya 9876543210", "whatsapp add contact"),
    ("open whatsapp", "open"),
    # vs code
    ("open bogie in vs code", "vscode open project"), ("open bogie project", "vscode open project"),
    ("work on jarvis", "vscode open project"), ("open vs code here", "vscode new window"),
    ("new vs code window", "vscode new window"), ("open file main.py in jarvis", "vscode open file"),
    ("list my projects", "vscode list projects"), ("rescan projects", "vscode rescan"),
    ("git status of jarvis", "git status"), ("pull jarvis", "git pull"), ("run bogie backend", "run project"),
    ("open vs code", "open"),
    # gmail
    ("connect google", "connect google"), ("disconnect google", "disconnect google"),
    ("check my email", "check email"), ("any new emails", "check email"), ("do i have any unread emails", "check email"),
    ("email", "check email"), ("check my inbox", "check email"),
    ("read the email from dhruv", "read email"), ("what did dhruv email me", "read email"),
    ("search email for invoice", "search email"), ("search my gmail for flight tickets", "search email"),
    ("email dhruv saying the demo is at 5", "email draft"), ("write an email to riya about the trip", "email draft"),
    ("send an email to mom", "email draft"), ("open gmail", "open"), ("search invoice", "google"),
    # calendar
    ("what's on my calendar today", "calendar today"), ("what's on my calendar tomorrow", "calendar today"),
    ("what do i have on friday", "calendar today"), ("my meetings this week", "calendar today"),
    ("am i free tomorrow", "calendar today"), ("my next meeting", "next meeting"),
    ("when is my next meeting", "next meeting"),
    ("add meeting with dhruv tomorrow at 5 pm for 30 minutes", "add event"),
    ("schedule dentist on monday at 11", "add event"), ("add lunch with mom tomorrow at 1 to my calendar", "add event"),
    ("cancel my 5 pm meeting", "cancel event"), ("delete the standup from my calendar", "cancel event"),
    ("schedule a shutdown in 10 minutes", "ai"), ("remind me tomorrow at 5 pm to call dhruv", "reminder"),
    ("cancel my reminders", "cancel reminders"),
    # spotify
    ("connect spotify", "connect spotify"), ("play believer on spotify", "spotify play"),
    ("spotify play kesariya", "spotify play"), ("play my liked songs", "spotify liked songs"),
    ("play playlist workout", "spotify playlist"), ("play my chill playlist on spotify", "spotify playlist"),
    ("pause spotify", "spotify pause"), ("resume spotify", "spotify resume"),
    ("next song on spotify", "spotify next"), ("skip song on spotify", "spotify next"),
    ("previous song on spotify", "spotify previous"), ("what's playing", "spotify now playing"),
    ("what song is this", "spotify now playing"), ("spotify volume 50", "spotify volume"),
    ("next song", "next track"), ("pause the music", "play pause"), ("play believer", "play youtube"),
    ("quit spotify", "close"), ("open spotify", "open"), ("volume up", "volume up"),
]

@pytest.mark.parametrize("phrase, expected", ROUTES)
def test_routing(recorded_skills, phrase, expected):
    assert handle(make_ctx(), phrase) == expected


def test_skill_order_cancel_shutdown_before_power():
    names = [s.name for s in router.SKILLS]
    assert names.index("cancel shutdown") < names.index("power")
    assert names.index("google") < names.index("aqi")


# ---------------------------------------------------------------- wake word and locations
@pytest.mark.parametrize("text, expected", [
    ("jarvis what's the aqi", (True, "what's the aqi")),
    ("hey jarvis, open chrome", (True, "open chrome")),
    ("okay jarvis what time is it", (True, "what time is it")),
    ("what's the weather jarvis", (True, "what's the weather")),
    ("jarvis", (True, "")),
    ("so jarvis tell me a joke", (True, "tell me a joke")),
    ("turn off jarvis", (True, "exit")),
    ("bye jarvis", (True, "exit")),
    ("shut down jarvis", (True, "exit")),
    ("what's the aqi", (False, "what's the aqi")),
    # "jarvis" is also a project name: keep it when it's the object of the command
    ("git status of jarvis", (True, "git status of jarvis")),
    ("jarvis open file main.py in jarvis", (True, "open file main.py in jarvis")),
    ("pull jarvis", (True, "pull jarvis")),
    ("close jarvis", (True, "exit")),
])
def test_strip_wake_word(text, expected):
    assert strip_wake_word(text, ["hey jarvis", "ok jarvis", "okay jarvis", "jarvis"]) == expected


@pytest.mark.parametrize("command, expected", [
    ("pm2.5 in rohini sector 7", "rohini sector 7"),
    ("what's the aqi in dwarka today", "dwarka"),
    ("aqi of noida", "noida"),
    ("aqi 110085", "110085"),
    ("pincode 110 085", "110085"),
    ("aqi in my area", None),
    ("is it safe to go out here", None),
    ("is it safe for a morning walk", None),
    ("pm10 today", None),
    ("what's the air quality", None),
])
def test_location_from_command(command, expected):
    assert geo.location_from_command(command) == expected


# ---------------------------------------------------------------- CPCB maths
@pytest.mark.parametrize("pollutant, conc, index", [
    ("pm2_5", 0, 0), ("pm2_5", 15, 25), ("pm2_5", 30, 50), ("pm2_5", 45, 75), ("pm2_5", 60, 100),
    ("pm2_5", 90, 200), ("pm2_5", 105, 250), ("pm2_5", 250, 400), ("pm2_5", 2000, 500),
    ("pm10", 50, 50), ("pm10", 175, 150), ("pm10", 350, 300), ("pm10", 430, 400),
    ("nitrogen_dioxide", 40, 50), ("nitrogen_dioxide", 130, 150), ("nitrogen_dioxide", 400, 400),
])
def test_sub_index(pollutant, conc, index):
    assert aqi.sub_index(pollutant, conc) == index


@pytest.mark.parametrize("value, name, zone", [
    (30, "Good", "Green"), (50, "Good", "Green"), (51, "Satisfactory", "Green"), (100, "Satisfactory", "Green"),
    (150, "Moderate", "Yellow"), (200, "Moderate", "Yellow"), (201, "Poor", "Red"), (350, "Very Poor", "Red"),
    (450, "Severe", "Red"),
])
def test_categories(value, name, zone):
    assert aqi.category(value)[0] == name
    assert aqi.zone(value) == zone


# ---------------------------------------------------------------- AQI end to end
def open_meteo_item(pm25, pm10=60.0, no2=20.0, current_pm25=None):
    start = datetime(2026, 11, 20, 0, 0)
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
    # 24 hours before "now" use the given value; far-past and future hours are junk that must be ignored
    now_index = 34
    series = [999.0 if (i <= now_index - 24 or i > now_index) else pm25 for i in range(48)]
    return {
        "current": {"time": times[now_index], "pm2_5": current_pm25 or pm25, "pm10": pm10, "nitrogen_dioxide": no2},
        "hourly": {"time": times, "pm2_5": series, "pm10": [pm10] * 48, "nitrogen_dioxide": [no2] * 48},
    }


def fake_air(values):
    calls = []

    def get_json(url, params=None, **kwargs):
        calls.append((url, params))
        assert url == aqi.AIR_URL
        count = len(params["latitude"].split(","))
        items = [open_meteo_item(values[i % len(values)]) for i in range(count)]
        return items if count > 1 else items[0]

    return get_json, calls


def test_aqi_end_to_end(monkeypatch):
    get_json, calls = fake_air([100.0])
    monkeypatch.setattr(http, "get_json", get_json)
    ctx = make_ctx()
    ctx.state["location"] = Place("Rohini", 28.74, 77.08)
    assert handle(ctx, "what's the aqi") == "aqi"
    reply = ctx.voice.last
    assert "Rohini" in reply and "233" in reply and "Poor" in reply and "red zone" in reply
    assert "PM2.5" in reply and "N95" in reply
    params = calls[0][1]
    assert params["past_days"] == 1 and params["timezone"] == "auto" and "pm2_5" in params["hourly"]


def test_pm25_reply_has_current_and_average(monkeypatch):
    def get_json(url, params=None, **kwargs):
        return open_meteo_item(40.0, current_pm25=55.0)

    monkeypatch.setattr(http, "get_json", get_json)
    ctx = make_ctx()
    ctx.state["location"] = Place("Dwarka", 28.59, 77.04)
    handle(ctx, "pm 2.5")
    assert ctx.voice.last == ("In Dwarka, PM2.5 is 55 micrograms per cubic metre right now, "
                              "with a 24-hour average of 40. That's a green zone.")


def test_set_location_geocodes_and_remembers(monkeypatch):
    def get_json(url, params=None, **kwargs):
        if url == geo.NOMINATIM_URL:
            assert params["postalcode"] == "110085" and params["country"] == "India"
            return [{"lat": "28.71", "lon": "77.11", "display_name": "110085, Rohini, Delhi, India"}]
        return open_meteo_item(50.0)

    monkeypatch.setattr(http, "get_json", get_json)
    ctx = make_ctx()
    assert handle(ctx, "110085") == "set location"
    assert ctx.state["location"].name == "Rohini"
    assert "Rohini, pincode 110085," in ctx.voice.last and "Satisfactory" in ctx.voice.last


def test_bom_from_piped_input_is_ignored(recorded_skills):
    assert handle(make_ctx(), "﻿hello") == "greet"


def test_safe_outside_advice(monkeypatch):
    monkeypatch.setattr(http, "get_json", lambda url, params=None, **k: open_meteo_item(20.0, pm10=30.0))
    ctx = make_ctx()
    ctx.state["location"] = Place("Noida", 28.53, 77.39)
    handle(ctx, "is it safe to go out")
    assert "Good" in ctx.voice.last and "safe to go out" in ctx.voice.last


def test_red_zones_open_meteo(monkeypatch):
    values = [100.0, 150.0, 40.0, 200.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0]
    get_json, calls = fake_air(values)
    monkeypatch.setattr(http, "get_json", get_json)
    ctx = make_ctx()
    handle(ctx, "red zones")
    assert len(calls) == 1  # all 10 NCR points in one request
    assert len(calls[0][1]["latitude"].split(",")) == 10
    reply = ctx.voice.last
    assert reply.index("Anand Vihar") < reply.index("Rohini") < reply.index("Central Delhi")
    assert "Dwarka" not in reply


def test_red_zones_none_red_names_worst(monkeypatch):
    get_json, _ = fake_air([20.0, 50.0, 20.0])
    monkeypatch.setattr(http, "get_json", get_json)
    ctx = make_ctx()
    handle(ctx, "red zones")
    assert "None of" in ctx.voice.last and "Rohini" in ctx.voice.last


def test_red_zones_waqi(monkeypatch):
    monkeypatch.setattr(config, "WAQI_TOKEN", "test-token")

    def get_json(url, params=None, **kwargs):
        assert url == aqi.WAQI_BOUNDS_URL and params["token"] == "test-token"
        lat1, lon1, lat2, lon2 = map(float, params["latlng"].split(","))
        assert round(lat2 - lat1, 2) == 0.7 and round(lon2 - lon1, 2) == 0.7
        return {"status": "ok", "data": [
            {"aqi": "-", "station": {"name": "Broken, Delhi"}},
            {"aqi": "250", "station": {"name": "Rohini, Delhi, Delhi, India"}},
            {"aqi": "180", "station": {"name": "Lodhi Road, Delhi"}},
            {"aqi": "390", "station": {"name": "Anand Vihar, Delhi - DPCC"}},
        ]}

    monkeypatch.setattr(http, "get_json", get_json)
    ctx = make_ctx()
    ctx.state["location"] = Place("New Delhi", 28.61, 77.21)
    handle(ctx, "red zones")
    reply = ctx.voice.last
    assert "Anand Vihar 390, Rohini 250" in reply
    assert "Lodhi" not in reply and "Broken" not in reply


def test_network_error_is_spoken_not_raised(monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(http, "get_json", boom)
    ctx = make_ctx()
    ctx.state["location"] = Place("Rohini", 28.74, 77.08)
    handle(ctx, "aqi")
    assert "couldn't reach the online service" in ctx.voice.last


# ---------------------------------------------------------------- calculator
@pytest.mark.parametrize("phrase, answer", [
    ("what is 25 times 4", "100"), ("calculate 10 plus 5 minus 3", "12"), ("what is 100 divided by 8", "12.5"),
    ("what is 10 percent of 250", "25"), ("what is the square root of 144", "12"), ("what is 2 to the power of 10", "1,024"),
    ("what's 12 squared", "144"), ("what is 7 into 6", "42"),
])
def test_calculator(phrase, answer):
    ctx = make_ctx()
    assert handle(ctx, phrase) == "calculator"
    assert ctx.voice.last.endswith(f" is {answer}.")


def test_calculator_rejects_huge_exponent():
    ctx = make_ctx()
    handle(ctx, "what is 2 to the power of 1000000")
    assert "too big" in ctx.voice.last


def test_calculator_divide_by_zero():
    ctx = make_ctx()
    handle(ctx, "what is 5 divided by 0")
    assert "divide by zero" in ctx.voice.last


def test_safe_eval_rejects_code():
    with pytest.raises(ValueError):
        general.safe_eval("__import__('os').system('echo hi')")
    assert general.to_expression("python") is None


# ---------------------------------------------------------------- time parsing and reminders
@pytest.mark.parametrize("text, seconds", [
    ("in 10 minutes", 600), ("in an hour and 30 minutes", 5400), ("in half an hour", 1800),
    ("in an hour and a half", 5400), ("after 2 hours", 7200), ("in 45 seconds", 45), ("for five minutes", 300),
    ("to call mom", None),
])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


def test_parse_clock_rolls_over():
    morning = datetime(2026, 11, 21, 10, 0)
    assert find_clock("at 6 pm", morning)[0] == datetime(2026, 11, 21, 18, 0)
    assert find_clock("at 6", morning)[0] == datetime(2026, 11, 21, 18, 0)
    assert find_clock("at 9:30 am", morning)[0] == datetime(2026, 11, 22, 9, 30)
    evening = datetime(2026, 11, 21, 19, 0)
    assert find_clock("at 6 pm", evening)[0] == datetime(2026, 11, 22, 18, 0)


def test_reminder_fires():
    ctx = make_ctx()
    handle(ctx, "remind me in 1 second to stretch")
    assert "stretch" in ctx.voice.last
    deadline = time.time() + 4
    while time.time() < deadline and "Reminder, Anjali: stretch" not in ctx.voice.said:
        time.sleep(0.05)
    assert "Reminder, Anjali: stretch" in ctx.voice.said
    assert ctx.state["reminders"] == []


def test_reminder_asks_when_and_cancel():
    ctx = make_ctx(["10 minutes"])
    handle(ctx, "remind me to drink water")
    assert ctx.voice.said[-2] == "When should I remind you?"
    assert "drink water" in ctx.voice.last and "10 minutes" in ctx.voice.last
    handle(ctx, "cancel my reminders")
    assert ctx.voice.last == "Cancelled 1 reminder."


# ---------------------------------------------------------------- notes
def test_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "NOTES_FILE", tmp_path / "data" / "notes.txt")
    ctx = make_ctx()
    handle(ctx, "read my notes")
    assert "don't have any notes" in ctx.voice.last
    handle(ctx, "take a note buy milk")
    assert ctx.voice.last == "Noted: buy milk."
    handle(ctx, "read my notes")
    assert ctx.voice.last == "You have 1 note: buy milk."
    handle(ctx, "remember that the meeting is at 5")
    handle(ctx, "read my notes")
    assert ctx.voice.last.startswith("You have 2 notes.") and "1: the meeting is at 5" in ctx.voice.last
    ctx.voice.answers = ["call the bank"]
    handle(ctx, "take a note")
    assert ctx.voice.last == "Noted: call the bank."
    ctx.voice.answers = ["yes"]
    handle(ctx, "clear my notes")
    assert ctx.voice.last == "Done. I deleted 3 notes."


# ---------------------------------------------------------------- system safety, AI, exit
def test_shutdown_refused_when_answer_is_no(monkeypatch):
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    ran = []
    monkeypatch.setattr(system, "run", lambda *a, **k: ran.append(a))
    ctx = make_ctx(["no"])
    handle(ctx, "shut down the laptop")
    assert "Are you sure" in ctx.voice.said[0]
    assert ctx.voice.last == "Okay, I won't shut down the laptop."
    assert ran == []


def test_shutdown_confirmed_uses_delay(monkeypatch):
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    ran = []

    class Done:
        returncode = 0

    monkeypatch.setattr(system, "run", lambda args, **k: ran.append(args) or Done())
    ctx = make_ctx(["yes"])
    handle(ctx, "restart the laptop in 5 minutes")
    assert ran == [["shutdown", "/r", "/t", "300"]]


def test_ai_without_key(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    ctx = make_ctx()
    assert handle(ctx, "turn off the lights") == "ai"
    assert "AI_API_KEY" in ctx.voice.last


def test_ai_with_key_keeps_history(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "sk-test")

    sent = []
    monkeypatch.setattr(http, "post_stream",
                        lambda url, payload, **k: sent.append(payload) or openai_stream("**Sure!** Here's a `tip`."))
    ctx = make_ctx()
    handle(ctx, "give me a study tip")
    assert ctx.voice.last == "Sure! Here's a tip."
    assert len(ctx.state["ai_history"]) == 2 and sent[0]["max_tokens"] == 150 and sent[0]["stream"] is True
    handle(ctx, "forget our conversation")
    assert ctx.state["ai_history"] == []


@pytest.mark.parametrize("status, body, expected", [
    (401, "Invalid API Key", "My AI key was rejected. Please check AI_API_KEY in your .env file."),
    (400, '{"error": {"message": "API key not valid. Please pass a valid API key."}}', "My AI key was rejected"),
    (403, "", "My AI key was rejected"),
    (404, "model not found", "The AI model test-model wasn't found. Please check AI_MODEL and AI_BASE_URL"),
    (429, "quota exceeded", "I've used up the free AI quota or hit the rate limit"),
    (503, "", "The AI service is having problems"),
    (400, "bad request", "The AI service returned an error, code 400."),
])
def test_ai_error_messages(monkeypatch, status, body, expected):
    monkeypatch.setattr(config, "AI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AI_MODEL", "test-model")

    monkeypatch.setattr(http, "post_stream", lambda *a, **k: StreamResponse(status_code=status, text=body))
    ctx = make_ctx()
    handle(ctx, "tell me something interesting")
    assert ctx.voice.last.startswith(expected)
    assert "sk-test" not in ctx.voice.last
    assert ctx.state["ai_history"] == []  # failed turns are not remembered


def test_ai_prompt_asks_for_roman_hinglish(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "sk-test")
    sent = []

    monkeypatch.setattr(http, "post_stream",
                        lambda url, payload, **k: sent.append(payload) or openai_stream("Haan, bilkul!"))
    ctx = make_ctx()
    handle(ctx, "kya tum hindi samajh sakte ho")
    system_prompt = sent[0]["messages"][0]["content"]
    assert "Hinglish" in system_prompt and "Roman" in system_prompt and "Devanagari" in system_prompt
    assert ctx.voice.last == "Haan, bilkul!"


# ---------------------------------------------------------------- help and language
def test_short_help_prints_full_list(caplog):
    caplog.set_level("INFO")
    ctx = make_ctx()
    assert handle(ctx, "help") == "help"
    assert ctx.voice.last == general.SHORT_HELP
    printed = caplog.text
    for title in ("Air quality:", "WhatsApp:", "VS Code:", "Laptop controls:"):
        assert title in printed
    assert '"send a WhatsApp to mom saying I\'ll be late"' in printed


def test_every_skill_has_category_and_examples():
    for sk in router.SKILLS:
        assert sk.category in general.CATEGORIES, sk.name
        assert sk.examples, sk.name
    for key in general.CATEGORIES:
        assert 4 <= len(general.examples_for(key, limit=6, spoken=True)) <= 6, key


def test_spoken_general_help_skips_housekeeping():
    ctx = make_ctx()
    handle(ctx, "help with general questions")
    assert ctx.voice.said[0].startswith("For general questions, you can say: Switch to Hindi. What is 25 times 4.")
    assert "Goodbye" not in ctx.voice.said[0]


@pytest.mark.parametrize("phrase, title", [
    ("help with whatsapp", "WhatsApp"), ("help laptop", "laptop controls"),
    ("what can you do with vs code", "VS Code"), ("help me with my notes", "notes and reminders"),
    ("help with the air quality", "air quality"), ("help with whats app", "WhatsApp"),
    ("help with vs cod", "VS Code"), ("commands for weather", "time and weather"), ("help with youtube", "web"),
    ("help with email", "email"), ("help with gmail", "email"), ("help with calendar", "calendar"),
    ("help with my meetings", "calendar"), ("help with spotify", "Spotify"), ("help with playlists", "Spotify"),
])
def test_help_category(phrase, title):
    ctx = make_ctx()
    assert handle(ctx, phrase) == "help category"
    reply = ctx.voice.said[0]
    assert reply.startswith(f"For {title}, you can say: ")
    assert reply.count(". ") == 2                          # 3 examples...
    assert ctx.voice.said[1:] == ["Want more?"]           # ...then it asks (FakeVoice answers nothing)


def test_help_category_falls_through_to_ai(monkeypatch):
    ctx = make_ctx()
    assert handle(ctx, "help me write an email to my teacher") == "ai"
    assert handle(ctx, "help me") == "help"


def test_language_switch():
    ctx = make_ctx()
    ctx.voice.language = "en-IN"
    assert handle(ctx, "switch to hindi") == "language"
    assert ctx.voice.language == "hi-IN" and "switched to Hindi" in ctx.voice.last
    assert handle(ctx, "switch to hindi") == "language"
    assert ctx.voice.last == "I'm already listening in Hindi."
    # in Hindi mode Google returns Devanagari text
    assert handle(ctx, "स्विच टू इंग्लिश") == "language"
    assert ctx.voice.language == "en-IN" and ctx.voice.last == "Okay, switched back to English."


def test_language_switch_does_not_touch_config(monkeypatch):
    monkeypatch.setattr(config, "SPEECH_LANGUAGE", "en-IN")
    ctx = make_ctx()
    handle(ctx, "change language to hindi")
    assert config.SPEECH_LANGUAGE == "en-IN" and ctx.voice.language == "hi-IN"


def test_hindi_wake_word_and_exit():
    assert strip_wake_word("जार्विस मौसम कैसा है") == (True, "मौसम कैसा है")
    ctx = make_ctx()
    assert handle(ctx, "बाय") == "exit" and ctx.running is False


def test_exit_stops():
    ctx = make_ctx()
    assert handle(ctx, "exit") == "exit"
    assert ctx.running is False


def test_main_loop_with_wake_word(monkeypatch):
    monkeypatch.setattr(config, "FOLLOW_UP_SECONDS", 0)
    voice = FakeVoice(["what time is it", "jarvis", "jarvis what is 2 plus 2", "", "bye jarvis"], stop_when_empty=True)
    ctx = run(wake_enabled=True, voice=voice)
    assert "is online" in voice.said[0]
    assert "Yes?" in voice.said
    assert "2 plus 2 is 4." in voice.said
    assert not any(s.startswith("It's ") for s in voice.said)  # ignored: no wake word
    assert ctx.running is False


def test_main_loop_no_wake_misses():
    voice = FakeVoice(["", "", "exit"], stop_when_empty=True)
    run(wake_enabled=False, voice=voice)
    assert "Sorry, I didn't catch that." in voice.said


def test_ai_rejected_key_message_for_gemini():
    from jarvis.skills.ai import error_message

    assert error_message(400, '{"error": {"message": "Invalid Auth key."}}').startswith("My AI key was rejected.")
