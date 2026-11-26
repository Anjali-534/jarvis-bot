"""Skill registry, conversation Context and the command dispatcher.

A skill is a function ``handler(ctx, command, match)`` registered with ``@skill``:

* return a non-empty string  -> it is spoken
* return "" or True          -> handled, nothing more to say
* return None                -> "not mine", the router tries the next skill
"""
import logging
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

from jarvis import persona

log = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    patterns: list
    handler: Callable
    examples: list = field(default_factory=list)  # example phrases for help and COMMANDS.md
    category: str | None = None                   # a key of jarvis.skills.general.CATEGORIES
    # AI tool calling (jarvis.intent): set params to expose this skill as a tool the AI can pick
    params: dict | None = None    # {"place?": "city or area", "day?": ["today", "tomorrow"]}; "?" = optional
    command: object = None        # template "weather[ in {place}][ {day}]" or fn(args) -> canonical command
    tool: str | None = None       # tool name (default: the skill name)
    also: tuple = ()              # other skills this tool's commands may land on (e.g. volume down, mute)
    risky: bool = False           # always confirm before running when the AI picked it
    confirms: bool = False        # the skill already asks for confirmation itself
    module: str = ""              # the module that registered it (jarvis.skills orders SKILLS by module)


SKILLS: list[Skill] = []
FALLBACK: Skill | None = None


class SpokenError(Exception):
    """Raise from a skill to stop it and speak a friendly message."""


def skill(name, *patterns, help=(), category=None, params=None, command=None, tool=None, also=(), risky=False,
          confirms=False):
    """Register a skill triggered by any of the regex patterns (case-insensitive).

    help: an example phrase, or a list of them, shown by "help" / "help with <category>".
    category: which help category the skill belongs to.
    params / command: expose the skill to the AI as a tool (see jarvis.intent). params maps argument names to a
        description, a list of allowed values, or ("integer", description); a trailing "?" marks it optional.
        command turns the AI's arguments back into a phrase this skill understands.
    risky: confirm before running when the AI chose it (confirms=True if the skill already asks).
    """
    examples = [help] if isinstance(help, str) and help else list(help)
    module = sys._getframe(1).f_globals.get("__name__", "")

    def decorator(fn):
        SKILLS.append(Skill(name, [re.compile(p, re.IGNORECASE) for p in patterns], fn, examples, category,
                            params, command or (name if params == {} else None), tool, tuple(also), risky, confirms,
                            module))
        return fn

    return decorator


def skills_in(category):
    return [s for s in SKILLS if s.category == category]


def fallback(fn):
    """Register the handler for anything no skill matched (the AI)."""
    global FALLBACK
    FALLBACK = Skill(fn.__name__, [], fn)
    return fn


STARTUP: list[Callable] = []


def on_startup(fn):
    """Register fn(ctx) to run once when the listening loop starts (e.g. background pollers)."""
    STARTUP.append(fn)
    return fn


def run_startup(ctx):
    for fn in STARTUP:
        try:
            fn(ctx)
        except Exception:  # a broken integration must never stop Jarvis from starting
            log.exception("Startup hook %s failed", getattr(fn, "__name__", fn))


YES_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "haan", "han", "ha", "haa", "ji", "haan ji",
    "of course", "do it", "go ahead", "confirm", "confirmed", "please", "absolutely", "correct", "right",
}


HINDI_YES = {"हां", "हा", "जी", "जी हां", "हां जी", "ठीक है", "बिल्कुल", "जरूर", "कर दो", "भेज दो", "हां भेज दो"}
HINDI_NO = {"नहीं", "नही", "ना", "मत", "रहने दो", "मत करो", "मत भेजो", "नहीं करना"}


NO = re.compile(r"^(?:no|nope|nah|nahi|nahin|na|mat|rehne do|chhodo|chodo|no thanks|nahi chahiye)\b")


