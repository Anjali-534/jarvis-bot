"""Natural-language actions via AI tool calling. The AI is mocked: each test scripts what it 'decides'."""
import json
import logging
from datetime import datetime, timedelta

import pytest
from helpers import FakeVoice, make_ctx, openai_stream

import config
from jarvis import http, intent
from jarvis.geo import Place
from jarvis.router import Context, handle
from jarvis.skills import aqi, notes, system, weather, web, whatsapp


class Recorder(list):
    pass


class Response:
    def __init__(self, data, status=200):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


def tool_call(name, **args):
    return Response({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]})


def text_reply(text):
    return Response({"choices": [{"message": {"role": "assistant", "content": text}}]})


@pytest.fixture
def ai_router(monkeypatch):
    """AI routing on, provider Groq. Queue responses with ai_router.append(...); requests are recorded."""
    monkeypatch.setattr(config, "AI_ROUTING", True)
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(config, "AI_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(config, "AI_MODEL", "qwen/qwen3.8-27b")
    monkeypatch.setattr(config, "AI_REASONING_EFFORT", "none")
    requests, responses = [], Recorder()

    def post(url, payload, headers=None, timeout=None):
        requests.append({"url": url, "payload": payload, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(http, "post_json", post)
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: pytest.fail("chat should not be needed"))
    responses.requests = requests
    return responses


@pytest.fixture
def laptop(monkeypatch):
    pressed = Recorder()
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "press", lambda key, presses=1: pressed.append((key, presses)))
    return pressed


# ---------------------------------------------------------------- the examples
def test_hinglish_volume(ai_router, laptop, caplog):
    ai_router.append(tool_call("volume", direction="up"))
    ctx = make_ctx()
    with caplog.at_level(logging.INFO):
        assert handle(ctx, "yaar thoda sound badha do") == "volume up"
    assert laptop == [("volumeup", 5)]
    assert "(understood as: volume up)" in caplog.text
    body = ai_router.requests[0]["payload"]
    assert body["tool_choice"] == "required" and body["model"] == "qwen/qwen3.8-27b"  # a skill, or "reply"
    assert ai_router.requests[0]["timeout"] == 4.0
    assert {t["function"]["name"] for t in body["tools"]} >= {"volume", "reminder", "whatsapp_message", "air_quality"}


def test_cant_hear_anything(ai_router, laptop):
    ai_router.append(tool_call("volume", direction="up", amount=20))
    assert handle(make_ctx(), "i can't hear anything") == "volume up"
    assert laptop == [("volumeup", 10)]  # "volume up by 20": 10 steps of 2%


@pytest.fixture
def contacts(monkeypatch):
    config.CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CONTACTS_FILE.write_text(json.dumps({"dhruv": "919812345678", "riya": "919811112222"}), encoding="utf-8")
    sent = []
    monkeypatch.setattr(whatsapp, "whatsapp_installed", lambda: True)
    monkeypatch.setattr(whatsapp, "open_uri", sent.append)
    monkeypatch.setattr(whatsapp, "press_enter", lambda: sent.append("enter"))
    return sent


def test_ping_dhruv_asks_before_sending(ai_router, contacts):
    ai_router.append(tool_call("whatsapp_message", contact="dhruv", message="I'm running late"))
    ctx = make_ctx(["no"])
    assert handle(ctx, "ping dhruv that i'm running late") == "whatsapp message"
    assert ctx.voice.said == ["Send 'I'm running late' to Dhruv?", "Okay, I didn't send it."]
    assert contacts == []  # nothing sent without a yes

    ai_router.append(tool_call("whatsapp_message", contact="dhruv", message="I'm running late"))
    ctx = make_ctx(["yes"])
    handle(ctx, "ping dhruv that i'm running late please")
    assert contacts[0].startswith("whatsapp://send?phone=919812345678&text=I%27m%20running%20late")


def test_kal_subah_reminder(ai_router, monkeypatch):
    ai_router.append(tool_call("reminder", when="tomorrow at 9 am", message="go to the gym"))
    ctx = make_ctx()
    assert handle(ctx, "kal subah 9 baje yaad dilana gym jaana hai") == "reminder"
    entry = ctx.state["reminders"][0]
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    assert (entry["when"].date(), entry["when"].hour, entry["when"].minute) == (tomorrow, 9, 0)
    assert entry["text"] == "go to the gym"
    assert ctx.voice.last == "Okay, I'll remind you tomorrow at 9 AM: go to the gym."


@pytest.fixture
def air(monkeypatch):
    asked = []

    def fake_reading(ctx, command):
        asked.append(command)
        name = command.split(" in ", 1)[1].title() if " in " in command else "New Delhi"
        return aqi.AirReading(Place(name, 28.5, 77.0), {}, {}, 180, "pm2_5")

    monkeypatch.setattr(aqi, "reading_for", fake_reading)
    return asked


def test_hows_the_air_outside(ai_router, air):
    ai_router.append(tool_call("air_quality"))
    ctx = make_ctx()
    assert handle(ctx, "how's the air outside") == "aqi"
    assert air == ["aqi"] and ctx.voice.last.startswith("The AQI in New Delhi is 180")


def test_cold_tomorrow(ai_router, monkeypatch):
    seen = []
    monkeypatch.setattr(weather, "forecast", lambda place: seen.append(place.name) or {
        "current": {"temperature_2m": 20, "apparent_temperature": 19, "relative_humidity_2m": 50, "weather_code": 0,
                    "wind_speed_10m": 5},
        "daily": {"weather_code": [0, 3], "temperature_2m_max": [24, 21], "temperature_2m_min": [12, 9],
                  "precipitation_probability_max": [0, 10]}})
    ai_router.append(tool_call("weather", day="tomorrow"))
    ctx = make_ctx()
    assert handle(ctx, "is it going to be cold tomorrow") == "weather"
    assert "tomorrow" in ctx.voice.last.lower() and "9" in ctx.voice.last


def test_put_on_some_chill_music(ai_router, monkeypatch):
    opened = []
    monkeypatch.setattr(web, "find_youtube_video", lambda query: "abcdefghijk")
    monkeypatch.setattr(web, "open_url", opened.append)
    ai_router.append(tool_call("play", query="chill music"))
    assert handle(make_ctx(), "put on some chill music") == "play youtube"
    assert opened == ["https://www.youtube.com/watch?v=abcdefghijk"]


def test_play_on_spotify_goes_to_spotify(ai_router, monkeypatch):
    from jarvis.skills import spotify

    opened = []
    monkeypatch.setattr(spotify, "client", lambda: None)
    monkeypatch.setattr(spotify, "open_uri", opened.append)
    ai_router.append(tool_call("play", query="arijit singh", platform="spotify"))
    assert handle(make_ctx(), "lagao arijit wale gaane spotify pe") == "spotify play"
    assert opened == ["spotify:search:arijit%20singh"]


def test_what_about_gurgaon_uses_context(ai_router, air):
    ctx = make_ctx()
    handle(ctx, "aqi in noida")  # regex skill, no AI involved
    assert ai_router.requests == []
    ai_router.append(tool_call("air_quality", place="gurgaon"))
    assert handle(ctx, "what about gurgaon") == "aqi"
    system = ai_router.requests[0]["payload"]["messages"][0]["content"]
    assert "Recent conversation:\nUser: aqi in noida  ->  ran command: aqi in noida" in system
    assert "The AQI in Noida is 180" not in system  # old answers aren't shown, so the model must call the tool
    assert air == ["aqi in noida", "aqi in gurgaon"]


def test_fun_fact_is_plain_chat(ai_router):
    ai_router.append(text_reply("Honey never spoils. Archaeologists found edible honey in ancient Egyptian tombs."))
    ctx = make_ctx()
    assert handle(ctx, "tell me a fun fact") == "ai"
    assert ctx.voice.said == ["Honey never spoils.", "Archaeologists found edible honey in ancient Egyptian tombs."]
    assert ctx.state["ai_history"][-1]["content"].startswith("Honey never spoils.")


def test_reply_tool_is_plain_chat(ai_router):
    ai_router.append(tool_call("reply", text="Main bilkul theek hoon. Aap batao, kya haal hai?"))
    ctx = make_ctx()
    assert handle(ctx, "tum kaise ho yaar") == "ai"
    assert ctx.voice.said == ["Main bilkul theek hoon.", "Aap batao, kya haal hai?"]
    names = [t["function"]["name"] for t in ai_router.requests[0]["payload"]["tools"]]
    assert names[-3:] == ["reply", "cant_do", "clarify"]


# ---------------------------------------------------------------- follow-ups
def test_make_it_5_minutes_instead(ai_router):
    ctx = make_ctx()
    handle(ctx, "set a timer for 10 minutes")
    ai_router.append(tool_call("timer", duration="5 minutes"))
    assert handle(ctx, "make it 5 minutes instead") == "timer"
    assert "User: set a timer for 10 minutes  ->  ran command: set a timer for 10 minutes" in \
        ai_router.requests[0]["payload"]["messages"][0]["content"]
    assert ctx.voice.last == "Timer set for 5 minutes."


def test_and_tomorrow_after_weather(ai_router, monkeypatch):
    monkeypatch.setattr(weather, "forecast", lambda place: {
        "current": {"temperature_2m": 20, "apparent_temperature": 19, "relative_humidity_2m": 50, "weather_code": 0,
                    "wind_speed_10m": 5},
        "daily": {"weather_code": [0, 61], "temperature_2m_max": [24, 21], "temperature_2m_min": [12, 9],
                  "precipitation_probability_max": [0, 70]}})
    ctx = make_ctx()
    handle(ctx, "weather in mumbai")
    ai_router.append(tool_call("weather", place="mumbai", day="tomorrow"))
    assert handle(ctx, "and tomorrow?") == "weather"
    assert ctx.state["turns"][-1]["understood"] == "weather in mumbai tomorrow"


def test_send_the_same_to_riya(ai_router, contacts):
    ctx = make_ctx(["yes", "yes"])
    handle(ctx, "send a whatsapp to dhruv saying the demo is at 5")
    ai_router.append(tool_call("whatsapp_message", contact="riya", message="the demo is at 5"))
    assert handle(ctx, "send the same to riya") == "whatsapp message"
    context = ai_router.requests[0]["payload"]["messages"][0]["content"]
    assert "send a whatsapp to dhruv saying the demo is at 5" in context
    assert ctx.voice.said[-2] == "Send 'The demo is at 5' to Riya?"
    assert len([u for u in contacts if u != "enter"]) == 2


def test_only_the_last_three_turns_are_sent(ai_router):
    ctx = make_ctx()
    for phrase in ("what time is it", "tell me a joke", "what's the date today", "what is 2 plus 2"):
        handle(ctx, phrase)
    ai_router.append(text_reply("Sure thing, here you go."))
    handle(ctx, "something else entirely please")
    context = ai_router.requests[0]["payload"]["messages"][0]["content"]
    assert "what time is it" not in context and "what is 2 plus 2" in context


# ---------------------------------------------------------------- safety
RISKY = {"power", "whatsapp_message", "email_draft", "calendar_add", "calendar_cancel", "close",
         "git_pull", "run_project"}


def test_risky_tools_are_marked():
    marked = {name for name, _, _, sk in intent.tools() if sk.risky}
    assert marked == RISKY


def test_risky_tool_without_its_own_check_is_confirmed(ai_router, laptop, monkeypatch):
    killed = []
    from jarvis.skills import apps

    monkeypatch.setattr(apps, "kill", lambda image: killed.append(image))
    ai_router.append(tool_call("close", target="chrome"))
    ctx = make_ctx(["no"])
    assert handle(ctx, "band karo chrome ko") == "close"
    assert ctx.voice.said == ["Just to check: close chrome. Shall I go ahead?", "Okay, I didn't do it."]
    assert killed == []


def test_shutdown_picked_by_ai_still_confirms(ai_router, monkeypatch):
    ran = []
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "run", lambda args, timeout=20: ran.append(args))
    ai_router.append(tool_call("power", action="shutdown"))
    ctx = make_ctx(["no"])
    handle(ctx, "laptop band kar do yaar")
    assert ran == [] and "?" in ctx.voice.said[0]


