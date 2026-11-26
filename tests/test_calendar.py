"""Calendar skill and day/time parsing tests. The Calendar API is faked."""
from datetime import date, datetime

import pytest
from fakes import FakeCalendar, event
from helpers import make_ctx

import config
from jarvis import google_auth, notify
from jarvis.google_auth import service as real_service
from jarvis.router import handle
from jarvis.skills import calendar
from jarvis.timeparse import find_datetime, find_day, say_day

NOW = datetime(2026, 11, 23, 9, 0)  # a Monday, 9 AM IST


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(calendar, "now", lambda: NOW)


def use(monkeypatch, fake):
    monkeypatch.setattr(google_auth, "service", lambda name, version: fake)
    return fake


# ---------------------------------------------------------------- time parsing
@pytest.mark.parametrize("text, expected", [
    ("today", date(2026, 11, 23)), ("tonight", date(2026, 11, 23)), ("tomorrow", date(2026, 11, 24)),
    ("day after tomorrow", date(2026, 11, 25)), ("on monday", date(2026, 11, 30)),
    ("this monday", date(2026, 11, 23)), ("next friday", date(2026, 11, 27)), ("on wed", date(2026, 11, 25)),
    ("on the 25th", date(2026, 11, 25)), ("the 3rd", date(2026, 12, 3)), ("25th december", date(2026, 12, 25)),
    ("january 5", date(2027, 1, 5)), ("on 1 nov", date(2027, 11, 1)),
])
def test_find_day(text, expected):
    assert find_day(f"meeting {text} please", NOW)[0] == expected


@pytest.mark.parametrize("text", ["for 30 minutes", "the month", "sunny weather", "in 5 minutes"])
def test_find_day_ignores(text):
    assert find_day(text, NOW) is None


@pytest.mark.parametrize("text, moment, has_time", [
    ("tomorrow at 5 pm", datetime(2026, 11, 24, 17, 0), True),
    ("on monday at 11", datetime(2026, 11, 30, 11, 0), True),   # 8-11 without am/pm: morning
    ("on friday at 4", datetime(2026, 11, 27, 16, 0), True),    # 1-7 without am/pm: afternoon
    ("tomorrow at 12", datetime(2026, 11, 24, 12, 0), True),
    ("today at 8", datetime(2026, 11, 23, 20, 0), True),        # today: the next 8 o'clock
    ("at 10:30 am", datetime(2026, 11, 23, 10, 30), True),
    ("at 6", datetime(2026, 11, 23, 18, 0), True),
    ("december 2 at 9:15 pm", datetime(2026, 12, 2, 21, 15), True),
    ("on thursday", datetime(2026, 11, 26, 0, 0), False),
])
def test_find_datetime(text, moment, has_time):
    found = find_datetime(text, NOW)
    assert found[:2] == (moment, has_time)


def test_say_day():
    assert say_day(datetime(2026, 11, 24), NOW) == "tomorrow"
    assert say_day(datetime(2026, 11, 27), NOW) == "on Friday"
    assert say_day(datetime(2026, 12, 1), NOW) == "on Tuesday 1 December"


@pytest.mark.parametrize("command, title, start, minutes", [
    ("add meeting with dhruv tomorrow at 5 pm for 30 minutes", "Meeting with Dhruv", datetime(2026, 11, 24, 17), 30),
    ("schedule dentist on monday at 11", "Dentist", datetime(2026, 11, 30, 11), 30),
    ("add a call with riya sharma on friday at 4 for an hour", "Call with Riya Sharma", datetime(2026, 11, 27, 16), 60),
    ("schedule standup in 2 hours for 15 minutes", "Standup", datetime(2026, 11, 23, 11), 15),
    ("add lunch with mom tomorrow at 1 to my calendar", "Lunch with Mom", datetime(2026, 11, 24, 13), 30),
    ("add an event called gym on wednesday at 7 am", "Gym", datetime(2026, 11, 25, 7), 30),
])
def test_parse_event(command, title, start, minutes):
    parsed = calendar.parse_event(command, NOW)
    assert (parsed["title"], parsed["start"], parsed["minutes"], parsed["has_time"]) == (title, start, minutes, True)


