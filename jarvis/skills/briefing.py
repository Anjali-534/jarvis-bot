"""Morning briefing and good-night wrap-up.

"Good morning Jarvis" / "morning briefing" / "brief me": greeting + date, today's weather, AQI and mask advice,
the first calendar events, unread email, today's reminders and one headline. All sections are fetched in
parallel (6 s each); anything that fails or isn't connected is silently left out. The spoken version is kept
under about 40 seconds; the full version is printed in the terminal.

"Good night Jarvis": tomorrow's first event and weather, tomorrow's reminders, an optional wake-up reminder,
and the volume turned down to 20%.

.env: BRIEFING_SECTIONS (order), BRIEFING_TIME (automatic once a day when idle), BRIEFING_MUSIC.
"""
import concurrent.futures
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import config
from jarvis import notify
from jarvis.router import on_startup, skill

log = logging.getLogger(__name__)

SECTION_TIMEOUT = 6.0
WORDS_PER_SECOND = 2.5
MAX_SECONDS = 40
MAX_WORDS = int(MAX_SECONDS * WORDS_PER_SECOND)  # 100 words
DEFAULT_SECTIONS = ["weather", "aqi", "calendar", "email", "reminders", "news"]
IDLE_SECONDS = 120            # automatic briefing only after 2 minutes without talking to Jarvis
AUTO_WINDOW_HOURS = 3         # ...and only within 3 hours of BRIEFING_TIME
DONE_KEY = "briefing-done"


@dataclass
class Section:
    spoken: str        # short, for speech
    full: str = ""     # longer, for the terminal (defaults to spoken)

    def __post_init__(self):
        self.full = self.full or self.spoken


def word_count(text):
    return len(re.findall(r"[\w'-]+", text or ""))


def spoken_seconds(text):
    return word_count(text) / WORDS_PER_SECOND


def today():
    return datetime.now()


# ---------------------------------------------------------------- sections (each returns Section or None)
def _place(ctx):
    from jarvis import geo

    return ctx.state.get("location") or geo.default_place()


def weather_section(ctx, day_index=0):
    from jarvis.skills import weather

    place = _place(ctx)
    day = weather._day(weather.forecast(place), day_index)
    when = "Today" if day_index == 0 else "Tomorrow"
    sky = weather.describe(day["code"])
    spoken = (f"{when} in {place.name}: {day['min']:.0f} to {day['max']:.0f} degrees, {sky}, "
              f"{day['rain']:.0f} percent chance of rain.")
    return Section(spoken)


def mask_advice(zone):
    return {"Red": "Wear a mask outside.", "Yellow": "Sensitive people should wear a mask."}.get(
        zone, "No mask needed.")


def aqi_section(ctx):
    from jarvis.skills import aqi

    reading = aqi.fetch_air([_place(ctx)])[0]
    spoken = (f"Air quality in {reading.place.name} is {reading.aqi}, {reading.category}, a {reading.zone.lower()} "
              f"zone. {mask_advice(reading.zone)}")
    return Section(spoken, f"{spoken} ({reading.advice})")


def _calendar_events(start, end):
    from jarvis import google_auth
    from jarvis.skills import calendar

    if not google_auth.is_connected():
        return None
    return calendar.fetch_events(calendar.calendar_api(), start, end)


def calendar_section(ctx):
    from jarvis.skills import calendar

    current = calendar.now()
    events = _calendar_events(current - timedelta(hours=1),
                              datetime.combine(current.date() + timedelta(days=1), datetime.min.time()))
    if events is None:
        return None
    if not events:
        return Section("Your calendar is clear today.")
    described = [calendar.describe(e, current) for e in events]
    count = f"{len(events)} event{'s' if len(events) != 1 else ''} today"
    spoken = f"You have {count}: " + "; ".join(described[:3]) + ("; and more." if len(events) > 3 else ".")
    return Section(spoken, f"You have {count}: " + "; ".join(described) + ".")


def email_section(ctx):
    from jarvis import google_auth
    from jarvis.skills import gmail

    if not google_auth.is_connected():
        return None
    service = gmail.gmail()
    ids, more = gmail.list_ids(service, gmail.PRIMARY_UNREAD, gmail.MAX_COUNT)
    if not ids:
        return Section("No unread email.")
    headers = [gmail.headers_of(gmail.metadata(service, i)) for i in ids[:2]]
    senders = [gmail.sender_name(h.get("from")) for h in headers]
    count = f"more than {gmail.MAX_COUNT}" if more else str(len(ids))
    plural = "" if count == "1" else "s"
    spoken = f"{count} unread email{plural}, from {' and '.join(senders)}."
    full = f"{count} unread email{plural}: " + "; ".join(
        f"{gmail.sender_name(h.get('from'))}: {h.get('subject') or 'no subject'}" for h in headers) + "."
    return Section(spoken, full)


def _reminders_on(ctx, day):
    from jarvis.timeparse import say_clock

    active = sorted(ctx.state.get("reminders", []), key=lambda e: e["when"])
    return [(f"a {e['text']} timer" if e["kind"] == "timer" else e["text"]) + f" at {say_clock(e['when'], e['when'])}"
            for e in active if e["when"].date() == day]


