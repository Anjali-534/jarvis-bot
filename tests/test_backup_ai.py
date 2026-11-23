"""Gemini as the automatic backup for Groq (HTTP mocked): failover, daily-limit stickiness, both failing."""
import json
from datetime import datetime

import pytest
import requests
from helpers import StreamResponse, gemini_stream, make_ctx, openai_stream

import config
from jarvis import cache, http
from jarvis.router import handle
from jarvis.skills import ai, system

GROQ = "https://api.groq.com/openai/v1"
CHAT = "say something nice about mondays"  # no command pattern: goes to the AI
PER_MINUTE = ('{"error":{"message":"Rate limit reached for model `qwen/qwen3.8-27b` on tokens per minute (TPM): '
              'Limit 8000, Used 7900, Requested 900.","type":"tokens","code":"rate_limit_exceeded"}}')
PER_DAY = ('{"error":{"message":"Rate limit reached for model `qwen/qwen3.8-27b` on requests per day (RPD): '
           'Limit 1000, Used 1000, Requested 1.","type":"requests","code":"rate_limit_exceeded"}}')


class Response:
    def __init__(self, data=None, status=200, text=None):
        self.status_code, self._data = status, data
        self.text = text if text is not None else json.dumps(data)

    def json(self):
        return self._data


def groq_tool(name, **args):
    return Response({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]})


def gemini_tool(name, **args):
    return Response({"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args}}]}}]})


def error(status, text):
    return StreamResponse(status_code=status, text=text)


@pytest.fixture
def backends(monkeypatch):
    """Groq main, Gemini backup. Queue responses (or exceptions) in backends['groq'] / backends['gemini']."""
    monkeypatch.setattr(config, "AI_API_KEY", "groq-test-key")
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(config, "AI_BASE_URL", GROQ)
    monkeypatch.setattr(config, "AI_MODEL", "qwen/qwen3.8-27b")
    monkeypatch.setattr(config, "AI_REASONING_EFFORT", "none")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-test-key")
    queues = {"groq": [], "gemini": [], "calls": []}

    def post(url, payload, headers=None, timeout=None):
        who = "gemini" if "generativelanguage.googleapis.com" in url else "groq"
        queues["calls"].append({"who": who, "url": url, "headers": headers, "timeout": timeout})
        if not queues[who]:
            pytest.fail(f"unexpected request to {who}")
        response = queues[who].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(http, "post_json", post)
    monkeypatch.setattr(http, "post_stream", post)
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"data": []})  # model listing (retirement check)
    return queues


def who_was_asked(backends):
    return [c["who"] for c in backends["calls"]]


# ---------------------------------------------------------------- chat
def test_groq_429_gemini_answers(backends):
    backends["groq"].append(error(429, PER_MINUTE))
    backends["gemini"].append(gemini_stream("Mondays are a fresh start. Make it count!"))
    ctx = make_ctx()
    assert handle(ctx, CHAT) == "ai"
    assert ctx.voice.said == [ai.BACKUP_SWITCH, "Mondays are a fresh start.", "Make it count!"]
    gemini = backends["calls"][1]
    assert "/models/gemini-3.5-flash-lite:streamGenerateContent" in gemini["url"]
    assert gemini["headers"] == {"x-goog-api-key": "gemini-test-key"}  # the backup key, not Groq's
    assert ai.backup_until() is None  # a per-minute limit isn't sticky


def test_after_a_one_off_failover_groq_goes_first_again(backends, caplog):
    backends["groq"] += [error(429, PER_MINUTE), openai_stream("Back on Groq here.")]
    backends["gemini"].append(gemini_stream("Gemini here, helping out."))
    ctx = make_ctx()
    handle(ctx, CHAT)
    with caplog.at_level("WARNING"):
        handle(ctx, "say something nice about tuesdays")
    assert who_was_asked(backends) == ["groq", "gemini", "groq"]
    assert ctx.voice.said[-1] == "Back on Groq here."
    assert "back on the main AI" in caplog.text


@pytest.mark.parametrize("failure", [
    error(500, "internal error"),
    error(503, "service unavailable"),
    error(401, '{"error":{"message":"Invalid API Key"}}'),
    error(404, '{"error":{"code":"model_not_found","message":"The model does not exist"}}'),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectionError("connection reset"),
], ids=["500", "503", "bad key", "model gone", "timeout", "connection"])
def test_failover_reasons(backends, failure):
    backends["groq"].append(failure)
    backends["gemini"].append(gemini_stream("Gemini answering for Groq."))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert ctx.voice.said == [ai.BACKUP_SWITCH, "Gemini answering for Groq."]


