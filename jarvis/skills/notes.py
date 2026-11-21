"""Notes (saved to a text file), reminders and timers."""
import itertools
import re
import threading
from datetime import datetime, timedelta

import config
from jarvis import notify
from jarvis.router import skill
from jarvis.timeparse import find_clock, find_duration, say_clock, say_duration


# ---------------------------------------------------------------- notes
def _notes_path():
    path = config.NOTES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_notes():
    path = config.NOTES_FILE
    if not path.exists():
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [line.split(" | ", 1)[-1] for line in lines]


def add_note(text):
    with _notes_path().open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M} | {text}\n")


@skill("clear notes", r"\b(?:clear|delete|erase|remove|wipe)\b.*\bnotes?\b", category='notes', help=['clear my notes'])
def clear_notes(ctx, command, match):
    notes = read_notes()
    if not notes:
        return "You don't have any notes to clear."
    count = f"{len(notes)} note{'s' if len(notes) != 1 else ''}"
    if not ctx.confirm(f"Are you sure you want to delete all {count}?"):
        return "Okay, I've kept your notes."
    config.NOTES_FILE.write_text("", encoding="utf-8")
    return f"Done. I deleted {count}."


@skill("read notes", r"\b(?:read|show|tell me|what are|list|open)\b.*\bnotes\b", r"\bmy notes\b", r"^notes$", category='notes', help=['read my notes'])
def read_notes_skill(ctx, command, match):
    notes = read_notes()
    if not notes:
        return "You don't have any notes yet. Say take a note to add one."
    if len(notes) == 1:
        return f"You have 1 note: {notes[0]}."
    latest = list(reversed(notes[-5:]))
    spoken = ". ".join(f"{i}: {text}" for i, text in enumerate(latest, 1))
    intro = f"You have {len(notes)} notes." + (" Here are the latest 5." if len(notes) > 5 else "")
    return f"{intro} {spoken}."


@skill("take note", r"\b(?:take|make|write|add|create)\s+(?:a\s+|down\s+a\s+|this\s+)?note(?:\s+(?:that|saying|of))?\s*:?\s*(.*)$",
       r"\bnote (?:down|this|that)\s*:?\s*(.*)$", r"^remember (?:that\s+)?(.+)$", r"\bremember that\s+(.+)$", category='notes', help=['take a note buy milk', 'remember that the meeting is at 5'])
def take_note(ctx, command, match):
    text = match.group(1).strip(" .:")
    if not text:
        text = ctx.ask("What should I note down?").strip(" .")
        if not text:
            return "Okay, never mind."
    add_note(text)
    return f"Noted: {text}."


# ---------------------------------------------------------------- reminders and timers
_ids = itertools.count(1)
_ALL_TIMERS = []  # every reminder/timer thread, so shutdown (and tests) can cancel them


def cancel_all_timers():
    while _ALL_TIMERS:
        _ALL_TIMERS.pop().cancel()


def _reminders(ctx):
    return ctx.state.setdefault("reminders", [])


def schedule(ctx, delay_seconds, message, kind="reminder"):
    entry = {"id": next(_ids), "when": datetime.now() + timedelta(seconds=delay_seconds), "text": message, "kind": kind}

    def fire():
        if entry in _reminders(ctx):
            _reminders(ctx).remove(entry)
        if kind == "timer":
            notify.toast("Timer done", f"Your {message} timer is done.")
            ctx.say(f"Time's up, {config.USER_NAME}! Your {message} timer is done.", wait=False)
        else:
            notify.toast("Reminder", message)  # visible even while muted or in a meeting
            ctx.say(f"Reminder, {config.USER_NAME}: {message}", wait=False)

    timer = threading.Timer(delay_seconds, fire)
    timer.daemon = True
    entry["timer"] = timer
    _ALL_TIMERS.append(timer)
    _reminders(ctx).append(entry)
    timer.start()
    return entry


def parse_when(text, now=None):
    """Return (seconds_from_now, remaining_text, spoken_when) or (None, text, None)."""
    now = now or datetime.now()
    found = find_duration(text)
    if found:
        seconds, start, end = found
        return seconds, text[:start] + " " + text[end:], f"in {say_duration(seconds)}"
    clock = find_clock(text, now)
    if clock:
        moment, start, end = clock
        return max(1, (moment - now).total_seconds()), text[:start] + " " + text[end:], f"at {say_clock(moment, now)}"
    return None, text, None


def clean_message(text):
    text = re.sub(r"\s+", " ", text).strip(" .,")
    text = re.sub(r"^(?:me\s+)?(?:to|that|about|of)\s+", "", text)
    text = re.sub(r"\s+(?:to|that)$", "", text)
    return text.strip(" .,")


@skill("cancel reminders", r"\b(?:cancel|delete|clear|stop|remove)\b.*\b(?:reminders?|timers?)\b", category='notes', help=['cancel my reminders'])
def cancel_reminders(ctx, command, match):
    active = _reminders(ctx)
    if not active:
        return "There's nothing to cancel."
    want = "timer" if re.search(r"\btimers?\b", command) and not re.search(r"\breminders?\b", command) else None
    cancelled = [e for e in active if want is None or e["kind"] == want]
    for entry in cancelled:
        entry["timer"].cancel()
        active.remove(entry)
    noun = want or "reminder"
    return f"Cancelled {len(cancelled)} {noun}{'s' if len(cancelled) != 1 else ''}."


@skill("list reminders", r"\b(?:list|show|what are|read|tell me|any)\b.*\b(?:reminders|timers)\b", r"\bmy (?:reminders|timers)\b", category='notes', help=['what are my reminders'])
def list_reminders(ctx, command, match):
    active = sorted(_reminders(ctx), key=lambda e: e["when"])
    if not active:
        return "You have no reminders or timers set."
    items = [
        (f"a {e['text']} timer" if e["kind"] == "timer" else e["text"]) + f" at {say_clock(e['when'])}" for e in active
    ]
    return f"You have {len(active)} active: " + "; ".join(items) + "."


@skill("timer", r"\b(?:set|start)\s+(?:a\s+|an\s+)?timer\b(.*)$", r"\btimer for\b(.*)$", r"^(.+?)\s+timer$", category='notes', help=['set a timer for 5 minutes'])
def timer_skill(ctx, command, match):
    seconds = find_duration(match.group(1))
    if not seconds:
        answer = ctx.ask("For how long?")
        seconds = find_duration(answer) if answer else None
        if not seconds:
            return "Sorry, I didn't get the duration. Try: set a timer for 5 minutes."
    seconds = seconds[0]
    schedule(ctx, seconds, say_duration(seconds), kind="timer")
    return f"Timer set for {say_duration(seconds)}."


@skill("reminder", r"\bremind me\b(.*)$", r"\bset (?:a\s+)?reminder\b(.*)$", category='notes', help=['remind me in 10 minutes to drink water', 'remind me at 6 pm to call mom'])
def reminder_skill(ctx, command, match):
    seconds, rest, when = parse_when(match.group(1))
    if seconds is None:
        answer = ctx.ask("When should I remind you?")
        if answer:
            seconds, _, when = parse_when(answer if re.match(r"\s*(?:in|at|after)\b", answer) else f"in {answer}")
            if seconds is None:
                seconds, _, when = parse_when(answer)
        if seconds is None:
            return "Sorry, I didn't catch the time. Try: remind me in 10 minutes to drink water."
    message = clean_message(rest)
    if not message:
        message = clean_message(ctx.ask("What should I remind you about?"))
        if not message:
            return "Okay, never mind."
    schedule(ctx, seconds, message)
    return f"Okay, I'll remind you {when}: {message}."
