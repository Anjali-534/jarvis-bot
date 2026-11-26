"""Surviving AI model retirements (HTTP mocked): pick a replacement, retry, save it to .env, say so once."""
import pytest
from helpers import StreamResponse, gemini_stream, make_ctx, openai_stream

import config
from jarvis import cache, http
from jarvis.router import handle
from jarvis.skills import ai

GROQ = "https://api.groq.com/openai/v1"
FAKE_KEY = "test-key-not-real"
ENV_TEXT = ("# --- AI fallback ---\r\nAI_PROVIDER=openai\r\nAI_API_KEY={key}\r\nAI_BASE_URL=" + GROQ +
            "\r\nAI_MODEL=llama-3.3-70b-versatile\r\nAI_REASONING_EFFORT=none\r\nVOICE_RATE=185\r\n")
DECOMMISSIONED = ('{"error":{"message":"The model `llama-3.3-70b-versatile` has been decommissioned and is no longer '
                  'supported.","type":"invalid_request_error","code":"model_decommissioned"}}')
GROQ_MODELS = {"data": [
    {"id": "whisper-large-v3", "active": True},
    {"id": "openai/gpt-oss-120b", "active": True},
    {"id": "openai/gpt-oss-20b", "active": True},
    {"id": "qwen/qwen3.8-27b", "active": False},  # listed but switched off
]}


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(config, "AI_BASE_URL", GROQ)
    monkeypatch.setattr(config, "AI_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setattr(config, "AI_MODEL_FALLBACKS", ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"])
    config.ENV_FILE.write_text(ENV_TEXT.format(key=FAKE_KEY), encoding="utf-8", newline="")
    posts, listings, responses = [], [], []

    def post(url, payload, headers=None, timeout=30):
        posts.append(payload["model"])
        return responses.pop(0)

    def get_json(url, params=None, headers=None, timeout=None, **kwargs):
        listings.append(url)
        return GROQ_MODELS

    monkeypatch.setattr(http, "post_stream", post)
    monkeypatch.setattr(http, "post_json", post)
    monkeypatch.setattr(http, "get_json", get_json)
    return posts, listings, responses


def env_text():
    with open(config.ENV_FILE, encoding="utf-8", newline="") as fh:
        return fh.read()


@pytest.mark.parametrize("status, body, retired", [
    (404, "not found", True),
    (400, DECOMMISSIONED, True),
    (400, '{"error":{"code":"model_not_found","message":"The model `x` does not exist"}}', True),
    (400, '{"error":{"message":"models/gemini-1.0-pro is not found for API version v1beta"}}', True),
    (401, "model not found", False),            # a key problem, not a retirement
    (429, "rate limit for model x", False),
    (400, "bad request: messages too long", False),
])
def test_is_retired(status, body, retired):
    assert ai.is_retired(status, body) is retired


def test_retired_model_is_replaced_retried_and_saved(groq, caplog):
    posts, listings, responses = groq
    responses += [StreamResponse(status_code=400, text=DECOMMISSIONED),
                  openai_stream("Main theek hoon, shukriya."), openai_stream("Bilkul, poochiye.")]
    ctx = make_ctx()
    handle(ctx, "tum kaise ho")
    # qwen is first in AI_MODEL_FALLBACKS but inactive; gpt-oss-20b is next
    assert posts == ["llama-3.3-70b-versatile", "openai/gpt-oss-20b"]
    assert listings == [f"{GROQ}/models"]
    assert ctx.voice.said == [ai.SWITCHED, "Main theek hoon, shukriya."]
    assert config.AI_MODEL == "openai/gpt-oss-20b"
    assert "switching to openai/gpt-oss-20b" in caplog.text and FAKE_KEY not in caplog.text

    expected = ENV_TEXT.format(key=FAKE_KEY).replace("AI_MODEL=llama-3.3-70b-versatile", "AI_MODEL=openai/gpt-oss-20b")
    assert env_text() == expected  # only the AI_MODEL line changed; key, CRLF line endings and the rest intact

    handle(ctx, "ek sawaal hai")
    assert ctx.voice.said[-1] == "Bilkul, poochiye." and ctx.voice.said.count(ai.SWITCHED) == 1  # said once
    assert posts[-1] == "openai/gpt-oss-20b" and len(listings) == 1


def test_falls_back_to_first_chat_model_when_no_preference_is_offered(groq, monkeypatch):
    posts, _, responses = groq
    monkeypatch.setattr(config, "AI_MODEL_FALLBACKS", ["some/other-model"])
    responses += [StreamResponse(status_code=404, text="not found"), openai_stream("Hello there, friend.")]
    ctx = make_ctx()
    handle(ctx, "hello there jarvis friend")
    assert posts[-1] == "openai/gpt-oss-120b"  # first chat model (whisper is skipped)


def test_nothing_available_gives_clear_error(groq, monkeypatch):
    posts, _, responses = groq
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"data": [{"id": "whisper-large-v3"}]})
    responses.append(StreamResponse(status_code=400, text=DECOMMISSIONED))
    ctx = make_ctx()
    handle(ctx, "tum kaise ho")
    assert ctx.voice.last == ("The AI model llama 3.3 70b versatile has been retired, and I couldn't find a replacement. "
                              "Please pick a current model in Settings.")
    assert posts == ["llama-3.3-70b-versatile"]  # no pointless retry
    assert env_text() == ENV_TEXT.format(key=FAKE_KEY)  # .env untouched