def test_unknown_tool_is_ignored(ai_router, monkeypatch):
    ai_router.append(tool_call("format_hard_drive", drive="C"))
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: openai_stream("I can't do that, sorry."))
    ctx = make_ctx()
    assert handle(ctx, "wipe everything") == "ai"
    assert ctx.voice.last == "I can't do that, sorry."


def test_arguments_cannot_reach_other_skills(ai_router, monkeypatch):
    """A tool's command may only land on that tool's own skill(s)."""
    ai_router.append(tool_call("media", action="shut down the laptop"))  # not an allowed value
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: openai_stream("Okay."))
    monkeypatch.setattr(system, "run", lambda *a, **k: pytest.fail("power must not run"))
    ctx = make_ctx()
    assert handle(ctx, "do the thing") == "ai"


def test_target_for_respects_allowed_skills():
    from jarvis import router

    volume = next(sk for sk in router.SKILLS if sk.name == "volume up")
    assert intent.target_for(volume, "volume down")[0].name == "volume down"
    assert intent.target_for(volume, "shut down the laptop") == (None, None)


def test_args_are_sanitised():
    params = {"contact": "name", "message": "text"}
    assert intent.clean_args({"contact": " riya\nsharma ", "message": "x" * 500, "extra": "rm -rf"}, params) == {
        "contact": "riya sharma", "message": "x" * 200}