# ---------------------------------------------------------------- reading the calendar
def test_not_connected(monkeypatch):
    monkeypatch.setattr(google_auth, "service", real_service)
    for phrase in ("what's on my calendar today", "my next meeting", "cancel my 5 pm meeting"):
        ctx = make_ctx(["yes"])
        handle(ctx, phrase)
        assert ctx.voice.last == google_auth.NOT_CONNECTED


def test_today_in_ist(monkeypatch):
    fake = use(monkeypatch, FakeCalendar([
        event("1", "Standup", "2026-11-23T05:00:00Z"),               # UTC -> 10:30 AM IST
        event("2", "Meeting with Dhruv", "2026-11-23T17:00:00+05:30"),
        event("3", "Holiday", "2026-11-23", all_day=True),
    ]))
    ctx = make_ctx()
    assert handle(ctx, "what's on my calendar today") == "calendar today"
    assert ctx.voice.last == ("You have 3 events today. Standup at 10:30 AM, Meeting with Dhruv at 5 PM "
                              "and Holiday all day.")
    call = fake.list_calls[0]
    assert call["timeMin"] == "2026-11-23T08:00:00+05:30" and call["timeMax"] == "2026-11-24T00:00:00+05:30"
    assert call["timeZone"] == "Asia/Kolkata" and call["singleEvents"] is True


def test_tomorrow_week_and_clear(monkeypatch):
    fake = use(monkeypatch, FakeCalendar())
    ctx = make_ctx()
    handle(ctx, "what's on my calendar tomorrow")
    assert ctx.voice.last == "Your calendar is clear tomorrow."
    assert fake.list_calls[0]["timeMin"] == "2026-11-24T00:00:00+05:30"
    fake.items = [event("1", "Standup", "2026-11-24T10:00:00+05:30"), event("2", "Demo", "2026-11-27T15:00:00+05:30")]
    handle(ctx, "my meetings this week")
    assert ctx.voice.last == "You have 2 events this week. Standup at 10 AM tomorrow and Demo at 3 PM on Friday."
    assert fake.list_calls[1]["timeMax"] == "2026-11-30T00:00:00+05:30"


def test_next_meeting(monkeypatch):
    use(monkeypatch, FakeCalendar([event("0", "Holiday", "2026-11-23", all_day=True),
                                   event("1", "Standup", "2026-11-23T09:45:00+05:30")]))
    ctx = make_ctx()
    assert handle(ctx, "when is my next meeting") == "next meeting"
    assert ctx.voice.last == "Your next meeting is Standup at 9:45 AM today, in 45 minutes."


# ---------------------------------------------------------------- adding
def test_add_event_confirmed(monkeypatch):
    fake = use(monkeypatch, FakeCalendar())
    ctx = make_ctx(["yes"])
    assert handle(ctx, "add meeting with dhruv tomorrow at 5 pm for 30 minutes") == "add event"
    assert ctx.voice.said[0] == "Add Meeting with Dhruv tomorrow at 5 PM for 30 minutes?"
    assert fake.inserted == [{
        "summary": "Meeting with Dhruv",
        "start": {"dateTime": "2026-11-24T17:00:00", "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": "2026-11-24T17:30:00", "timeZone": "Asia/Kolkata"},
    }]
    assert ctx.voice.last == "Done. Meeting with Dhruv is on your calendar tomorrow at 5 PM."


def test_add_event_refused_creates_nothing(monkeypatch):
    fake = use(monkeypatch, FakeCalendar())
    ctx = make_ctx(["no"])
    handle(ctx, "schedule dentist on monday at 11")
    assert ctx.voice.said[0] == "Add Dentist on Monday 30 November at 11 AM for 30 minutes?"
    assert ctx.voice.last == "Okay, I didn't add it."
    assert fake.inserted == []


