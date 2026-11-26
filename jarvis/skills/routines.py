"""Alexa-style routines: one phrase runs a list of ordinary Jarvis commands ("start work mode").

Routines live in data/routines.json (copied from routines.example.json the first time):

    {"routines": {"movie mode": {"steps": ["dark mode on", "brightness 40", {"wait": 2}, "open netflix"]}},
     "schedules": [{"routine": "work mode", "days": ["mon", "tue", "wed", "thu", "fri"], "time": "09:30"}]}

A step is a command (it runs through the normal skills, so every confirmation still applies), {"wait": seconds}
or {"say": "text"}. Schedules run while Jarvis is running; a scheduled run never answers "yes" for you, so a
step that needs a yes (closing apps, shutting down) is skipped and you're told.
"""
import copy
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta

import config
from jarvis import actions, hinglish as hi
from jarvis.router import Context, join_and, on_startup, skill, speak_list

log = logging.getLogger(__name__)

MAX_STEPS = 20
MAX_DEPTH = 2              # a routine may start another routine, but not endlessly
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_WORDS = {"monday": ["mon"], "tuesday": ["tue"], "wednesday": ["wed"], "thursday": ["thu"], "friday": ["fri"],
             "saturday": ["sat"], "sunday": ["sun"], "weekday": DAY_NAMES[:5], "weekdays": DAY_NAMES[:5],
             "weekend": DAY_NAMES[5:], "weekends": DAY_NAMES[5:], "day": DAY_NAMES, "daily": DAY_NAMES,
             "morning": DAY_NAMES, "evening": DAY_NAMES, "night": DAY_NAMES}
SPOKEN_DAYS = {tuple(DAY_NAMES): "every day", tuple(DAY_NAMES[:5]): "every weekday", tuple(DAY_NAMES[5:]): "on weekends"}
DONE_WORDS = re.compile(r"^(?:done|that's it|that is it|thats it|finish(?:ed)?|stop|no more|nothing|bas|ho gaya|"
                        r"that's all|that is all|save it|end)\b")

_lock = threading.Lock()       # one routine at a time
_file_lock = threading.Lock()


# ---------------------------------------------------------------- the file
def _default():
    try:
        return json.loads(config.ROUTINES_EXAMPLE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"routines": {}, "schedules": []}


