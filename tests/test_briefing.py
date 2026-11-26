"""Morning briefing and good night. Every section is mocked; nothing goes to the network."""
import logging
import time
from datetime import date, datetime, timedelta

import pytest
from fakes import FakeCalendar, FakeGmail, event, mail
from helpers import FakeVoice, make_ctx

import config
from jarvis import google_auth, notify
from jarvis.geo import Place
from jarvis.router import Context, handle
from jarvis.skills import briefing, calendar, gmail, system, weather, web
from jarvis.skills import aqi as aqi_skill

NOW = datetime(2026, 11, 23, 8, 30)  # Monday morning
S = briefing.Section


def fake_sections(monkeypatch, **overrides):
    """Replace every section with a quick fake. Pass name=None to make it 'not connected'."""
    sections = {
        "weather": lambda ctx: S("Today in New Delhi: 12 to 24 degrees, clear sky, 0 percent chance of rain."),
        "aqi": lambda ctx: S("Air quality in New Delhi is 261, Poor, a red zone. Wear a mask outside."),
        "calendar": lambda ctx: S("You have 2 events today: Standup at 10 AM; Demo at 3 PM."),
        "email": lambda ctx: S("4 unread emails, from Dhruv and Amazon."),
        "reminders": lambda ctx: S("Reminders today: call mom at 6 PM."),
        "news": lambda ctx: S("Top headline: Delhi gets its first cold wave of the season."),
    }
    sections.update(overrides)
    monkeypatch.setattr(briefing, "SECTIONS", {k: (v if v else (lambda ctx: None)) for k, v in sections.items()})
    monkeypatch.setattr(briefing, "today", lambda: NOW)


# ---------------------------------------------------------------- the briefing
def test_good_morning_gives_briefing_in_order(monkeypatch, caplog):
    fake_sections(monkeypatch)
    ctx = make_ctx()
    with caplog.at_level(logging.INFO):
        assert handle(ctx, "good morning jarvis") == "morning briefing"
    spoken = ctx.voice.said[0]
    assert len(ctx.voice.said) == 1  # ONE briefing
    order = ["Good morning, Anjali. It's Monday, 23 November.", "Today in New Delhi", "Air quality",
             "You have 2 events", "4 unread emails", "Reminders today", "Top headline"]
    positions = [spoken.index(piece) for piece in order]
    assert positions == sorted(positions)
    assert "=== Morning briefing ===" in caplog.text and "Top headline" in caplog.text  # full version printed


@pytest.mark.parametrize("phrase", ["good morning", "morning briefing", "brief me", "give me my daily briefing"])
def test_briefing_phrases(monkeypatch, phrase):
    fake_sections(monkeypatch)
    assert handle(make_ctx(), phrase) == "morning briefing"


def test_plain_greetings_still_work():
    assert handle(make_ctx(), "hello jarvis") == "greet"
    assert handle(make_ctx(), "good evening") == "greet"


def test_sections_follow_configured_order(monkeypatch):
    fake_sections(monkeypatch)
    monkeypatch.setattr(config, "BRIEFING_SECTIONS", ["news", "weather", "bogus"])
    ctx = make_ctx()
    handle(ctx, "brief me")
    spoken = ctx.voice.said[0]
    assert spoken.index("Top headline") < spoken.index("Today in New Delhi")
    assert "Air quality" not in spoken and "unread" not in spoken


def test_failing_and_unconnected_sections_are_skipped_silently(monkeypatch):
    def broken(ctx):
        raise ConnectionError("offline")

    fake_sections(monkeypatch, aqi=broken, email=None, calendar=None)
    ctx = make_ctx()
    handle(ctx, "morning briefing")
    spoken = ctx.voice.said[0]
    assert "Today in New Delhi" in spoken and "Top headline" in spoken
    assert "Air quality" not in spoken and "unread" not in spoken and "events" not in spoken
    assert not any(word in spoken.lower() for word in ("sorry", "error", "couldn't", "connect"))


