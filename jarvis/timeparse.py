"""Parse spoken durations ("an hour and 30 minutes") and clock times ("at 6 pm")."""
import re
from datetime import datetime, timedelta

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "twenty five": 25, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60,
}
_NUM = r"(?:\d+(?:\.\d+)?|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + ")"
_UNIT = r"(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)\b"
_CHUNK = rf"{_NUM}\s*{_UNIT}(?:\s+and\s+a\s+half)?"
_DURATION = (
    rf"(?:(?:a\s+)?half\s+(?:an\s+)?hour|(?:a\s+)?quarter\s+(?:of\s+)?(?:an\s+)?hour"
    rf"|{_CHUNK}(?:\s*(?:,|and)?\s*{_CHUNK})*)"
)
DURATION_RE = re.compile(rf"(?:\b(?:in|after|for)\s+)?\b({_DURATION})", re.IGNORECASE)
_CHUNK_RE = re.compile(rf"({_NUM})\s*({_UNIT})(\s+and\s+a\s+half)?", re.IGNORECASE)

_AMPM = r"(a\.?\s?m\b\.?|p\.?\s?m\b\.?|o'?\s?clock\b)"
CLOCK_RE = re.compile(
    rf"\bat\s+(\d{{1,2}})(?:[:.](\d{{2}}))?\s*{_AMPM}?"
    rf"|\b(\d{{1,2}})(?:[:.](\d{{2}}))?\s*(a\.?\s?m\b\.?|p\.?\s?m\b\.?)"
    r"|\bat\s+(noon|midnight)\b",
    re.IGNORECASE,
)


def _number(token):
    token = token.lower().strip()
    return float(token) if token[0].isdigit() else NUMBER_WORDS[token]


def find_duration(text):
    """Return (seconds, start, end) for the first spoken duration in text, or None."""
    match = DURATION_RE.search(text or "")
    if not match:
        return None
    phrase = match.group(1).lower()
    if re.fullmatch(r"(?:a\s+)?half\s+(?:an\s+)?hour", phrase):
        seconds = 1800
    elif re.fullmatch(r"(?:a\s+)?quarter\s+(?:of\s+)?(?:an\s+)?hour", phrase):
        seconds = 900
    else:
        seconds = 0
        for chunk in _CHUNK_RE.finditer(phrase):
            amount = _number(chunk.group(1)) + (0.5 if chunk.group(3) else 0)
            unit = chunk.group(2).lower()
            seconds += amount * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    if seconds <= 0:
        return None
    return int(round(seconds)), match.start(), match.end()


def parse_duration(text):
    found = find_duration(text)
    return found[0] if found else None


def find_clock(text, now=None):
    """Return (datetime, start, end) for a clock time like 'at 6 pm', rolling to tomorrow if past."""
    now = now or datetime.now()
    match = CLOCK_RE.search(text or "")
    if not match:
        return None
    if match.group(7):
        hour, minute, suffix = (12 if match.group(7).lower() == "noon" else 0), 0, "fixed"
    elif match.group(1):
        hour, minute, suffix = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "")
    else:
        hour, minute, suffix = int(match.group(4)), int(match.group(5) or 0), match.group(6)
    suffix = suffix.lower().replace(".", "").replace(" ", "")
    if minute > 59 or hour > 23:
        return None
    if suffix in ("am", "pm"):
        if hour > 12:
            return None
        hour = hour % 12 + (12 if suffix == "pm" else 0)
        candidates = [hour]
    elif suffix == "fixed" or hour > 12 or hour == 0:
        candidates = [hour]
    else:  # "at 6" -> whichever of 6:00 / 18:00 comes next
        candidates = [hour % 12, hour % 12 + 12] if hour != 12 else [12, 0]
    for h in candidates:
        moment = now.replace(hour=h, minute=minute, second=0, microsecond=0)
        if moment > now:
            return moment, match.start(), match.end()
    moment = now.replace(hour=candidates[0], minute=minute, second=0, microsecond=0) + timedelta(days=1)
    return moment, match.start(), match.end()


def say_duration(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    for value, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second")):
        if value:
            parts.append(f"{value} {unit}{'' if value == 1 else 's'}")
    if not parts:
        return "0 seconds"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def say_clock(moment, now=None):
    now = now or datetime.now()
    hour = moment.hour % 12 or 12
    text = f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    if moment.minute == 0:
        text = f"{hour} {'AM' if moment.hour < 12 else 'PM'}"
    if moment.date() != now.date():
        text += " tomorrow" if moment.date() == (now + timedelta(days=1)).date() else moment.strftime(" on %A")
    return text