def reminders_section(ctx, day_offset=0):
    items = _reminders_on(ctx, today().date() + timedelta(days=day_offset))
    if not items:
        return None
    label = "Reminders today" if day_offset == 0 else "Reminders for tomorrow"
    return Section(f"{label}: " + "; ".join(items[:3]) + ".", f"{label}: " + "; ".join(items) + ".")


def news_section(ctx):
    from jarvis.skills import web

    titles = web.headlines(limit=1)
    if not titles:
        return None
    return Section(f"Top headline: {titles[0]}.")


SECTIONS = {
    "weather": weather_section,
    "aqi": aqi_section,
    "calendar": calendar_section,
    "email": email_section,
    "reminders": reminders_section,
    "news": news_section,
}


def configured_sections():
    names = config.BRIEFING_SECTIONS or DEFAULT_SECTIONS
    unknown = [n for n in names if n not in SECTIONS]
    if unknown:
        log.warning("[briefing] unknown BRIEFING_SECTIONS ignored: %s", ", ".join(unknown))
    return [n for n in names if n in SECTIONS]


def gather(ctx, jobs, timeout=SECTION_TIMEOUT):
    """Run {name: fn} in parallel. Returns {name: Section} for those that finished in time without failing."""
    results = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(jobs)), thread_name_prefix="briefing")
    try:
        futures = {name: pool.submit(fn, ctx) for name, fn in jobs.items()}
        deadline = time.monotonic() + timeout
        for name, future in futures.items():
            try:
                section = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except concurrent.futures.TimeoutError:
                log.info("[briefing] %s took longer than %.0f s, skipped", name, timeout)
                continue
            except Exception as exc:  # not connected, offline, no data...
                log.debug("[briefing] %s skipped: %s", name, exc)
                continue
            if section and section.spoken:
                results[name] = section
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def fit_budget(parts, max_words=MAX_WORDS):
    """Drop sections from the end (never the greeting) until the spoken text fits ~40 seconds."""
    parts = list(parts)
    while len(parts) > 1 and word_count(" ".join(parts)) > max_words:
        parts.pop()
    return parts


# ---------------------------------------------------------------- Hinglish
def in_hindi_mode(ctx):
    language = getattr(ctx.voice, "language", None) or ctx.state.get("speech_language") or config.SPEECH_LANGUAGE
    return str(language).lower().startswith("hi")


def to_hinglish(text):
    """Rewrite the briefing in Roman-script Hinglish with the AI; the English text if that isn't possible."""
    if not config.AI_API_KEY:
        return text
    try:
        from jarvis.skills import ai

        status, reply = ai.complete(
            "Rewrite this spoken assistant briefing in natural Hinglish (Hindi-English mix) written ONLY in Roman "
            "letters, never Devanagari. Keep every fact, number and name. Keep it about as short. Plain text only.",
            [], text, max_tokens=300)
        reply = ai.strip_markdown(reply) if status < 400 else ""
        if reply and not re.search(r"[ऀ-ॿ]", reply):
            return reply
    except Exception as exc:
        log.debug("[briefing] Hinglish rewrite failed: %s", exc)
    return text


# ---------------------------------------------------------------- the briefing
def greeting(now=None):
    from jarvis.skills.general import time_of_day

    now = now or today()
    return f"Good {time_of_day(now)}, {config.USER_NAME}. It's {now:%A}, {now.day} {now:%B}."


def build(ctx, names=None, now=None):
    """Return (spoken text, full text) for the morning briefing."""
    names = names or configured_sections()
    found = gather(ctx, {name: SECTIONS[name] for name in names})
    ordered = [found[name] for name in names if name in found]
    head = greeting(now)
    spoken = " ".join(fit_budget([head] + [s.spoken for s in ordered]))
    full = "\n".join([head] + [f"  - {s.full}" for s in ordered])
    return spoken, full


def run_briefing(ctx):
    spoken, full = build(ctx)
    log.info("\n=== Morning briefing ===\n%s\n", full)
    if in_hindi_mode(ctx):
        spoken = to_hinglish(spoken)
    ctx.say(spoken)
    if config.BRIEFING_MUSIC:
        from jarvis.router import handle

        handle(ctx, f"play {config.BRIEFING_MUSIC} on spotify")
    return ""


@skill("morning briefing",
       r"^good\s*morning(?:\s+jarvis)?$", r"\bmorning briefing\b", r"^brief me\b", r"\b(?:daily|my) briefing\b",
       category="briefing", help=["good morning Jarvis", "morning briefing", "brief me"])
def morning_briefing(ctx, command, match):
    return run_briefing(ctx)