def is_yes(answer: str) -> bool:
    from jarvis import translit

    if translit.has_devanagari(answer):  # Hindi mode: "हाँ" / "नहीं"
        words = " ".join(translit.tokenize(translit.norm(answer)))
        if any(re.search(rf"(?<!\S){translit.norm(w)}(?!\S)", words) for w in HINDI_NO):
            return False
        return any(re.search(rf"(?<!\S){translit.norm(w)}(?!\S)", words) for w in HINDI_YES)
    answer = re.sub(r"[^\w\s]", "", (answer or "").lower()).strip()
    if not answer or re.search(r"\b(?:no|nope|nah|don't|dont|cancel|stop|nahi|mat)\b", answer):
        return False
    return answer in YES_WORDS or any(re.search(rf"\b{re.escape(w)}\b", answer) for w in YES_WORDS)


@dataclass
class Context:
    voice: object
    state: dict = field(default_factory=dict)
    running: bool = True
    sleeping: bool = False
    last_skill: str | None = None
    _one_sec: object = field(default=None, repr=False)  # pending "One sec." timer

    def say(self, text, wait=True):
        if text:
            pending, self._one_sec = self._one_sec, None
            one_sec = _is_one_sec(text)
            if pending is not None and not one_sec:
                pending.cancel()  # the skill spoke in time
            if not one_sec:
                self.state["last_said"] = text
            self.voice.speak(text, wait=wait)

    def ask(self, prompt):
        self.say(prompt)
        return (self.voice.listen() or "").strip().lower()

    def confirm(self, prompt):
        return is_yes(self.ask(prompt))


ONE_SEC = "One sec."


def _is_one_sec(text):
    return text == ONE_SEC or persona.is_line("one_sec", text)


def _start_one_sec(ctx):
    """Say "One sec." if the skill hasn't said anything after ONE_SEC_AFTER seconds (slow network calls)."""
    import config

    delay = getattr(config, "ONE_SEC_AFTER", 0)
    if not delay or delay <= 0:
        return None

    def fire():
        if ctx._one_sec is timer:  # still waiting and nothing said yet
            ctx._one_sec = None
            ctx.say(persona.line("one_sec"), wait=False)

    timer = threading.Timer(delay, fire)
    timer.daemon = True
    ctx._one_sec = timer
    timer.start()
    return timer


LIST_CHUNK = 3
WANT_MORE = "Want more?"