def test_sections_run_in_parallel_with_a_timeout(monkeypatch):
    import threading

    together = threading.Barrier(3, timeout=5)  # the quick ones only get past it if they run at the same time
    stuck = threading.Event()

    def slow(ctx):
        stuck.wait(10)
        return S("Too late to be included.")

    def quick(ctx):
        together.wait()
        return S("Quick section here.")

    fake_sections(monkeypatch)
    jobs = {"a": quick, "b": quick, "c": quick, "slow": slow}
    started = time.monotonic()
    try:
        found = briefing.gather(make_ctx(), jobs, timeout=2.0)
    finally:
        stuck.set()
    elapsed = time.monotonic() - started
    assert set(found) == {"a", "b", "c"}  # all three met at the barrier (parallel), the slow one was dropped
    assert elapsed < 5.0                   # cut off at the 2 s timeout, not the slow section's 10 s


def test_default_timeout_is_six_seconds():
    assert briefing.SECTION_TIMEOUT == 6.0


def test_forty_second_budget(monkeypatch):
    long_text = " ".join(["word"] * 45) + "."
    fake_sections(monkeypatch, weather=lambda ctx: S(long_text), aqi=lambda ctx: S(long_text),
                  calendar=lambda ctx: S(long_text))
    ctx = make_ctx()
    handle(ctx, "brief me")
    spoken = ctx.voice.said[0]
    assert briefing.spoken_seconds(spoken) <= 40
    assert spoken.startswith("Good morning, Anjali.")


def test_real_sections_fit_the_budget(monkeypatch):
    """With every section at its realistic longest, the briefing still fits 40 s at 2.5 words/s."""
    fake_sections(monkeypatch)
    spoken, _ = briefing.build(make_ctx(), now=NOW)
    assert briefing.word_count(spoken) <= briefing.MAX_WORDS
    assert briefing.spoken_seconds(spoken) <= 40


def test_briefing_music(monkeypatch):
    fake_sections(monkeypatch)
    monkeypatch.setattr(config, "BRIEFING_MUSIC", "lofi beats")
    played = []
    from jarvis.skills import spotify

    monkeypatch.setattr(spotify, "client", lambda: None)
    monkeypatch.setattr(spotify, "open_uri", played.append)
    handle(make_ctx(), "good morning")
    assert played == ["spotify:search:lofi%20beats"]


def test_hinglish_in_hindi_mode(monkeypatch):
    fake_sections(monkeypatch)
    from jarvis.skills import ai

    monkeypatch.setattr(config, "AI_API_KEY", "test")
    sent = []
    monkeypatch.setattr(ai, "complete", lambda system, history, text, max_tokens=150: sent.append(text) or (
        200, "Good morning Anjali! Aaj Delhi mein 12 se 24 degree rahega."))
    voice = FakeVoice()
    voice.language = "hi-IN"
    ctx = Context(voice)
    handle(ctx, "good morning")
    assert voice.said == ["Good morning Anjali! Aaj Delhi mein 12 se 24 degree rahega."]
    assert "Today in New Delhi" in sent[0]


def test_hindi_mode_without_ai_key_stays_english(monkeypatch):
    fake_sections(monkeypatch)
    voice = FakeVoice()
    voice.language = "hi-IN"
    ctx = Context(voice)
    handle(ctx, "good morning")
    assert voice.said[0].startswith("Good morning, Anjali.")


def test_hinglish_rejects_devanagari(monkeypatch):
    from jarvis.skills import ai

    monkeypatch.setattr(config, "AI_API_KEY", "test")
    monkeypatch.setattr(ai, "complete", lambda *a, **k: (200, "सुप्रभात"))
    assert briefing.to_hinglish("Good morning.") == "Good morning."


# ---------------------------------------------------------------- real section builders (APIs mocked)
def test_weather_and_aqi_sections(monkeypatch):
    ctx = make_ctx()
    ctx.state["location"] = Place("Rohini", 28.7, 77.1)
    monkeypatch.setattr(weather, "forecast", lambda place: {"daily": {
        "weather_code": [0, 61], "temperature_2m_max": [24.2, 22.0], "temperature_2m_min": [11.6, 10.0],
        "precipitation_probability_max": [10, 70]}})
    assert briefing.weather_section(ctx).spoken == ("Today in Rohini: 12 to 24 degrees, clear skies, "
                                                    "10 percent chance of rain.")
    assert briefing.weather_section(ctx, 1).spoken.startswith("Tomorrow in Rohini: 10 to 22 degrees")
    reading = aqi_skill.AirReading(ctx.state["location"], {}, {}, 261, "pm2_5")
    monkeypatch.setattr(aqi_skill, "fetch_air", lambda places: [reading])
    assert briefing.aqi_section(ctx).spoken == ("Air quality in Rohini is 261, Poor, a red zone. "
                                                "Wear a mask outside.")


