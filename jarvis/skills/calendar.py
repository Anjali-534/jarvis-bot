"""Google Calendar: what's on today / tomorrow / this week, next meeting, add and cancel events.

All times are spoken in IST. Adding or cancelling always reads the event back and asks first.
While Google is connected, a background thread gives a spoken reminder and a toast
CALENDAR_REMINDER_MINUTES before each event (polling every 5 minutes).
"""
import logging
import re
import threading
from datetime import datetime, time, timedelta, timezone

import config
from jarvis import google_auth, notify
from jarvis.google_auth import friendly_errors
from jarvis.router import on_startup, skill, speak_list
from jarvis.timeparse import (
    DURATION_RE, MONTHS, clock_parts, find_datetime, find_day, parse_duration, remove_spans, say_clock, say_day,
    say_duration,
)

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30), "IST")
DEFAULT_MINUTES = 30
MAX_SPOKEN = 8
POLL_SECONDS = 300
EVENT = r"(?:meeting|event|appointment|call|session|class|interview)"
CAL = r"(?:calendar|schedule|agenda)"


def now():
    """The current time in IST, as a naive datetime (everything here is IST wall-clock time)."""
    return datetime.now(IST).replace(tzinfo=None)


def calendar_api():
    return google_auth.service("calendar", "v3")