def load():
    with _file_lock:
        try:
            data = json.loads(config.ROUTINES_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = _default()
            _write(data)
        except (OSError, ValueError):
            log.warning("data/routines.json is broken; using the built-in routines for now")
            data = _default()
    data.setdefault("routines", {})
    data.setdefault("schedules", [])
    data["routines"] = {k.lower(): v for k, v in data["routines"].items() if not k.startswith("_")}
    return data


def _write(data):
    config.ROUTINES_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.ROUTINES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save(data):
    with _file_lock:
        _write(data)


def steps_of(routine):
    return routine.get("steps", []) if isinstance(routine, dict) else list(routine)


# ---------------------------------------------------------------- finding a routine by name
def _bare(name):
    name = re.sub(r"^(?:the|my)\s+", "", (name or "").lower().strip(" .?!"))
    name = re.sub(r"\s+(?:routine|please|now|on|kar\w*|shuru\w*|chalu)$", "", name)
    return name.strip()


def find(name, data=None):
    """The routine's key for 'work', 'work mode', 'the work mode routine' (or None)."""
    from jarvis.skills import app_index

    data = data or load()
    wanted = _bare(name)
    if not wanted:
        return None
    for key, routine in data["routines"].items():
        names = {key, _bare(key), *[a.lower() for a in (routine.get("aliases", []) if isinstance(routine, dict) else [])]}
        names |= {re.sub(r"\s+(?:mode|time|routine)$", "", n) for n in list(names)}
        if wanted in names or re.sub(r"\s+(?:mode|time|routine)$", "", wanted) in names:
            return key
    scored = sorted(((app_index.score(wanted, key), key) for key in data["routines"]), reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 0.88 else None


# ---------------------------------------------------------------- running
class _Unattended:
    """A voice for scheduled runs: speaks normally, but nobody is there to say yes."""

    text_mode = True

    def __init__(self, voice):
        self.voice, self.asked, self._last = voice, [], ""

    def speak(self, text, wait=True):
        self._last = text
        self.voice.speak(text, wait=wait)

    def listen(self, *args, **kwargs):
        self.asked.append(self._last)  # the question nobody could answer: counts as "no"
        return ""

    def __getattr__(self, name):
        return getattr(self.voice, name)


def sleep(seconds):
    time.sleep(seconds)


def run_step(ctx, step):
    """Run one step. Returns True if a skill handled it."""
    from jarvis import router

    if isinstance(step, dict):
        if "wait" in step:
            sleep(max(0, min(600, float(step["wait"]))))
            return True
        if "say" in step:
            ctx.say(str(step["say"]))
            return True
        return False
    command = re.sub(r"\s+", " ", str(step).strip().lower())
    if not command:
        return False
    return bool(router._try_skills(ctx, command))  # never the AI: a routine only runs real commands


def run(ctx, name, unattended=False):
    """Run a routine's steps in order. Returns what to say at the end."""
    data = load()
    key = find(name, data)
    if key is None:
        return f"I don't have a routine called {name}."
    depth = ctx.state.get("routine_depth", 0)
    if depth >= MAX_DEPTH:
        return f"{key} starts another routine too many times, so I stopped."
    steps = steps_of(data["routines"][key])
    runner = ctx
    if unattended:
        runner = Context(_Unattended(ctx.voice), state=ctx.state)
    top = depth == 0  # a nested routine runs inside the outer one's lock
    if top and not _lock.acquire(timeout=1):
        return "Another routine is still running."
    failed = []
    try:
        ctx.state["routine_depth"] = depth + 1
        actions.record(f"Started the routine {key}" + (" (scheduled)" if unattended else ""))
        if top:
            runner.say(f"Starting {key}.")
        for step in steps[:MAX_STEPS]:
            label = step if isinstance(step, str) else json.dumps(step)
            try:
                ok = run_step(runner, step)
            except Exception:
                log.exception("[routine] step %r failed", step)
                ok = False
            actions.note(f"  routine {key}: {label} -> {'done' if ok else 'not understood'}")
            if not ok:
                failed.append(label)
    finally:
        ctx.state["routine_depth"] = depth
        if top:
            _lock.release()
    reply = f"{key[:1].upper()}{key[1:]} done." if top else ""
    if failed:
        reply += f" I couldn't do: {join_and(failed)}."
    if unattended and runner.voice.asked:
        reply += " I skipped the steps that needed your yes."
    return reply.strip()


# ---------------------------------------------------------------- skills
_START = r"(?:start|run|begin|activate|do|turn on|switch to|switch on|enable|go into|enter|trigger|launch)"


@skill("run routine", rf"^(?:please\s+)?{_START}\s+(?:the\s+|my\s+)?(.+?)(?:\s+routine)?$",
       r"^(?:please\s+)?(.+?\s+(?:mode|time|routine))(?:\s+(?:on|please|now|shuru\w*|chalu|start))?$",
       rf"{hi.LEAD}(.+?)\s+(?:shuru|chalu|start|on){hi.KAR}$",
       category="routines", help=["start work mode", "movie mode", "break time"],
       tool="routine", params={"name": "routine name, e.g. work mode"}, command="start {name} routine")
def run_routine(ctx, command, match):
    name = match.group(1)
    if find(name) is None:
        return None  # not a routine: "start chrome" is the open skill
    return run(ctx, name)


def parse_step(answer):
    """A spoken step: 'wait 5 seconds' -> {"wait": 5}, 'say hello' -> {"say": "hello"}, else the command."""
    from jarvis.timeparse import parse_duration

    answer = answer.strip().strip(".")
    if re.match(r"^(?:wait|pause for|ruko)\b", answer):
        return {"wait": parse_duration(answer) or 5}
    said = re.match(r"^(?:say|tell me|announce)\s+(.+)$", answer)
    if said:
        return {"say": said.group(1)}
    return answer


def understood(step):
    """Would a skill (not the AI) run this step?"""
    from jarvis import router

    if isinstance(step, dict):
        return True
    return any(p.search(step) for sk in router.SKILLS for p in sk.patterns if sk.name != "create routine")


@skill("create routine", r"^(?:please\s+)?(?:create|make|add|set up|setup|build|new)\s+(?:a\s+)?(?:new\s+)?routine"
                         r"(?:\s+(?:called|named|for))?\s+(.+)$",
       r"^(?:please\s+)?(?:create|make|add)\s+(?:a\s+|the\s+)?(.+?)\s+routine$",
       rf"{hi.LEAD}(?:ek\s+)?(?:naya\s+)?routine\s+(?:bana|banao|bana do|bana de)\s+(.+)$",
       category="routines", help=["create a routine called study time"])
def create_routine(ctx, command, match):
    name = _bare(match.group(1))
    if not name:
        return "What should the routine be called?"
    data = load()
    if name in data["routines"] and not ctx.confirm(f"You already have {name}. Replace it?"):
        return f"Okay, I kept your {name} routine."
    steps = []
    prompt = "What's the first step? Say any command, like open Chrome or volume 30."
    while len(steps) < MAX_STEPS:
        answer = ctx.ask(prompt)
        if not answer:
            if not steps:
                return "Okay, I didn't make it."
            break
        if DONE_WORDS.search(answer):
            break
        step = parse_step(answer)
        if not understood(step):
            prompt = "I don't know that command. Say it another way, or say done."
            continue
        steps.append(step)
        prompt = "Next step? Say done when you're finished."
    if not steps:
        return "Okay, I didn't make it."
    spoken = [s if isinstance(s, str) else (f"wait {s['wait']:g} seconds" if "wait" in s else f"say {s['say']}")
              for s in steps]
    if not ctx.confirm(f"{name}: {join_and(spoken)}. Save it?"):
        return "Okay, I didn't save it."
    data["routines"][name] = {"steps": steps}
    save(data)
    actions.record(f"Created the routine {name} ({len(steps)} steps)")
    return f"Saved. Say start {name} to run it."


@skill("list routines", r"\b(?:list|show|tell me|what are|which are|read)\b.*\broutines\b", r"^(?:my\s+)?routines$",
       r"\bwhat routines\b", category="routines", help=["list my routines"])
def list_routines(ctx, command, match):
    data = load()
    names = list(data["routines"])
    if not names:
        return "You don't have any routines yet. Say create a routine called, and a name."
    reply = speak_list(ctx, f"You have {len(names)} routines:", names)
    plans = [f"{s['routine']} {spoken_days(s.get('days'))} at {spoken_time(s.get('time'))}" for s in data["schedules"]]
    if plans and reply:
        reply += " Scheduled: " + join_and(plans) + "."
    return reply


@skill("delete routine", r"^(?:please\s+)?(?:delete|remove|forget|get rid of)\s+(?:the\s+|my\s+)?(?:routine\s+(?:called\s+)?(.+)|(.+?)\s+routine)$",
       category="routines", help=["delete routine study time"])
def delete_routine(ctx, command, match):
    name = match.group(1) or match.group(2)
    data = load()
    key = find(name, data)
    if key is None:
        return f"I don't have a routine called {_bare(name)}."
    if not ctx.confirm(f"Delete the {key} routine?"):
        return f"Okay, I kept {key}."
    removed = data["routines"].pop(key)
    dropped = [s for s in data["schedules"] if s.get("routine") == key]
    data["schedules"] = [s for s in data["schedules"] if s.get("routine") != key]
    save(data)

    def undo():
        restored = load()
        restored["routines"][key] = removed
        restored["schedules"] += dropped
        save(restored)

    actions.record(f"Deleted the routine {key}", undo=undo, undo_text=f"bring back the {key} routine")
    return f"Deleted {key}."


# ---------------------------------------------------------------- schedules
def spoken_days(days):
    days = tuple(d for d in DAY_NAMES if d in (days or DAY_NAMES))
    if days in SPOKEN_DAYS:
        return SPOKEN_DAYS[days]
    full = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
            "sat": "Saturday", "sun": "Sunday"}
    return "every " + join_and([full[d] for d in days])


def spoken_time(hhmm):
    try:
        hour, minute = map(int, (hhmm or "").split(":"))
    except ValueError:
        return hhmm or "?"
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}" + (f":{minute:02d}" if minute else "") + f" {suffix}"