def test_calendar_and_email_sections(monkeypatch):
    ctx = make_ctx()
    assert briefing.calendar_section(ctx) is None and briefing.email_section(ctx) is None  # not connected
    monkeypatch.setattr(google_auth, "is_connected", lambda: True)
    monkeypatch.setattr(calendar, "now", lambda: NOW)
    items = [event(str(i), f"Meeting {i}", f"2026-11-23T{10 + i}:00:00+05:30") for i in range(5)]
    fake_cal = FakeCalendar(items)
    messages = [mail(str(i), f"Person {i} <p{i}@x.com>", f"Subject {i}") for i in range(4)]
    fake_mail = FakeGmail(messages, {gmail.PRIMARY_UNREAD: ["0", "1", "2", "3"]})
    monkeypatch.setattr(google_auth, "service", lambda name, version: fake_cal if name == "calendar" else fake_mail)
    section = briefing.calendar_section(ctx)
    assert section.spoken == ("You have 5 events today: Meeting 0 at 10 AM; Meeting 1 at 11 AM; Meeting 2 at 12 PM; "
                              "and more.")
    assert "Meeting 4" in section.full
    assert briefing.email_section(ctx).spoken == "4 unread emails, from Person 0 and Person 1."


def test_reminders_and_news_sections(monkeypatch):
    monkeypatch.setattr(briefing, "today", lambda: NOW)
    ctx = make_ctx()
    assert briefing.reminders_section(ctx) is None
    ctx.state["reminders"] = [
        {"text": "call mom", "kind": "reminder", "when": NOW.replace(hour=18, minute=0)},
        {"text": "pay rent", "kind": "reminder", "when": NOW + timedelta(days=1)},
    ]
    assert briefing.reminders_section(ctx).spoken == "Reminders today: call mom at 6 PM."
    assert briefing.reminders_section(ctx, 1).spoken == "Reminders for tomorrow: pay rent at 8:30 AM."
    monkeypatch.setattr(web, "headlines", lambda topic="", limit=5: ["Cold wave hits Delhi"])
    assert briefing.news_section(ctx).spoken == "Top headline: Cold wave hits Delhi."


# ---------------------------------------------------------------- good night
def test_good_night(monkeypatch):
    monkeypatch.setattr(briefing, "today", lambda: NOW)
    monkeypatch.setattr(briefing, "NIGHT_SECTIONS", {
        "calendar": lambda ctx: S("Tomorrow starts with Standup at 10 AM."),
        "weather": lambda ctx: S("Tomorrow in New Delhi: 10 to 22 degrees, rain, 70 percent chance of rain."),
        "reminders": lambda ctx: None,
    })
    pressed, scheduled = [], []
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "press", lambda key, presses=1: pressed.append((key, presses)))
    from jarvis.skills import notes

    monkeypatch.setattr(notes, "schedule", lambda ctx, seconds, text, kind="reminder": scheduled.append(text))
    ctx = make_ctx(["7 am"])
    assert handle(ctx, "good night jarvis") == "good night"
    assert ctx.voice.said[0] == ("Good night, Anjali. Tomorrow starts with Standup at 10 AM. Tomorrow in New Delhi: "
                                 "10 to 22 degrees, rain, 70 percent chance of rain.")
    assert ctx.voice.said[1].startswith("Shall I set a wake-up reminder?")
    assert scheduled == ["Good morning! Time to wake up."]
    assert ctx.voice.last == ("Wake-up reminder set for 7 AM tomorrow. I've turned the volume down to 20 percent. "
                              "Sleep well.")
    assert pressed == [("volumedown", 50), ("volumeup", 10)]  # 0%, then 10 x 2% = 20%


def test_good_night_declining_alarm(monkeypatch):
    monkeypatch.setattr(briefing, "NIGHT_SECTIONS", {"weather": lambda ctx: None})
    monkeypatch.setattr(system, "IS_WINDOWS", False)
    from jarvis.skills import notes

    monkeypatch.setattr(notes, "schedule", lambda *a, **k: pytest.fail("no alarm was asked for"))
    ctx = make_ctx(["no thanks"])
    handle(ctx, "good night")
    assert ctx.voice.said[0] == "Good night, Anjali."
    assert ctx.voice.last == "Sleep well."