def join_and(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def speak_list(ctx, intro, items, join=join_and, end=".", chunk=LIST_CHUNK, print_full=True):
    """Speak a long list a few items at a time: up to 3, then "Want more?". The full list is printed.

    Returns the last part for the router to speak (or "" if the user didn't want more), so a skill can
    simply `return speak_list(ctx, "You have 7 events today.", items)`.
    """
    items = [str(i) for i in items]
    if print_full and len(items) > chunk:
        log.info("\n" + (intro + "\n" if intro else "") + "\n".join(f"  {n}. {item}" for n, item in enumerate(items, 1)))
    shown, first = 0, True
    while True:
        part = items[shown:shown + chunk]
        shown += len(part)
        text = " ".join(t for t in ((intro if first else ""), (join(part) + end) if part else "") if t)
        first = False
        if shown >= len(items):
            return text
        ctx.say(text)
        if not ctx.confirm(WANT_MORE):
            return ""


@contextmanager
def one_sec(ctx):
    """Say "One sec." if nothing has been said ONE_SEC_AFTER seconds into the block."""
    timer = _start_one_sec(ctx)
    try:
        yield
    finally:
        if timer is not None:
            timer.cancel()
            if ctx._one_sec is timer:
                ctx._one_sec = None


def _run(ctx, sk, command, match):
    with one_sec(ctx):
        return _run_handler(ctx, sk, command, match)


def _run_handler(ctx, sk, command, match):
    try:
        return sk.handler(ctx, command, match)
    except SpokenError as exc:
        return str(exc)
    except Exception as exc:  # never let one skill crash the assistant
        from jarvis import http

        if http.is_request_error(exc):
            log.warning(f"[{sk.name}] network error: {exc}")
            return persona.line("network_error")
        log.exception("Skill %s crashed", sk.name)
        return persona.line("error")


def _deliver(ctx, sk, result):
    ctx.last_skill = sk.name
    if isinstance(result, str) and result:
        if sk is not FALLBACK:  # fixed skill replies get the friend's openers; the AI already has the style
            result = persona.restyle(result)
        ctx.say(result)
    return sk.name


def blocked(ctx, sk):
    """The message to say instead of running `sk`, when this context may not use it (the phone remote can't type
    into windows unless TELEGRAM_ALLOW_KEYBOARD is on), else None."""
    return (ctx.state.get("blocked_modules") or {}).get(sk.module)


def must_confirm(ctx, sk):
    """The phone remote confirms every risky action with buttons, even ones that don't ask on the laptop."""
    return bool(ctx.state.get("confirm_risky")) and (sk.risky or sk.name in ctx.state.get("confirm_skills", ())) \
        and not sk.confirms


def _try_skills(ctx, command):
    for sk in SKILLS:
        match = next((m for m in (p.search(command) for p in sk.patterns) if m), None)
        if match is None:
            continue
        refusal = blocked(ctx, sk)
        if refusal:
            ctx.say(refusal)
            return _deliver(ctx, sk, "")
        if must_confirm(ctx, sk):
            if not ctx.confirm(f"Do this on the laptop: {command}?"):
                ctx.say(persona.line("not_done"))
                return _deliver(ctx, sk, "")
        result = _run(ctx, sk, command, match)
        if result is not None:
            return _deliver(ctx, sk, result)
    return None


def handle(ctx, command):
    """Route one command. Returns the name of the skill that handled it."""
    from jarvis import translit

    original = re.sub(r"\s+", " ", (command or "").replace("﻿", "").strip())
    command = original.lower()
    if not command:
        return None
    ctx.state["original"] = original  # with capitals, for "type ..." (skills get the lower-case command)
    ctx.state["heard"] = command  # what was actually said (skills reached via the AI get an English command)
    if translit.has_devanagari(command):
        # Hindi mode: try the English rewrite first; chat that isn't a command reaches the AI unchanged
        rewritten = translit.normalize_command(command)
        if rewritten != command:
            log.info(f"(understood as: {rewritten})")
            handled = _try_skills(ctx, rewritten)
            if handled:
                return handled
    ctx.state.pop("last_said", None)
    ctx.state.pop("understood", None)
    offer = ctx.state.pop("offer", None)  # "can't order pizza, Google pe dhoondh doon?" -> "haan"
    if offer and is_yes(command):
        log.info(f"(offer accepted: {offer})")
        return handle(ctx, offer)
    if offer and NO.search(command):
        ctx.say(persona.line("never_mind"))
        remember_turn(ctx, command, "offer")
        return "offer"
    handled = _try_skills(ctx, command)
    if not handled and FALLBACK is not None:
        from jarvis import intent

        handled = intent.resolve(ctx, command)  # natural language -> a registered skill, or a direct AI answer
        if not handled:
            result = _run(ctx, FALLBACK, command, None)
            handled = _deliver(ctx, FALLBACK, result or "")
    if handled:
        remember_turn(ctx, command, handled)
        nudge = persona.late_night_nudge(ctx.state) if persona.is_friend() and handled != "exit" else None
        if nudge:
            ctx.say(nudge)
        return handled
    ctx.say(persona.line("dont_know"))
    return None


MAX_TURNS_REMEMBERED = 3


def remember_turn(ctx, command, skill_name):
    """Keep the last 3 turns so the AI can resolve follow-ups ("what about Noida?", "and tomorrow?")."""
    if ctx.state.get("last_said"):
        ctx.state["last_answer"] = ctx.state["last_said"]  # "copy that" copies it in full
    turns = ctx.state.setdefault("turns", [])
    turns.append({"you": command, "understood": ctx.state.get("understood") or command, "skill": skill_name,
                  "jarvis": (ctx.state.get("last_said") or "")[:200]})
    del turns[:-MAX_TURNS_REMEMBERED]