def parse_days(text):
    days = []
    for word in re.findall(r"[a-z]+", text.lower()):
        for day in DAY_WORDS.get(word, DAY_WORDS.get(word.rstrip("s"), [])):
            if day not in days:
                days.append(day)
    return [d for d in DAY_NAMES if d in days]


def parse_time(text):
    """'9:30' -> '09:30'; a bare 1-7 is PM ('at 6' -> 18:00), like the calendar."""
    from jarvis.timeparse import clock_parts

    parts = clock_parts(text if re.search(r"\bat\b", text) else f"at {text}")
    if not parts:
        return None
    hour, minute, exact = parts[0], parts[1], parts[2]
    if not exact and 1 <= hour <= 7:
        hour += 12
    if re.search(r"\b(?:evening|night)\b", text) and hour < 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


_DAYS = r"(day|daily|weekdays?|weekends?|morning|evening|night|(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?(?:\s*(?:,|and)\s*)?)+)"


_DAYS_G = _DAYS.replace("(", "(?P<days>", 1)


@skill("schedule routine",
       rf"^(?:please\s+)?(?:every|each|on)\s+{_DAYS_G}\s+(?:at\s+)?(?P<time>.+?)\s+{_START}\s+(?:the\s+|my\s+)?(?P<name>.+?)"
       r"(?:\s+routine)?$",
       rf"^(?:please\s+)?{_START}\s+(?:the\s+|my\s+)?(?P<name>.+?)(?:\s+routine)?\s+(?:every|each|on)\s+{_DAYS_G}\s+"
       r"(?:at\s+)?(?P<time>.+)$",
       rf"^(?:please\s+)?(?P<days>daily|everyday)\s+(?:at\s+)?(?P<time>.+?)\s+{_START}\s+(?:the\s+|my\s+)?(?P<name>.+?)"
       r"(?:\s+routine)?$",
       category="routines", help=["every weekday at 9:30 start work mode"])
