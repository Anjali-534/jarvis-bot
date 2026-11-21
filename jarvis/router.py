"""Skill registry, conversation Context and the command dispatcher.

A skill is a function ``handler(ctx, command, match)`` registered with ``@skill``:

* return a non-empty string  -> it is spoken
* return "" or True          -> handled, nothing more to say
* return None                -> "not mine", the router tries the next skill
"""
import re
import traceback
from dataclasses import dataclass, field
from typing import Callable

import requests


@dataclass
class Skill:
    name: str
    patterns: list
    handler: Callable
    examples: list = field(default_factory=list)  # example phrases for help and COMMANDS.md
    category: str | None = None                   # a key of jarvis.skills.general.CATEGORIES


SKILLS: list[Skill] = []
FALLBACK: Skill | None = None


class SpokenError(Exception):
    """Raise from a skill to stop it and speak a friendly message."""


def skill(name, *patterns, help=(), category=None):
    """Register a skill triggered by any of the regex patterns (case-insensitive).

    help: an example phrase, or a list of them, shown by "help" / "help with <category>".
    category: which help category the skill belongs to.
    """
    examples = [help] if isinstance(help, str) and help else list(help)

    def decorator(fn):
        SKILLS.append(Skill(name, [re.compile(p, re.IGNORECASE) for p in patterns], fn, examples, category))
        return fn

    return decorator


def skills_in(category):
    return [s for s in SKILLS if s.category == category]


def fallback(fn):
    """Register the handler for anything no skill matched (the AI)."""
    global FALLBACK
    FALLBACK = Skill(fn.__name__, [], fn)
    return fn


YES_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "haan", "han", "ha", "haa", "ji", "haan ji",
    "of course", "do it", "go ahead", "confirm", "confirmed", "please", "absolutely", "correct", "right",
}


HINDI_YES = {"हां", "हा", "जी", "जी हां", "हां जी", "ठीक है", "बिल्कुल", "जरूर", "कर दो", "भेज दो", "हां भेज दो"}
HINDI_NO = {"नहीं", "नही", "ना", "मत", "रहने दो", "मत करो", "मत भेजो", "नहीं करना"}


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

    def say(self, text, wait=True):
        if text:
            self.voice.speak(text, wait=wait)

    def ask(self, prompt):
        self.say(prompt)
        return (self.voice.listen() or "").strip().lower()

    def confirm(self, prompt):
        return is_yes(self.ask(prompt))


def _run(ctx, sk, command, match):
    try:
        return sk.handler(ctx, command, match)
    except SpokenError as exc:
        return str(exc)
    except requests.RequestException as exc:
        print(f"[{sk.name}] network error: {exc}")
        return "I couldn't reach the online service right now. Please check your internet and try again."
    except Exception:  # never let one skill crash the assistant
        traceback.print_exc()
        return "Sorry, something went wrong while doing that."


def _deliver(ctx, sk, result):
    ctx.last_skill = sk.name
    if isinstance(result, str) and result:
        ctx.say(result)
    return sk.name


def _try_skills(ctx, command):
    for sk in SKILLS:
        match = next((m for m in (p.search(command) for p in sk.patterns) if m), None)
        if match is None:
            continue
        result = _run(ctx, sk, command, match)
        if result is not None:
            return _deliver(ctx, sk, result)
    return None


def handle(ctx, command):
    """Route one command. Returns the name of the skill that handled it."""
    from jarvis import translit

    command = re.sub(r"\s+", " ", (command or "").replace("﻿", "").strip().lower())
    if not command:
        return None
    if translit.has_devanagari(command):
        # Hindi mode: try the English rewrite first; chat that isn't a command reaches the AI unchanged
        rewritten = translit.normalize_command(command)
        if rewritten != command:
            print(f"(understood as: {rewritten})")
            handled = _try_skills(ctx, rewritten)
            if handled:
                return handled
    handled = _try_skills(ctx, command)
    if handled:
        return handled
    if FALLBACK is not None:
        result = _run(ctx, FALLBACK, command, None)
        return _deliver(ctx, FALLBACK, result or "")
    ctx.say("Sorry, I don't know how to do that yet.")
    return None