# ---------------------------------------------------------------- good night
def tomorrow_event_section(ctx):
    from jarvis.skills import calendar

    current = calendar.now()
    start = datetime.combine(current.date() + timedelta(days=1), datetime.min.time())
    events = _calendar_events(start, start + timedelta(days=1))
    if events is None:
        return None
    timed = [e for e in events if not calendar.event_start(e)[1]] or events
    if not timed:
        return Section("Nothing on your calendar tomorrow.")
    first = timed[0]
    begins, all_day = calendar.event_start(first)
    when = "all day" if all_day else f"at {calendar.say_clock(begins, begins)}"
    extra = f", one of {len(events)} events" if len(events) > 1 else ""
    return Section(f"Tomorrow starts with {calendar.title_of(first)} {when}{extra}.")


NIGHT_SECTIONS = {
    "calendar": tomorrow_event_section,
    "weather": lambda ctx: weather_section(ctx, day_index=1),
    "reminders": lambda ctx: reminders_section(ctx, day_offset=1),
}


def set_volume_percent(percent):
    """Windows volume to about `percent`: all the way down, then up in 2% steps."""
    from jarvis.skills import system

    if not system.IS_WINDOWS:
        return False
    system.press("volumedown", 50)
    system.press("volumeup", max(0, round(percent / 2)))
    return True


def offer_alarm(ctx):
    """Ask for a wake-up time and set a reminder. Returns the spoken confirmation, or ''."""
    from jarvis.skills import notes
    from jarvis.timeparse import find_clock, say_clock

    answer = ctx.ask("Shall I set a wake-up reminder? Say a time, like 7 am, or no.")
    if not answer:
        return ""
    found = find_clock(answer if re.search(r"\bat\b", answer) else f"at {answer}")
    if not found:
        from jarvis.router import is_yes

        if not is_yes(answer):
            return ""
        answer = ctx.ask("What time?")
        found = find_clock(answer if re.search(r"\bat\b", answer or "") else f"at {answer}") if answer else None
        if not found:
            return "Sorry, I didn't catch the time, so no wake-up reminder."
    moment = found[0]
    notes.schedule(ctx, max(1, (moment - datetime.now()).total_seconds()), "Good morning! Time to wake up.")
    return f"Wake-up reminder set for {say_clock(moment)}."


@skill("good night", r"^good\s*night(?:\s+jarvis)?$", r"\bevening (?:wrap[- ]?up|briefing)\b", r"\bnight briefing\b",
       category="briefing", help=["good night Jarvis"])
def good_night(ctx, command, match):
    found = gather(ctx, NIGHT_SECTIONS)
    parts = [f"Good night, {config.USER_NAME}."] + [found[n].spoken for n in NIGHT_SECTIONS if n in found]
    spoken = " ".join(fit_budget(parts))
    log.info("\n=== Good night ===\n%s\n", "\n".join(parts))
    if in_hindi_mode(ctx):
        spoken = to_hinglish(spoken)
    ctx.say(spoken)
    confirmation = offer_alarm(ctx)
    turned_down = set_volume_percent(20)
    closing = " ".join(t for t in (confirmation, "I've turned the volume down to 20 percent." if turned_down else "",
                                   "Sleep well.") if t)
    return closing


# ---------------------------------------------------------------- automatic morning briefing
def parse_time(text):
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", text or "")
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return int(match.group(1)), int(match.group(2))


def done_on():
    """The date the automatic briefing last ran (from data/cache.json), or None."""
    from jarvis import cache

    value = cache.get(DONE_KEY, 7 * 24 * 3600)
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def mark_done(day):
    from jarvis import cache

    cache.put(DONE_KEY, day.isoformat())


def due(now, idle_seconds, last_done):
    """Should the automatic briefing run now?"""
    at = parse_time(config.BRIEFING_TIME)
    if at is None or last_done == now.date():
        return False
    start = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    return start <= now <= start + timedelta(hours=AUTO_WINDOW_HOURS) and idle_seconds >= IDLE_SECONDS


def idle_seconds(ctx):
    assistant = ctx.state.get("assistant")
    last = getattr(assistant, "_last_reply", None)
    return time.monotonic() - last if last is not None else float("inf")


def tick(ctx, now=None):
    """One check of the automatic briefing. Returns True if it ran."""
    now = now or today()
    if ctx.sleeping or not ctx.running or not due(now, idle_seconds(ctx), done_on()):
        return False
    mark_done(now.date())  # first, so it can never run twice in a day
    notify.toast("Morning briefing", f"Good morning, {config.USER_NAME}! Here's your day.")
    run_briefing(ctx)
    return True


_stop = threading.Event()


def _loop(ctx):
    while not _stop.wait(30) and ctx.running:
        try:
            tick(ctx)
        except Exception:
            log.exception("[briefing] automatic briefing failed")


@on_startup
def _start_scheduler(ctx):
    if parse_time(config.BRIEFING_TIME) is None:
        if config.BRIEFING_TIME:
            log.warning("[briefing] BRIEFING_TIME=%s isn't HH:MM, automatic briefing is off", config.BRIEFING_TIME)
        return
    _stop.clear()
    threading.Thread(target=_loop, args=(ctx,), name="briefing-scheduler", daemon=True).start()
    log.info("[briefing] automatic morning briefing at %s", config.BRIEFING_TIME)


def stop_scheduler():
    _stop.set()
