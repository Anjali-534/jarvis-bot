"""Shared test helpers."""
from jarvis.router import Context


class FakeVoice:
    text_mode = False

    def __init__(self, answers=None, stop_when_empty=False):
        self.said = []
        self.answers = list(answers or [])
        self.stop_when_empty = stop_when_empty  # main-loop tests: Ctrl+C instead of looping forever

    def speak(self, text, wait=True):
        self.said.append(text)

    def listen(self, timeout=8, phrase_time_limit=12):
        if not self.answers and self.stop_when_empty:
            raise KeyboardInterrupt
        return self.answers.pop(0) if self.answers else ""

    @property
    def last(self):
        return self.said[-1] if self.said else ""


def make_ctx(answers=None):
    return Context(FakeVoice(answers))