def test_model_list_failing_gives_clear_error(groq, monkeypatch):
    _, _, responses = groq

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(http, "get_json", offline)
    responses.append(StreamResponse(status_code=404, text="not found"))
    ctx = make_ctx()
    handle(ctx, "tum kaise ho")
    assert "has been retired, and I couldn't find a replacement" in ctx.voice.last


def test_other_errors_do_not_trigger_a_switch(groq):
    posts, listings, responses = groq
    responses.append(StreamResponse(status_code=401, text='{"error":{"message":"Invalid API Key"}}'))
    ctx = make_ctx()
    handle(ctx, "tum kaise ho")
    assert ctx.voice.last.startswith("My AI key was rejected") and listings == [] and posts == ["llama-3.3-70b-versatile"]


def test_gmail_summary_also_recovers(groq):
    from jarvis.skills import gmail

    posts, _, responses = groq

    class Json:
        def __init__(self, status, data=None, text=""):
            self.status_code, self._data, self.text = status, data, text

        def json(self):
            return self._data

    responses += [Json(404, text="not found"),
                  Json(200, {"choices": [{"message": {"content": "The demo moved to 5."}}]})]
    assert gmail.summarize("Hi. The demo moved to 5 pm. Bring the laptop.") == "The demo moved to 5."
    assert posts == ["llama-3.3-70b-versatile", "openai/gpt-oss-20b"]


def test_gemini_recovery_uses_fallbacks_then_newest_flash(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_MODEL", "gemini-1.5-flash")
    monkeypatch.setattr(config, "AI_MODEL_FALLBACKS", ["qwen/qwen3.8-27b"])  # not a Gemini model
    models = [{"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]
    monkeypatch.setattr(http, "get_json", lambda url, **k: {"models": models})
    responses, urls = [StreamResponse(status_code=404, text="not found"), gemini_stream("Hello there, friend!")], []
    monkeypatch.setattr(http, "post_stream", lambda url, payload, **k: urls.append(url) or responses.pop(0))
    ctx = make_ctx()
    handle(ctx, "hi there friend")
    assert ctx.voice.said == [ai.SWITCHED, "Hello there, friend!"]
    assert "/models/gemini-2.5-flash:" in urls[1]
    assert "AI_MODEL=gemini-2.5-flash" in env_text()


def test_update_env_model_appends_when_missing_and_keeps_lf(tmp_path):
    path = tmp_path / "x.env"
    path.write_text("AI_API_KEY=abc\nNOTES_FILE=data/notes.txt", encoding="utf-8", newline="")
    ai.update_env_model("openai/gpt-oss-20b", path)
    assert path.read_text(encoding="utf-8") == "AI_API_KEY=abc\nNOTES_FILE=data/notes.txt\nAI_MODEL=openai/gpt-oss-20b\n"
    ai.update_env_model("qwen/qwen3.8-27b", path)
    assert path.read_text(encoding="utf-8").count("AI_MODEL=") == 1
    assert path.read_text(encoding="utf-8").endswith("AI_MODEL=qwen/qwen3.8-27b\n")


# ---------------------------------------------------------------- startup check
def test_startup_check_passes_and_is_cached_for_a_day(groq, monkeypatch):
    posts, listings, _ = groq
    monkeypatch.setattr(config, "AI_MODEL", "openai/gpt-oss-120b")
    assert ai.check_model() is True
    assert ai.check_model() is True
    assert listings == [f"{GROQ}/models"]  # second check came from data/cache.json
    import time

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + ai.MODEL_CHECK_SECONDS + 60)
    assert ai.check_model() is True and len(listings) == 2  # a day later it checks again


def test_startup_check_switches_before_the_first_question(groq):
    posts, listings, responses = groq
    assert ai.check_model() == "openai/gpt-oss-20b"
    assert "AI_MODEL=openai/gpt-oss-20b" in env_text() and posts == []
    responses.append(openai_stream("Main theek hoon."))
    ctx = make_ctx()
    handle(ctx, "tum kaise ho")
    assert posts == ["openai/gpt-oss-20b"]  # the first question goes straight to the working model
    assert ctx.voice.said == [ai.SWITCHED, "Main theek hoon."]


def test_startup_check_offline_or_without_key(groq, monkeypatch):
    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(http, "get_json", offline)
    assert ai.check_model() is None and config.AI_MODEL == "llama-3.3-70b-versatile"
    monkeypatch.setattr(config, "AI_API_KEY", "")
    assert ai.check_model() is None


def test_startup_hook_runs_in_background(groq, monkeypatch):
    started = []
    import threading

    class FakeThread:
        def __init__(self, target, name, daemon):
            started.append(name)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", FakeThread)
    ai._check_model_in_background(make_ctx())
    assert started == ["ai-model-check"]


# ---------------------------------------------------------------- "which AI model are you using"
@pytest.mark.parametrize("phrase", ["which ai model are you using", "what model are you using",
                                    "what's your ai model", "which ai are you"])
def test_which_model_command(groq, monkeypatch, phrase):
    monkeypatch.setattr(config, "AI_MODEL", "qwen/qwen3.8-27b")
    ctx = make_ctx()
    assert handle(ctx, phrase) == "ai model"
    assert ctx.voice.last == "I'm using Groq, with the model qwen 3.8 27b."


def test_which_model_command_gemini_and_no_key(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_MODEL", "gemini-2.5-flash")
    ctx = make_ctx()
    handle(ctx, "which ai model are you using")
    assert ctx.voice.last == "I'm using Google Gemini, with the model gemini 2.5 flash."
    monkeypatch.setattr(config, "AI_API_KEY", "")
    handle(ctx, "which ai model are you using")
    assert ctx.voice.last.startswith("No AI is set up yet.")