def test_switch_announced_once_per_switch(backends, caplog):
    backends["groq"] += [error(503, "down"), error(503, "down"), openai_stream("Groq is fine again."),
                         error(503, "down")]
    backends["gemini"] += [gemini_stream("First backup answer."), gemini_stream("Second backup answer."),
                           gemini_stream("Third backup answer.")]
    ctx = make_ctx()
    with caplog.at_level("WARNING"):
        for n in range(4):
            handle(ctx, f"{CHAT} number {n}")
    assert ctx.voice.said == [ai.BACKUP_SWITCH, "First backup answer.", "Second backup answer.",
                              "Groq is fine again.", ai.BACKUP_SWITCH, "Third backup answer."]
    assert caplog.text.count("switching to the backup AI, Gemini (gemini-3.5-flash-lite)") == 2


# ---------------------------------------------------------------- no failover
def test_no_failover_on_a_normal_answer(backends):
    backends["groq"].append(openai_stream("Mondays are great."))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert who_was_asked(backends) == ["groq"]
    assert ctx.voice.said == ["Mondays are great."]
    assert ai.BACKUP_SWITCH not in ctx.voice.said


def test_no_failover_on_a_bad_request(backends):
    backends["groq"].append(error(400, '{"error":{"message":"messages: too long"}}'))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert who_was_asked(backends) == ["groq"]  # Gemini wouldn't do better with the same request
    assert ctx.voice.last == "The AI service returned an error, code 400."


