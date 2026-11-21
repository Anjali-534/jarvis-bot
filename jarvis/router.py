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
    help: str = ""


SKILLS: list[Skill] = []
FALLBACK: Skill | None = None


class SpokenError(Exception):
    """Raise from a skill to stop it and speak a friendly message."""


def skill(name, *patterns, help=""):
    """Register a skill triggered by any of the regex patterns (case-insensitive)."""

    def decorator(fn):
        SKILLS.append(Skill(name, [re.compile(p, re.IGNORECASE) for p in patterns], fn, help))
        return fn

    return decorator


def fallback(fn):
    """Register the handler for anything no skill matched (the AI)."""
    global FALLBACK
    FALLBACK = Skill(fn.__name__, [], fn)
    return fn


YES_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "haan", "han", "ha", "haa", "ji", "haan ji",
    "of course", "do it", "go ahead", "confirm", "confirmed", "please", "absolutely", "correct", "right",
}


def is_yes(answer: str) -> bool:
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


def handle(ctx, command):
    """Route one command. Returns the name of the skill that handled it."""
    command = re.sub(r"\s+", " ", (command or "").replace("﻿", "").strip().lower())
    if not command:
        return None
    for sk in SKILLS:
        match = next((m for m in (p.search(command) for p in sk.patterns) if m), None)
        if match is None:
            continue
        result = _run(ctx, sk, command, match)
        if result is not None:
            return _deliver(ctx, sk, result)
    if FALLBACK is not None:
        result = _run(ctx, FALLBACK, command, None)
        return _deliver(ctx, FALLBACK, result or "")
    ctx.say("Sorry, I don't know how to do that yet.")
    return None
