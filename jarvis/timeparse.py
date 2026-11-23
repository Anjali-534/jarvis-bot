"""Parse spoken durations ("an hour and 30 minutes"), clock times ("at 6 pm") and days ("tomorrow", "on Monday")."""
import re
from datetime import date, datetime, time, timedelta

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


def clock_parts(text):
    """Return (hour, minute, exact, start, end) for the first clock time in text, or None.

    exact is False for "at 6" / "at 6 o'clock", which could be 6 AM or 6 PM (hour is then 1-12).
    """
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
        return hour % 12 + (12 if suffix == "pm" else 0), minute, True, match.start(), match.end()
    exact = suffix == "fixed" or hour > 12 or hour == 0
    return hour, minute, exact, match.start(), match.end()


def _candidates(hour, exact):
    if exact:
        return [hour]
    return [hour % 12, hour % 12 + 12] if hour != 12 else [12, 0]


def find_clock(text, now=None):
    """Return (datetime, start, end) for a clock time like 'at 6 pm', rolling to tomorrow if past."""
    now = now or datetime.now()
    parts = clock_parts(text)
    if not parts:
        return None
    hour, minute, exact, start, end = parts
    candidates = _candidates(hour, exact)  # "at 6" -> whichever of 6:00 / 18:00 comes next
    for h in candidates:
        moment = now.replace(hour=h, minute=minute, second=0, microsecond=0)
        if moment > now:
            return moment, start, end
    moment = now.replace(hour=candidates[0], minute=minute, second=0, microsecond=0) + timedelta(days=1)
    return moment, start, end


# ---------------------------------------------------------------- days
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
          r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_ORD = r"(?:st|nd|rd|th)"
DAY_RE = re.compile(
    r"\b(?:(?:on|for)\s+)?(?:"
    r"(?:the\s+)?(day after tomorrow)"                                                        # 1
    r"|(today|tonight|this (?:morning|afternoon|evening))"                                    # 2
    r"|(tomorrow|tomorow|tmrw)(?:\s+(?:morning|afternoon|evening|night))?"                    # 3
    r"|(?:(this|next|coming)\s+)?(mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"  # 4, 5
    rf"|(?:the\s+)?(\d{{1,2}}){_ORD}?\s+(?:of\s+)?{_MONTH}"                                    # 6, 7: 25th december
    rf"|{_MONTH}\s+(?:the\s+)?(\d{{1,2}}){_ORD}?"                                              # 8, 9: december 25
    rf"|the\s+(\d{{1,2}}){_ORD}"                                                               # 10: the 25th
    r")\b",
    re.IGNORECASE,
)


def _month_number(name):
    return next(i for i, full in enumerate(MONTHS, 1) if full.startswith(name.lower()[:3]))


def _next_date(today, month, day):
    """The next date with this month/day, today or later (None if it doesn't exist)."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def find_day(text, now=None):
    """Return (date, start, end) for 'today', 'tomorrow', 'on Monday', 'next friday', '25th December'...

    A weekday means its next occurrence after today ("this monday" said on a Monday means today).
    """
    today = (now or datetime.now()).date()
    for match in DAY_RE.finditer(text or ""):
        g = match.groups()
        if g[0]:
            found = today + timedelta(days=2)
        elif g[1]:
            found = today
        elif g[2]:
            found = today + timedelta(days=1)
        elif g[4]:
            target = next(i for i, name in enumerate(WEEKDAYS) if name.startswith(g[4].lower()[:3]))
            ahead = (target - today.weekday()) % 7
            if ahead == 0 and (g[3] or "").lower() != "this":
                ahead = 7
            found = today + timedelta(days=ahead)
        elif g[5]:
            found = _next_date(today, _month_number(g[6]), int(g[5]))
        elif g[7]:
            found = _next_date(today, _month_number(g[7]), int(g[8]))
        else:  # "the 25th": this month if it's still ahead, otherwise the next month that has that day
            found = None
            for offset in range(3):
                month, year = (today.month - 1 + offset) % 12 + 1, today.year + (today.month - 1 + offset) // 12
                try:
                    candidate = date(year, month, int(g[9]))
                except ValueError:
                    continue
                if candidate >= today:
                    found = candidate
                    break
        if found:
            return found, match.start(), match.end()
    return None


def find_datetime(text, now=None):
    """Combine a day and a clock time: 'tomorrow at 5 pm', 'on Monday at 11', 'at 6', 'on Friday'.

    Returns (datetime, has_time, spans) or None; spans are the (start, end) pieces used, so callers can
    cut them out of the text. "at 11" on another day is read as a working hour: 1-7 is PM, 8-11 AM, 12 noon.
    """
    now = now or datetime.now()
    day = find_day(text, now)
    parts = clock_parts(text)
    if not day and not parts:
        return None
    spans = [found[-2:] if found is day else found[3:] for found in (day, parts) if found]
    if not parts:
        return datetime.combine(day[0], time()), False, spans
    if not day:
        return find_clock(text, now)[0], True, spans
    hour, minute, exact, _, _ = parts
    if exact:
        chosen = hour
    elif day[0] == now.date():
        options = _candidates(hour, exact)
        chosen = next((h for h in options if datetime.combine(day[0], time(h, minute)) > now), max(options))
    else:
        chosen = hour + 12 if 1 <= hour <= 7 else hour
    return datetime.combine(day[0], time(chosen, minute)), True, spans


def remove_spans(text, spans):
    """Cut the (start, end) spans out of text."""
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return re.sub(r"\s+", " ", text).strip()


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
        text += " " + say_day(moment, now)
    return text


def say_day(moment, now=None):
    """'today', 'tomorrow', 'on Monday', or 'on Monday 1 December' when it's more than 6 days away."""
    now = now or datetime.now()
    day = moment.date() if isinstance(moment, datetime) else moment
    ahead = (day - now.date()).days
    if ahead == 0:
        return "today"
    if ahead == 1:
        return "tomorrow"
    if 1 < ahead <= 6:
        return f"on {WEEKDAYS[day.weekday()].title()}"
    return f"on {WEEKDAYS[day.weekday()].title()} {day.day} {MONTHS[day.month - 1].title()}"
