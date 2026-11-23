"""Per-turn latency measurements: wake word, speech detected, end of phrase, speech-to-text, skill, speech.

Logged at DEBUG level, or printed for every turn with `python main.py --timing`.
"""
import logging
import time

log = logging.getLogger("jarvis.timing")

ENABLED = False  # set by --timing: report every turn at INFO level

STAGES = [  # (key, label) in the order they're reported
    ("wake", "wake word"),
    ("speech", "speech detected"),
    ("end", "end of phrase"),
    ("stt", "speech-to-text"),
    ("skill", "routing + skill"),
    ("tts", "text-to-speech"),
]


class Turn:
    def __init__(self):
        self.start = time.perf_counter()
        self.marks = {}       # moments, seconds since the turn started
        self.durations = {}   # accumulated durations

    def mark(self, name):
        self.marks.setdefault(name, time.perf_counter() - self.start)

    def add(self, name, seconds):
        self.durations[name] = self.durations.get(name, 0.0) + seconds

    def line(self):
        parts = []
        for key, label in STAGES:
            if key in self.marks:
                parts.append(f"{label} at {self.marks[key]:.2f}s")
            elif key in self.durations:
                parts.append(f"{label} {self.durations[key]:.2f}s")
        parts.append(f"total {time.perf_counter() - self.start:.2f}s")
        return "[timing] " + " | ".join(parts)


_current = None


def begin():
    """Start timing a new turn (when Jarvis starts listening, or when typed text arrives)."""
    global _current
    _current = Turn()
    return _current


def current():
    return _current


def mark(name):
    if _current:
        _current.mark(name)


def add(name, seconds):
    if _current:
        _current.add(name, seconds)


def finish():
    """Report the turn and forget it. Returns the report line (or None if nothing was timed)."""
    global _current
    turn, _current = _current, None
    if turn is None:
        return None
    line = turn.line()
    log.log(logging.INFO if ENABLED else logging.DEBUG, line)
    return line