@pytest.mark.parametrize("template, args, expected", [
    ("weather[ in {place}][ {day}]", {}, "weather"),
    ("weather[ in {place}][ {day}]", {"day": "tomorrow"}, "weather tomorrow"),
    ("weather[ in {place}][ {day}]", {"place": "noida", "day": "tomorrow"}, "weather in noida tomorrow"),
    ("remind me {when} to {message}", {"when": "at 6 pm"}, None),  # a required field is missing
])
def test_fill(template, args, expected):
    assert intent.fill(template, args) == expected


# ---------------------------------------------------------------- speed and fallbacks
def test_regex_commands_never_call_the_ai(ai_router, laptop):
    assert handle(make_ctx(), "volume up") == "volume up"
    assert ai_router.requests == []


def test_identical_phrases_are_cached(ai_router, laptop):
    ai_router.append(tool_call("volume", direction="up"))
    ctx = make_ctx()
    handle(ctx, "yaar thoda sound badha do")
    handle(ctx, "yaar thoda sound badha do")
    assert len(ai_router.requests) == 1 and len(laptop) == 2


def test_router_timeout_falls_back_to_chat(ai_router, monkeypatch):
    import requests

    def slow(*args, **kwargs):
        raise requests.Timeout("4 s")

    monkeypatch.setattr(http, "post_json", slow)
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: openai_stream("Here's my answer."))
    ctx = make_ctx()
    assert handle(ctx, "tell me something nice") == "ai"
    assert ctx.voice.last == "Here's my answer."