def schedule_routine(ctx, command, match):
    name, days_text, time_text = match.group("name"), match.group("days") or "day", match.group("time")
    data = load()
    key = find(name, data)
    if key is None:
        return f"I don't have a routine called {_bare(name)}. Say create a routine called {_bare(name)} first."
    days = parse_days(days_text) or DAY_NAMES
    at = parse_time(time_text)
    if at is None:
        return "What time? Say something like every weekday at 9:30 start work mode."
    plan = f"{key} {spoken_days(days)} at {spoken_time(at)}"
    if not ctx.confirm(f"Start {plan}?"):
        return "Okay, no schedule."
    data["schedules"] = [s for s in data["schedules"] if not (s.get("routine") == key and s.get("time") == at)]
    data["schedules"].append({"routine": key, "days": days, "time": at})
    save(data)
    actions.record(f"Scheduled {plan}")
    return f"Done. I'll start {plan}, while I'm running."


@skill("cancel schedule", r"\b(?:cancel|stop|delete|remove|clear)\b.*\b(?:routine\s+)?schedules?\b(?!.*\bmeeting)",
       r"\b(?:don't|do not|stop)\s+(?:start|run)(?:ning)?\s+(?:the\s+|my\s+)?(.+?)\s+(?:automatically|every day|on schedule)\b",
       category="routines", help=["cancel the work mode schedule"])
def cancel_schedule(ctx, command, match):
    data = load()
    if not data["schedules"]:
        return "There are no scheduled routines."
    named = [k for k in data["routines"] if re.search(rf"\b{re.escape(_bare(k))}\b", command)]
    targets = [s for s in data["schedules"] if not named or s.get("routine") in named]
    if not targets:
        return "That routine isn't scheduled."
    plans = [f"{s['routine']} {spoken_days(s.get('days'))} at {spoken_time(s.get('time'))}" for s in targets]
    if not ctx.confirm(f"Stop {join_and(plans)}?"):
        return "Okay, I kept the schedule."
    before = copy.deepcopy(data["schedules"])
    data["schedules"] = [s for s in data["schedules"] if s not in targets]
    save(data)

    def undo():
        restored = load()
        restored["schedules"] = before
        save(restored)

    actions.record(f"Cancelled schedules: {join_and(plans)}", undo=undo, undo_text="put the schedules back")
    return "Done, no more " + join_and(plans) + "."


def due(schedule, now, ran):
    """Should this schedule run at `now`? `ran` holds (routine, time, date) runs done already."""
    if DAY_NAMES[now.weekday()] not in (schedule.get("days") or DAY_NAMES):
        return False
    try:
        hour, minute = map(int, schedule.get("time", "").split(":"))
    except ValueError:
        return False
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    key = (schedule.get("routine"), schedule.get("time"), now.date())
    return start <= now < start + WINDOW and key not in ran


WINDOW = timedelta(minutes=2)    # a schedule fires within 2 minutes of its time, once a day
_ran = set()
_stop = threading.Event()


def tick(ctx, now=None):
    """Run every schedule that's due. Returns the routines started."""
    now = now or datetime.now()
    started = []
    for schedule in load()["schedules"]:
        if due(schedule, now, _ran):
            _ran.add((schedule.get("routine"), schedule.get("time"), now.date()))
            log.info("[routine] scheduled %s", schedule.get("routine"))
            ctx.say(run(ctx, schedule["routine"], unattended=True))
            started.append(schedule["routine"])
    return started


@on_startup
def _start_scheduler(ctx):
    _stop.clear()

    def loop():
        while not _stop.wait(20) and ctx.running:
            try:
                tick(ctx)
            except Exception:
                log.exception("[routine] scheduler failed")

    threading.Thread(target=loop, name="routine-scheduler", daemon=True).start()


def stop_scheduler():
    _stop.set()
