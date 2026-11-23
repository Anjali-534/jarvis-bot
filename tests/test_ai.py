"""Native Gemini API tests (HTTP mocked): request shape, history, blocked replies, errors, model fallback."""
import pytest
from helpers import make_ctx

import config
from jarvis import http
from jarvis.router import handle
from jarvis.skills import ai

KEY = "AQ.test-key"


class Response:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data or {}, text

    def json(self):
        return self._data


def reply(text, finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]}


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", KEY)
    monkeypatch.setattr(config, "AI_PROVIDER", "")
    monkeypatch.setattr(config, "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setattr(config, "AI_MODEL", "gemini-2.5-flash")
    calls, responses = [], []

    def post(url, payload, headers=None, timeout=30):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(http, "post_json", post)
    return calls, responses


@pytest.mark.parametrize("provider, base, expected", [
    ("", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
    ("", "https://api.groq.com/openai/v1", "openai"),
    ("openai", "https://generativelanguage.googleapis.com/v1beta/openai", "openai"),
    ("gemini", "https://api.openai.com/v1", "gemini"),
])
def test_provider_choice(monkeypatch, provider, base, expected):
    monkeypatch.setattr(config, "AI_PROVIDER", provider)
    monkeypatch.setattr(config, "AI_BASE_URL", base)
    assert ai.provider() == expected


def test_request_shape_and_history(gemini):
    calls, responses = gemini
    responses += [Response(data=reply("Haan, main Hindi samajhta hoon.")), Response(data=reply("**Sure.**"))]
    ctx = make_ctx()
    handle(ctx, "do you know hindi")
    assert ctx.voice.last == "Haan, main Hindi samajhta hoon."
    first = calls[0]
    assert first["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert first["headers"] == {"x-goog-api-key": KEY}  # header only: no Bearer, no ?key=
    assert "key=" not in first["url"] and first["timeout"] == 15
    body = first["payload"]
    assert "Hinglish" in body["systemInstruction"]["parts"][0]["text"]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "do you know hindi"}]}]
    assert body["generationConfig"] == {"temperature": 0.6, "maxOutputTokens": 300}

    handle(ctx, "tell me more")
    assert [c["role"] for c in calls[1]["payload"]["contents"]] == ["user", "model", "user"]
    assert calls[1]["payload"]["contents"][1]["parts"][0]["text"] == "Haan, main Hindi samajhta hoon."
    assert ctx.voice.last == "Sure."


def test_multi_part_reply_is_joined(gemini):
    _, responses = gemini
    responses.append(Response(data={"candidates": [{"content": {"parts": [{"text": "Thandi hawa, "},
                                                                           {"text": "garam chai."}]}}]}))
    ctx = make_ctx()
    handle(ctx, "write a 2 line poem")
    assert ctx.voice.last == "Thandi hawa, garam chai."


@pytest.mark.parametrize("data", [
    {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
    {"candidates": []},
])
def test_blocked_responses(gemini, data):
    _, responses = gemini
    responses.append(Response(data=data))
    ctx = make_ctx()
    handle(ctx, "something edgy")
    assert ctx.voice.last == ai.BLOCKED
    assert ctx.state["ai_history"] == []


@pytest.mark.parametrize("status, body, expected", [
    (401, '{"error": {"status": "UNAUTHENTICATED", "details": [{"reason": "ACCESS_TOKEN_TYPE_UNSUPPORTED"}]}}',
     "My AI key was rejected"),
    (400, '{"error": {"details": [{"reason": "API_KEY_INVALID"}]}}', "My AI key was rejected"),
    (400, '{"error": {"message": "Invalid Auth key."}}', "My AI key was rejected"),
    (403, '{"error": {"status": "PERMISSION_DENIED"}}', "My AI key was rejected"),
    (429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}', "I've used up the free AI quota"),
    (500, "", "The AI service is having problems"),
])
def test_error_codes(gemini, status, body, expected):
    _, responses = gemini
    responses.append(Response(status, text=body))
    ctx = make_ctx()
    handle(ctx, "tell me something")
    assert ctx.voice.last.startswith(expected)
    assert KEY not in ctx.voice.last


MODELS = [
    {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.0-flash-preview", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
]


def test_pick_flash_model_prefers_newest_stable():
    assert ai.pick_flash_model(MODELS) == "gemini-2.5-flash"
    previews_only = [m for m in MODELS if "preview" in m["name"] or "lite" in m["name"]]
    assert ai.pick_flash_model(previews_only) == "gemini-3.0-flash-preview"
    assert ai.pick_flash_model([MODELS[-1]]) is None


def test_404_falls_back_to_newest_flash(gemini, monkeypatch, caplog):
    calls, responses = gemini
    monkeypatch.setattr(config, "AI_MODEL", "gemini-1.0-flash")
    listed = []

    def get_json(url, params=None, headers=None, timeout=None):
        listed.append((url, headers))
        return {"models": MODELS}

    monkeypatch.setattr(http, "get_json", get_json)
    responses += [Response(404, text="models/gemini-1.0-flash is not found"), Response(data=reply("Hello!"))]
    ctx = make_ctx()
    handle(ctx, "hi there friend")
    assert ctx.voice.last == "Hello!"
    assert listed == [("https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": KEY})]
    assert calls[1]["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert config.AI_MODEL == "gemini-2.5-flash"
    assert "switching to gemini-2.5-flash" in caplog.text


def test_404_with_no_better_model(gemini, monkeypatch):
    _, responses = gemini
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"models": []})
    responses.append(Response(404, text="not found"))
    ctx = make_ctx()
    handle(ctx, "hi there friend")
    assert ctx.voice.last.startswith("The AI model gemini-2.5-flash wasn't found.")


def test_gmail_summary_uses_gemini(gemini):
    from jarvis.skills import gmail

    calls, responses = gemini
    responses.append(Response(data=reply("Dhruv moved the demo to 5.")))
    assert gmail.summarize("Hi. The demo moved to 5 pm. Bring the laptop.") == "Dhruv moved the demo to 5."
    assert calls[0]["payload"]["systemInstruction"]["parts"][0]["text"].startswith("Summarise this email")
