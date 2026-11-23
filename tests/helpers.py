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


class StreamResponse:
    """A fake streaming HTTP response: server-sent events, or an error with a body."""

    def __init__(self, events=(), status_code=200, text=""):
        self.status_code, self.text, self.encoding = status_code, text, None
        self._lines = [f"data: {e}" if isinstance(e, str) else "data: " + __import__("json").dumps(e) for e in events]

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line
            yield ""


def openai_stream(text, size=7):
    """An OpenAI-style stream that delivers `text` in small chunks, ending with [DONE]."""
    events = [{"choices": [{"delta": {"role": "assistant"}}]}]
    events += [{"choices": [{"delta": {"content": text[i:i + size]}}]} for i in range(0, len(text), size)]
    return StreamResponse(events + ["[DONE]"])


def gemini_stream(text, size=12, finish="STOP"):
    events = [{"candidates": [{"content": {"role": "model", "parts": [{"text": text[i:i + size]}]}}]}
              for i in range(0, len(text), size)]
    events.append({"candidates": [{"content": {"parts": []}, "finishReason": finish}], "usageMetadata": {}})
    return StreamResponse(events)