def test_add_event_asks_for_missing_time(monkeypatch):
    fake = use(monkeypatch, FakeCalendar())
    ctx = make_ctx(["5 pm", "yes"])
    handle(ctx, "schedule team sync on friday")
    assert ctx.voice.said[0] == "At what time?"
    assert ctx.voice.said[1] == "Add Team sync on Friday at 5 PM for 30 minutes?"
    assert fake.inserted[0]["start"]["dateTime"] == "2026-11-27T17:00:00"


def test_add_event_no_time_given_adds_nothing(monkeypatch):
    fake = use(monkeypatch, FakeCalendar())
    ctx = make_ctx(["umm"])
    handle(ctx, "add a meeting with dhruv")
    assert ctx.voice.said[0] == "When is Meeting with Dhruv?"
    assert "didn't add anything" in ctx.voice.last and fake.inserted == []


def test_add_event_in_the_past(monkeypatch):
    fake = use(monkeypatch, FakeCalendar())
    ctx = make_ctx(["yes"])
    handle(ctx, "schedule review today at 7 am")
    assert "already passed" in ctx.voice.last and fake.inserted == []


# ---------------------------------------------------------------- cancelling
EVENTS = [event("a", "Standup", "2026-11-23T10:00:00+05:30"),
          event("b", "Meeting with Dhruv", "2026-11-23T17:00:00+05:30"),
          event("c", "Gym", "2026-11-23T19:00:00+05:30")]


def test_cancel_by_time_confirmed(monkeypatch):
    fake = use(monkeypatch, FakeCalendar(EVENTS))
    ctx = make_ctx(["yes"])
    assert handle(ctx, "cancel my 5 pm meeting") == "cancel event"
    assert ctx.voice.said[0] == "Meeting with Dhruv at 5 PM today. Cancel it?"
    assert fake.deleted == ["b"] and ctx.voice.last == "Cancelled Meeting with Dhruv."


def test_cancel_by_name_refused(monkeypatch):
    fake = use(monkeypatch, FakeCalendar(EVENTS))
    ctx = make_ctx(["no"])
    handle(ctx, "cancel the meeting with dhruv")
    assert ctx.voice.said[0].startswith("Meeting with Dhruv")
    assert ctx.voice.last == "Okay, I kept it." and fake.deleted == []


def test_cancel_nothing_matches(monkeypatch):
    fake = use(monkeypatch, FakeCalendar(EVENTS))
    ctx = make_ctx(["yes"])
    handle(ctx, "cancel my 3 pm meeting")
    assert ctx.voice.last == "I couldn't find a matching event on your calendar."
    assert fake.deleted == [] and len(ctx.voice.said) == 1


# ---------------------------------------------------------------- reminders before events
def test_check_upcoming_schedules_alerts_once(monkeypatch):
    use(monkeypatch, FakeCalendar([event("a", "Standup", "2026-11-23T09:12:00+05:30"),
                                   event("b", "Demo", "2026-11-23T09:05:00+05:30"),
                                   event("h", "Holiday", "2026-11-23", all_day=True)]))
    scheduled = []
    monkeypatch.setattr(calendar, "schedule_alert", lambda delay, fn: scheduled.append((delay, fn)))
    ctx = make_ctx()
    assert calendar.check_upcoming(ctx, NOW) == ["a", "b"]
    assert [d for d, _ in scheduled] == [120.0, -300.0]  # Standup at 9:02; Demo is within 10 minutes: right away
    assert calendar.check_upcoming(ctx, NOW) == []       # not announced twice


def test_announce_speaks_and_toasts(monkeypatch):
    toasts = []
    monkeypatch.setattr(notify, "toast", lambda title, message, **kw: toasts.append((title, message)))
    ctx = make_ctx()
    calendar.announce(ctx, event("a", "Standup", "2026-11-23T09:10:00+05:30"), NOW)
    assert ctx.voice.last == "Heads up, Anjali: Standup starts in 10 minutes."
    assert toasts == [("Upcoming event", "Standup at 9:10 AM")]


def test_reminders_only_start_when_connected(monkeypatch):
    ctx = make_ctx()
    assert calendar.start_reminders(ctx) is False
    config.GOOGLE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_TOKEN_FILE.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "CALENDAR_REMINDERS", False)
    assert calendar.start_reminders(ctx) is False