def test_without_a_gemini_key_nothing_changes(backends, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    backends["groq"].append(error(429, PER_MINUTE))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert who_was_asked(backends) == ["groq"]
    assert ctx.voice.last.startswith("I've used up the free AI quota")


def test_gemini_main_has_no_gemini_backup(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-test-key")
    assert not ai.has_backup()


# ---------------------------------------------------------------- daily limit: Gemini until midnight IST
def ist(*args):
    return datetime(*args, tzinfo=ai.IST).timestamp()


@pytest.mark.parametrize("at, midnight", [
    ((2026, 11, 23, 15, 0), (2026, 11, 24)),
    ((2026, 11, 23, 23, 59), (2026, 11, 24)),
    ((2026, 11, 24, 0, 5), (2026, 11, 25)),
    ((2026, 12, 31, 20, 0), (2027, 1, 1)),
])
def test_next_midnight_ist(at, midnight):
    assert ai.next_midnight_ist(ist(*at)) == ist(*midnight)


def test_daily_limit_sticks_to_gemini_until_midnight(backends, monkeypatch):
    clock = [ist(2026, 11, 23, 22, 0)]
    monkeypatch.setattr(ai, "now", lambda: clock[0])
    backends["groq"].append(error(429, PER_DAY))
    backends["gemini"] += [gemini_stream("Backup answer one."), gemini_stream("Backup answer two.")]
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert cache.get(ai.BACKUP_UNTIL, 10 ** 6) == ist(2026, 11, 24)  # saved in data/cache.json
    assert config.CACHE_FILE.exists()

    clock[0] = ist(2026, 11, 23, 23, 30)
    handle(ctx, "say something nice about tuesdays")
    assert who_was_asked(backends) == ["groq", "gemini", "gemini"]  # Groq skipped while its daily limit is out
    assert ctx.voice.said.count(ai.BACKUP_SWITCH) == 1

    handle(ctx, "which ai model are you using")
    assert ctx.voice.last == ("I'm on my backup brain, Google Gemini, gemini 3.5 flash lite, because Groq's daily "
                              "limit is used up. I'll go back to Groq at midnight.")

    clock[0] = ist(2026, 11, 24, 0, 1)  # past midnight IST: Groq first again
    backends["groq"].append(openai_stream("Good morning from Groq."))
    handle(ctx, "say something nice about wednesdays")
    assert who_was_asked(backends)[-1] == "groq" and ctx.voice.last == "Good morning from Groq."
    assert ai.backup_until() is None


def test_daily_limit_survives_a_restart(backends, monkeypatch):
    monkeypatch.setattr(ai, "now", lambda: ist(2026, 11, 23, 18, 0))
    cache.put(ai.BACKUP_UNTIL, ist(2026, 11, 24))
    monkeypatch.setattr(cache, "_data", None)  # as if Jarvis restarted: read data/cache.json again
    backends["gemini"].append(gemini_stream("Still on the backup."))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert who_was_asked(backends) == ["gemini"]


def test_sticky_backup_failing_still_tries_groq(backends, monkeypatch):
    monkeypatch.setattr(ai, "now", lambda: ist(2026, 11, 23, 18, 0))
    cache.put(ai.BACKUP_UNTIL, ist(2026, 11, 24))
    backends["gemini"].append(error(503, "high demand"))
    backends["groq"].append(openai_stream("Groq had room after all."))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert who_was_asked(backends) == ["gemini", "groq"] and ctx.voice.last == "Groq had room after all."


# ---------------------------------------------------------------- both fail
def test_both_fail_one_clear_message(backends):
    backends["groq"].append(error(429, PER_MINUTE))
    backends["gemini"].append(error(503, "high demand"))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert ctx.voice.said == [ai.BOTH_DOWN]


def test_both_time_out_one_clear_message(backends):
    backends["groq"].append(requests.exceptions.ReadTimeout("slow"))
    backends["gemini"].append(requests.exceptions.ReadTimeout("slow"))
    ctx = make_ctx()
    handle(ctx, CHAT)
    assert ctx.voice.said == [ai.BOTH_DOWN]


# ---------------------------------------------------------------- routing (natural-language actions)
@pytest.fixture
def laptop(monkeypatch):
    pressed = []
    monkeypatch.setattr(config, "AI_ROUTING", True)
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "press", lambda key, presses=1: pressed.append((key, presses)))
    return pressed


def test_routing_fails_over_to_gemini(backends, laptop):
    backends["groq"].append(Response(status=429, text=PER_MINUTE))
    backends["gemini"].append(gemini_tool("volume", direction="up"))
    ctx = make_ctx()
    assert handle(ctx, "yaar thoda sound badha do") == "volume up"
    assert laptop == [("volumeup", 5)]
    assert ctx.voice.said[0] == ai.BACKUP_SWITCH
    groq_call, gemini_call = backends["calls"]
    assert groq_call["timeout"] == 4.0 and gemini_call["timeout"] == 8.0
    assert "/models/gemini-3.5-flash-lite:generateContent" in gemini_call["url"]
    assert gemini_call["headers"] == {"x-goog-api-key": "gemini-test-key"}  # the backup's own key


def test_routing_timeout_fails_over(backends, laptop):
    backends["groq"].append(requests.exceptions.ReadTimeout("4 s"))
    backends["gemini"].append(gemini_tool("volume", direction="down"))
    ctx = make_ctx()
    assert handle(ctx, "it's way too loud") == "volume down"


def test_slow_backup_routing_says_one_sec(backends, laptop, monkeypatch):
    import time

    monkeypatch.setattr(config, "ONE_SEC_AFTER", 0.05)
    backends["groq"].append(Response(status=503, text="down"))
    slow = gemini_tool("volume", direction="up")

    def gemini_slowly(*args, **kwargs):
        time.sleep(0.2)
        return slow

    real = http.post_json
    monkeypatch.setattr(http, "post_json",
                        lambda url, *a, **k: gemini_slowly() if "generativelanguage" in url else real(url, *a, **k))
    ctx = make_ctx()
    assert handle(ctx, "yaar thoda sound badha do") == "volume up"
    assert ctx.voice.said[:2] == ["One sec.", ai.BACKUP_SWITCH]


def test_routing_and_chat_both_down_one_message(backends, laptop):
    backends["groq"].append(Response(status=429, text=PER_MINUTE))
    backends["gemini"].append(Response(status=503, text="high demand"))
    ctx = make_ctx()
    handle(ctx, "yaar thoda sound badha do")
    assert ctx.voice.said == [ai.BOTH_DOWN]
    assert who_was_asked(backends) == ["groq", "gemini"]  # chat didn't try both again
    assert laptop == []


def test_routing_daily_limit_is_sticky_too(backends, laptop, monkeypatch):
    monkeypatch.setattr(ai, "now", lambda: ist(2026, 11, 23, 12, 0))
    backends["groq"].append(Response(status=429, text=PER_DAY))
    backends["gemini"] += [gemini_tool("volume", direction="up"), gemini_tool("volume", direction="down")]
    ctx = make_ctx()
    handle(ctx, "yaar thoda sound badha do")
    handle(ctx, "ab thoda kam karo")
    assert who_was_asked(backends) == ["groq", "gemini", "gemini"]
    assert ai.backup_until() == ist(2026, 11, 24)


def test_routing_normal_answer_never_asks_gemini(backends, laptop):
    backends["groq"].append(groq_tool("volume", direction="up"))
    ctx = make_ctx()
    handle(ctx, "yaar thoda sound badha do")
    assert who_was_asked(backends) == ["groq"] and ai.BACKUP_SWITCH not in ctx.voice.said


# ---------------------------------------------------------------- "which AI model are you using"
def test_which_model_reports_backup_state(backends):
    ctx = make_ctx()
    handle(ctx, "which ai model are you using")
    assert ctx.voice.last == ("I'm using Groq, with the model qwen 3.8 27b. My backup is Google Gemini, "
                              "gemini 3.5 flash lite, and it isn't in use right now.")
    backends["groq"].append(error(503, "down"))
    backends["gemini"].append(gemini_stream("Backup speaking."))
    handle(ctx, CHAT)
    handle(ctx, "which ai model are you using")
    assert ctx.voice.last == ("I'm on my backup brain, Google Gemini, gemini 3.5 flash lite, because Groq didn't "
                              "answer last time. My main AI is Groq, with the model qwen 3.8 27b.")