# ---------------------------------------------------------------- events
def to_ist(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo:
        moment = moment.astimezone(IST).replace(tzinfo=None)
    return moment


def event_start(event):
    """(naive IST datetime, all_day) for an event."""
    start = event.get("start", {})
    if "dateTime" in start:
        return to_ist(start["dateTime"]), False
    return datetime.fromisoformat(start.get("date", "1970-01-01")), True


def title_of(event):
    return (event.get("summary") or "Untitled event").strip()


def _rfc3339(moment):
    return moment.replace(tzinfo=IST).isoformat()


def fetch_events(service, start, end, limit=50):
    result = service.events().list(
        calendarId="primary", timeMin=_rfc3339(start), timeMax=_rfc3339(end), singleEvents=True,
        orderBy="startTime", maxResults=limit, timeZone=config.TIMEZONE,
    ).execute()
    return [e for e in result.get("items", []) if e.get("status") != "cancelled"]


def describe(event, current, with_day=False):
    start, all_day = event_start(event)
    if all_day:
        when = "all day" + (f" {say_day(start, current)}" if with_day else "")
    else:
        when = f"at {say_clock(start, start)}" + (f" {say_day(start, current)}" if with_day else "")
    return f"{title_of(event)} {when}"


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------- what's on
def parse_range(command, current):
    """(spoken phrase, start, end) for a 'what's on my calendar ...' question: today, tomorrow, a day, the week."""
    if re.search(r"\bweek\b|\bnext (?:7|seven) days\b", command):
        return "this week", current, datetime.combine(current.date() + timedelta(days=7), time())
    day = find_day(command, current)
    target = day[0] if day else current.date()
    start = datetime.combine(target, time())
    if target == current.date():
        start = max(start, current - timedelta(hours=1))  # skip what's long over
    return say_day(target, current), start, datetime.combine(target + timedelta(days=1), time())


@skill("calendar today",
       rf"\bwhat(?:'s| is| do i have)\b.*\b(?:on|in)\s+(?:my\s+)?{CAL}\b",
       rf"\b(?:check|read|show)\s+(?:me\s+)?(?:my\s+)?{CAL}\b", r"^(?:my\s+)?(?:calendar|agenda)(?:\s+.+)?$",
       r"^my schedule(?:\s+.+)?$", rf"\b(?:any|my)\s+{EVENT}s\b", r"\bam i (?:free|busy)\b",
       rf"\bwhat(?:'s| is) my {CAL}\b", r"^what do i have (?:on\s+)?(?:today|tomorrow|this week|\w+day)\b",
       category="calendar", help=["what's on my calendar today", "what's on my calendar tomorrow",
                                  "my meetings this week"])
@friendly_errors
def calendar_today(ctx, command, match):
    if re.search(rf"^(?:please\s+)?(?:{_VERB}|cancel|delete|remove)\b", command):
        return None  # "cancel my meetings tomorrow", "schedule ..." belong to other skills
    current = now()
    phrase, start, end = parse_range(command, current)
    events = fetch_events(calendar_api(), start, end)
    if not events:
        return f"Your calendar is clear {phrase}."
    spoken = [describe(e, current, with_day=phrase == "this week") for e in events]
    count = f"{len(events)} event{'s' if len(events) != 1 else ''}"
    return speak_list(ctx, f"You have {count} {phrase}.", spoken)


@skill("next meeting", rf"\b(?:my\s+)?next\s+{EVENT}\b", rf"\bwhen is my next\b", rf"\bwhat(?:'s| is) next\b.*{CAL}",
       category="calendar", help=["my next meeting"])
@friendly_errors
def next_meeting(ctx, command, match):
    current = now()
    events = fetch_events(calendar_api(), current, current + timedelta(days=14), limit=10)
    upcoming = [e for e in events if not event_start(e)[1] and event_start(e)[0] >= current - timedelta(minutes=5)]
    if not upcoming:
        return "You have no meetings in the next two weeks."
    event = upcoming[0]
    start, _ = event_start(event)
    minutes = int((start - current).total_seconds() // 60)
    when = f"at {say_clock(start, start)} {say_day(start, current)}"
    if minutes <= 0:
        return f"{title_of(event)} is starting now."
    soon = f", in {say_duration(minutes * 60)}" if minutes < 120 else ""
    return f"Your next meeting is {title_of(event)} {when}{soon}."


# ---------------------------------------------------------------- add
_VERB = r"(?:add|schedule|create|book|set up|setup|put|plan|make)"


def parse_event(command, current):
    """'add meeting with dhruv tomorrow at 5 pm for 30 minutes' -> dict(title, start, minutes, has_time).

    start is None if no day or time was said.
    """
    text = command.strip().rstrip(" .?!")
    minutes, offset, spans = None, None, []
    for found in DURATION_RE.finditer(text):
        seconds = parse_duration(found.group(1))
        if not seconds:
            continue
        prefix = text[found.start():found.start(1)].strip().lower()
        if prefix in ("in", "after") and offset is None:  # "in an hour" is when it starts, not how long
            offset = seconds
        elif minutes is None:
            minutes = max(1, seconds // 60)
        else:
            continue
        spans.append((found.start(), found.end()))
    rest = remove_spans(text, spans)
    start, has_time = None, False
    if offset is not None:
        start, has_time = (current + timedelta(seconds=offset)).replace(second=0, microsecond=0), True
    else:
        when = find_datetime(rest, current)
        if when:
            start, has_time, used = when
            rest = remove_spans(rest, used)
    return {"title": clean_title(rest), "start": start, "minutes": minutes or DEFAULT_MINUTES, "has_time": has_time}


def time_from_answer(answer, day, current):
    """The start time from a follow-up answer like '5 pm' or 'tomorrow at 11', keeping a day already said."""
    answer = answer.strip()
    if not re.search(r"\bat\b", answer):
        answer = f"at {answer}"
    if day and not find_day(answer, current):
        answer = f"{day.day} {MONTHS[day.month - 1]} {answer}"
    when = find_datetime(answer, current)
    return when[0] if when and when[1] else None


def clean_title(text):
    text = re.sub(rf"^(?:please\s+)?{_VERB}\s+", "", text.strip())
    text = re.sub(r"^(?:a|an|the|my)\s+", "", text)
    text = re.sub(r"^(?:new\s+)?(?:event|calendar event)\s+(?:called|named|for|titled)?\s*", "", text)
    text = re.sub(rf"\s+(?:to|on|in|into)\s+(?:my\s+|the\s+)?{CAL}$", "", text)
    text = re.sub(r"\s+(?:at|on|for|from)$", "", text).strip(" ,.")
    # Names after "with" get capitals: "meeting with dhruv" -> "Meeting with Dhruv"
    text = re.sub(r"\b(with|and)\s+([a-z][\w']*(?:\s+(?!about|for|regarding|on|at)[a-z][\w']*)?)",
                  lambda m: f"{m.group(1)} {m.group(2).title()}", text)
    return text[:1].upper() + text[1:]


def create_event(service, title, start, minutes):
    body = {
        "summary": title,
        "start": {"dateTime": start.isoformat(timespec="seconds"), "timeZone": config.TIMEZONE},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
                "timeZone": config.TIMEZONE},
    }
    return service.events().insert(calendarId="primary", body=body).execute()


@skill("add event",
       rf"^(?:please\s+)?{_VERB}\s+(?:a\s+|an\s+|the\s+|my\s+)?(?:new\s+)?{EVENT}\b.*$",
       rf"^(?:please\s+)?{_VERB}\s+.+\b(?:to|on|in|into)\s+(?:my\s+|the\s+)?calendar$",
       r"^(?:please\s+)?schedule\s+(?!(?:a\s+)?(?:shut\s?down|restart|reminder|timer)\b).+$",
       category="calendar", help=["add meeting with Dhruv tomorrow at 5 pm for 30 minutes",
                                  "schedule dentist on Monday at 11"])
@friendly_errors
def add_event(ctx, command, match):
    current = now()
    event = parse_event(command, current)
    if not event["title"]:
        event["title"] = clean_title(ctx.ask("What should I call the event?"))
        if not event["title"]:
            return "Okay, never mind."
    if not event["has_time"]:
        day = event["start"].date() if event["start"] else None
        answer = ctx.ask("At what time?" if day else f"When is {event['title']}?")
        start = time_from_answer(answer, day, current) if answer else None
        if not start:
            return "Sorry, I didn't catch the time, so I didn't add anything. Try: add meeting with Dhruv tomorrow at 5 pm."
        event["start"] = start
    start = event["start"]
    if start < current - timedelta(minutes=1):
        return f"{say_clock(start, current)} has already passed. Please give me a time in the future."
    spoken = (f"Add {event['title']} {say_day(start, current)} at {say_clock(start, start)} "
              f"for {say_duration(event['minutes'] * 60)}?")
    if not ctx.confirm(spoken):
        return "Okay, I didn't add it."
    create_event(calendar_api(), event["title"], start, event["minutes"])
    return f"Done. {event['title']} is on your calendar {say_day(start, current)} at {say_clock(start, start)}."


# ---------------------------------------------------------------- cancel
_FILLER = {"cancel", "delete", "remove", "drop", "call", "off", "my", "the", "a", "an", "with", "at", "on", "for",
           "from", "calendar", "schedule", "meeting", "event", "appointment", "session", "class", "interview",
           "please", "today", "tomorrow", "am", "pm", "to", "in", "of"}


def find_to_cancel(service, command, current):
    """Events matching 'cancel my 5 pm meeting' / 'cancel the meeting with dhruv tomorrow', soonest first."""
    day = find_day(command, current)
    if day:
        start = datetime.combine(day[0], time())
        events = fetch_events(service, start, start + timedelta(days=1))
    else:
        events = fetch_events(service, current - timedelta(hours=1), current + timedelta(days=7))
    clock = clock_parts(command)
    rest = remove_spans(command, [found for found in (day[1:] if day else None, clock[3:] if clock else None) if found])
    words = [w for w in re.findall(r"[a-z0-9']+", rest.lower()) if w not in _FILLER and not w.isdigit()]
    matches = []
    for event in events:
        start, all_day = event_start(event)
        if clock:
            hour, minute, exact, _, _ = clock
            if all_day or start.minute != minute:
                continue
            if (start.hour != hour) if exact else (start.hour % 12 != hour % 12):
                continue
        title = title_of(event).lower()
        if words and not any(w in title for w in words):
            continue
        matches.append(event)
    return matches


@skill("cancel event", rf"\b(?:cancel|delete|remove|drop|call off)\b.*\b{EVENT}s?\b",
       rf"\b(?:cancel|delete|remove)\b.*\b(?:from|on|in)\s+(?:my\s+)?calendar\b",
       category="calendar", help=["cancel my 5 pm meeting"])
@friendly_errors
def cancel_event(ctx, command, match):
    current = now()
    service = calendar_api()
    matches = find_to_cancel(service, command, current)
    if not matches:
        return "I couldn't find a matching event on your calendar."
    event = matches[0]
    note = f"I found {len(matches)} matches; the first is " if len(matches) > 1 else ""
    if not ctx.confirm(f"{note}{describe(event, current, with_day=True)}. Cancel it?"):
        return "Okay, I kept it."
    service.events().delete(calendarId="primary", eventId=event["id"]).execute()
    return f"Cancelled {title_of(event)}."


# ---------------------------------------------------------------- reminders before events
_stop = threading.Event()
_thread = None
_alerted = set()        # event ids already scheduled/announced
_pending = []           # threading.Timer objects for alerts not yet fired


def schedule_alert(delay, fn):
    timer = threading.Timer(max(0.0, delay), fn)
    timer.daemon = True
    _pending.append(timer)
    timer.start()
    return timer


def announce(ctx, event, current=None):
    current = current or now()
    start, _ = event_start(event)
    minutes = max(0, round((start - current).total_seconds() / 60))
    soon = f"in {say_duration(minutes * 60)}" if minutes else "now"
    notify.toast("Upcoming event", f"{title_of(event)} at {say_clock(start, start)}")
    ctx.say(f"Heads up, {config.USER_NAME}: {title_of(event)} starts {soon}.", wait=False)


def check_upcoming(ctx, current=None):
    """Schedule a reminder for every timed event starting within the next poll window. Returns new event ids."""
    current = current or now()
    lead = timedelta(minutes=config.CALENDAR_REMINDER_MINUTES)
    window_end = current + lead + timedelta(seconds=POLL_SECONDS + 60)
    events = fetch_events(calendar_api(), current, window_end, limit=20)
    new = []
    for event in events:
        start, all_day = event_start(event)
        key = f"{event.get('id')}@{start.isoformat()}"
        if all_day or key in _alerted or start <= current:
            continue
        _alerted.add(key)
        delay = (start - lead - current).total_seconds()
        schedule_alert(delay, lambda e=event: announce(ctx, e))
        new.append(event.get("id"))
    return new


def _loop(ctx):
    while not _stop.is_set() and ctx.running:
        try:
            if google_auth.is_connected():
                check_upcoming(ctx)
        except Exception as exc:  # offline, token revoked... try again next round
            log.warning("[calendar] reminder check failed: %s", exc)
        _stop.wait(POLL_SECONDS)


def start_reminders(ctx):
    """Start the background poller (once) if reminders are on and Google is connected."""
    global _thread
    if not config.CALENDAR_REMINDERS or not google_auth.is_connected():
        return False
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(ctx,), name="calendar-reminders", daemon=True)
    _thread.start()
    log.info("[calendar] event reminders on (%s minutes before)", config.CALENDAR_REMINDER_MINUTES)
    return True


def stop_reminders():
    _stop.set()
    while _pending:
        _pending.pop().cancel()
    _alerted.clear()


@on_startup
def _startup(ctx):
    start_reminders(ctx)