def test_tomorrow_event_section(monkeypatch):
    monkeypatch.setattr(google_auth, "is_connected", lambda: True)
    monkeypatch.setattr(calendar, "now", lambda: NOW)
    fake_cal = FakeCalendar([event("h", "Holiday", "2026-11-24", all_day=True),
                             event("a", "Standup", "2026-11-24T10:00:00+05:30")])
    monkeypatch.setattr(google_auth, "service", lambda name, version: fake_cal)
    assert briefing.tomorrow_event_section(make_ctx()).spoken == "Tomorrow starts with Standup at 10 AM, one of 2 events."
    assert fake_cal.list_calls[0]["timeMin"] == "2026-11-24T00:00:00+05:30"


# ---------------------------------------------------------------- automatic briefing once a day
@pytest.mark.parametrize("now, idle, last_done, expected", [
    (datetime(2026, 11, 23, 8, 29), 999, None, False),   # too early
    (datetime(2026, 11, 23, 8, 30), 999, None, True),
    (datetime(2026, 11, 23, 9, 45), 999, None, True),
    (datetime(2026, 11, 23, 9, 45), 30, None, False),    # I'm in the middle of talking to Jarvis
    (datetime(2026, 11, 23, 9, 45), 999, date(2026, 11, 23), False),  # already done today
    (datetime(2026, 11, 24, 8, 40), 999, date(2026, 11, 23), True),   # next day
    (datetime(2026, 11, 23, 12, 0), 999, None, False),   # more than 3 hours late
])
def test_due(monkeypatch, now, idle, last_done, expected):
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:30")
    assert briefing.due(now, idle, last_done) is expected


@pytest.mark.parametrize("value", ["", "8.30am", "25:00", "morning"])
def test_due_off_or_invalid(monkeypatch, value):
    monkeypatch.setattr(config, "BRIEFING_TIME", value)
    assert briefing.due(datetime(2026, 11, 23, 9, 0), 999, None) is False


def test_tick_runs_once_per_day_with_toast(monkeypatch):
    fake_sections(monkeypatch)
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:30")
    toasts = []
    monkeypatch.setattr(notify, "toast", lambda title, message: toasts.append(title))
    ctx = make_ctx()
    assert briefing.tick(ctx, datetime(2026, 11, 23, 8, 31)) is True
    assert toasts == ["Morning briefing"] and ctx.voice.said[0].startswith("Good morning, Anjali.")
    assert briefing.done_on() == date(2026, 11, 23)  # remembered in data/cache.json
    assert briefing.tick(ctx, datetime(2026, 11, 23, 9, 0)) is False
    assert len(ctx.voice.said) == 1 and toasts == ["Morning briefing"]


def test_tick_waits_while_busy_or_asleep(monkeypatch):
    fake_sections(monkeypatch)
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:30")
    monkeypatch.setattr(notify, "toast", lambda *a: None)

    class Busy:
        _last_reply = time.monotonic()  # just talked to Jarvis

    ctx = make_ctx()
    ctx.state["assistant"] = Busy()
    assert briefing.tick(ctx, datetime(2026, 11, 23, 8, 31)) is False
    ctx.state.pop("assistant")
    ctx.sleeping = True
    assert briefing.tick(ctx, datetime(2026, 11, 23, 8, 31)) is False
    assert ctx.voice.said == [] and briefing.done_on() is None


def test_scheduler_only_starts_with_a_valid_time(monkeypatch):
    import threading

    started = []

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            started.append(name)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", FakeThread)
    briefing._start_scheduler(make_ctx())
    monkeypatch.setattr(config, "BRIEFING_TIME", "8 am")
    briefing._start_scheduler(make_ctx())
    assert started == []
    monkeypatch.setattr(config, "BRIEFING_TIME", "07:45")
    briefing._start_scheduler(make_ctx())
    assert started == ["briefing-scheduler"]


def test_help_with_briefing():
    ctx = make_ctx()
    assert handle(ctx, "help with briefing") == "help category"
    assert ctx.voice.said[0].startswith("For daily briefing, you can say: Good morning Jarvis. Good night Jarvis.")