def test_router_model_is_configurable(ai_router, monkeypatch):
    monkeypatch.setattr(config, "AI_ROUTER_MODEL", "openai/gpt-oss-20b")
    ai_router.append(text_reply("Hello there, Anjali!"))
    handle(make_ctx(), "hey whats up buddy")
    assert ai_router.requests[0]["payload"]["model"] == "openai/gpt-oss-20b"


def test_routing_off_goes_straight_to_chat(ai_router, monkeypatch):
    monkeypatch.setattr(config, "AI_ROUTING", False)
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: openai_stream("Chat answer here."))
    assert handle(make_ctx(), "yaar thoda sound badha do") == "ai"
    assert ai_router.requests == []


# ---------------------------------------------------------------- Gemini format
def test_gemini_function_declarations_and_call(monkeypatch, laptop):
    monkeypatch.setattr(config, "AI_ROUTING", True)
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_MODEL", "gemini-2.5-flash")
    sent = []

    def post(url, payload, headers=None, timeout=None):
        sent.append((url, payload, headers))
        return Response({"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "volume", "args": {"direction": "down"}}}]}}]})

    monkeypatch.setattr(http, "post_json", post)
    assert handle(make_ctx(), "awaaz kam karo thodi") == "volume down"
    url, body, headers = sent[0]
    assert url.endswith("/models/gemini-2.5-flash:generateContent") and headers == {"x-goog-api-key": "test-key"}
    declarations = {d["name"]: d for d in body["tools"][0]["functionDeclarations"]}
    assert declarations["volume"]["parameters"]["properties"]["direction"]["type"] == "STRING"
    assert declarations["volume"]["parameters"]["required"] == ["direction"]
    assert "required" not in declarations["air_quality"]["parameters"]  # all-optional: no empty "required"
