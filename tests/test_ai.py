"""AI tests (HTTP mocked): Gemini + OpenAI-compatible request shapes, streaming speech, short replies, errors."""
import pytest
from helpers import FakeVoice, StreamResponse, gemini_stream, make_ctx, openai_stream

import config
from jarvis import http
from jarvis.router import Context, handle
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
    """Gemini provider. Push responses (StreamResponse for ai(), Response for complete()) onto the list."""
    monkeypatch.setattr(config, "AI_API_KEY", KEY)
    monkeypatch.setattr(config, "AI_PROVIDER", "")
    monkeypatch.setattr(config, "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setattr(config, "AI_MODEL", "gemini-2.5-flash")
    calls, responses = [], []

    def post(url, payload, headers=None, timeout=30):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(http, "post_stream", post)
    monkeypatch.setattr(http, "post_json", post)
    return calls, responses


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "gsk-test")
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(config, "AI_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(config, "AI_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(config, "AI_REASONING_EFFORT", "low")
    calls, responses = [], []

    def post(url, payload, headers=None, timeout=30):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(http, "post_stream", post)
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


# ---------------------------------------------------------------- short replies
def test_system_prompt_asks_for_short_answers():
    prompt = ai.system_prompt()
    assert "1 or 2 short sentences" in prompt and "explain in detail" in prompt


@pytest.mark.parametrize("command, tokens", [
    ("tum kaise ho", 150), ("what is photosynthesis", 150),
    ("explain photosynthesis in detail", 600), ("explain it step by step", 600), ("detail mein batao", 600),
])
def test_max_tokens(command, tokens):
    assert ai.max_tokens_for(command) == tokens


# ---------------------------------------------------------------- sentence splitting
@pytest.mark.parametrize("chunks, expected", [
    (["Main theek hoon. Aap kai", "se ho?"], ["Main theek hoon.", "Aap kaise ho?"]),
    (["Dilli ki thand mein chai,\nSardi ka mausam ", "dil ko sukoon de."], ["Dilli ki thand mein chai,",
                                                                         "Sardi ka mausam dil ko sukoon de."]),
    (["It costs 3.5 rupees. Dr. Rao said ", "hi there to everyone."], ["It costs 3.5 rupees.",
                                                                     "Dr. Rao said hi there to everyone."]),
    (["Yes! ", "Sure. That works fine for me."], ["Yes! Sure. That works fine for me."]),  # tiny bits merge
    (["no ending punctuation"], ["no ending punctuation"]),
])
def test_sentences(chunks, expected):
    assert list(ai.sentences(chunks)) == expected


# ---------------------------------------------------------------- OpenAI-compatible (Groq)
def test_groq_request_shape_streaming(groq):
    calls, responses = groq
    responses.append(openai_stream("Main theek hoon, shukriya. Aap sunao, kya chal raha hai?"))
    ctx = make_ctx()
    handle(ctx, "tum kaise ho")
    call = calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer gsk-test"} and call["timeout"] == 15
    body = call["payload"]
    assert body["stream"] is True and body["max_tokens"] == 150 and body["reasoning_effort"] == "low"
    assert body["model"] == "openai/gpt-oss-120b" and body["messages"][-1] == {"role": "user", "content": "tum kaise ho"}
    assert ctx.voice.said == ["Main theek hoon, shukriya.", "Aap sunao, kya chal raha hai?"]
    assert ctx.state["ai_history"][-1]["content"] == "Main theek hoon, shukriya. Aap sunao, kya chal raha hai?"


def test_no_reasoning_param_unless_configured(groq, monkeypatch):
    calls, responses = groq
    monkeypatch.setattr(config, "AI_REASONING_EFFORT", "")
    responses.append(openai_stream("Hello there, friend."))
    handle(make_ctx(), "say hello")
    assert "reasoning_effort" not in calls[0]["payload"]


def test_first_sentence_is_spoken_while_the_rest_is_generating(groq, monkeypatch):
    calls, responses = groq
    ctx = make_ctx()
    seen_before_second = []

    class SlowStream(StreamResponse):
        def iter_lines(self, decode_unicode=False):
            yield 'data: {"choices": [{"delta": {"content": "Delhi winters are cold and foggy. "}}]}'
            seen_before_second.extend(ctx.voice.said)  # the stream is paused here, mid-generation
            yield 'data: {"choices": [{"delta": {"content": "Have some hot chai!"}}]}'
            yield "data: [DONE]"

    responses.append(SlowStream())
    handle(ctx, "tell me about delhi winters")
    assert seen_before_second == ["Delhi winters are cold and foggy."]
    assert ctx.voice.said == ["Delhi winters are cold and foggy.", "Have some hot chai!"]


def test_waits_for_speech_to_finish(groq):
    calls, responses = groq
    waited = []

    class Voice(FakeVoice):
        def wait_idle(self):
            waited.append(list(self.said))

    responses.append(openai_stream("One. Two sentences here."))
    ctx = Context(Voice())
    handle(ctx, "count please")
    assert waited == [["One. Two sentences here."]]


def test_barge_in_stops_reading_the_stream(groq):
    calls, responses = groq

    class Voice(FakeVoice):
        _hushed = False

        def speak(self, text, wait=True):
            super().speak(text, wait)
            self._hushed = True  # the hotkey was pressed during the first sentence

    responses.append(openai_stream("First sentence is here. Second one is here. Third one too."))
    ctx = Context(Voice())
    handle(ctx, "talk a lot")
    assert ctx.voice.said == ["First sentence is here."]


def test_stream_breaking_after_speech_keeps_what_was_said(groq):
    calls, responses = groq

    class Broken(StreamResponse):
        def iter_lines(self, decode_unicode=False):
            yield 'data: {"choices": [{"delta": {"content": "Here is the first part. And"}}]}'
            raise ConnectionError("connection reset")

    responses.append(Broken())
    ctx = make_ctx()
    handle(ctx, "tell me a story")
    assert ctx.voice.said == ["Here is the first part."]  # the unfinished fragment is dropped


def test_stream_failing_before_speech_gives_network_message(groq):
    import requests

    calls, responses = groq

    class Broken(StreamResponse):
        def iter_lines(self, decode_unicode=False):
            raise requests.ConnectionError("offline")
            yield  # pragma: no cover

    responses.append(Broken())
    ctx = make_ctx()
    handle(ctx, "tell me a story")
    assert "couldn't reach the online service" in ctx.voice.last


def test_empty_stream(groq):
    calls, responses = groq
    responses.append(StreamResponse(["[DONE]"]))
    ctx = make_ctx()
    handle(ctx, "hmm what")
    assert ctx.voice.last == "I'm not sure what to say to that."


# ---------------------------------------------------------------- Gemini
def test_gemini_request_shape_and_history(gemini):
    calls, responses = gemini
    responses += [gemini_stream("Haan, main Hindi samajhta hoon."), gemini_stream("**Sure.** Ask me anything.")]
    ctx = make_ctx()
    handle(ctx, "do you know hindi")
    assert ctx.voice.said == ["Haan, main Hindi samajhta hoon."]
    first = calls[0]
    assert first["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:"
                            "streamGenerateContent?alt=sse")
    assert first["headers"] == {"x-goog-api-key": KEY}  # header only: no Bearer, no ?key=
    assert "key=" not in first["url"] and first["timeout"] == 15
    body = first["payload"]
    assert "Hinglish" in body["systemInstruction"]["parts"][0]["text"]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "do you know hindi"}]}]
    assert body["generationConfig"] == {"temperature": 0.6, "maxOutputTokens": 150,
                                        "thinkingConfig": {"thinkingBudget": 0}}

    handle(ctx, "tell me more")
    assert [c["role"] for c in calls[1]["payload"]["contents"]] == ["user", "model", "user"]
    assert calls[1]["payload"]["contents"][1]["parts"][0]["text"] == "Haan, main Hindi samajhta hoon."
    assert ctx.voice.said[-1] == "Sure. Ask me anything."


@pytest.mark.parametrize("events", [
    [{"promptFeedback": {"blockReason": "SAFETY"}}],
    [{"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}],
    [],
])
def test_gemini_blocked_responses(gemini, events):
    _, responses = gemini
    responses.append(StreamResponse(events))
    ctx = make_ctx()
    handle(ctx, "something edgy")
    assert ctx.voice.last == ai.BLOCKED
    assert ctx.state["ai_history"] == []


@pytest.mark.parametrize("data", [
    {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
    {"candidates": []},
])
def test_gemini_text_blocked_non_streaming(data):
    with pytest.raises(ai.Blocked):
        ai.gemini_text(data)


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
    responses.append(StreamResponse(status_code=status, text=body))
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
    responses += [StreamResponse(status_code=404, text="models/gemini-1.0-flash is not found"),
                  gemini_stream("Hello there, friend!")]
    ctx = make_ctx()
    handle(ctx, "hi there friend")
    assert ctx.voice.last == "Hello there, friend!"
    assert listed == [("https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": KEY})]
    assert "/models/gemini-2.5-flash:streamGenerateContent" in calls[1]["url"]
    assert config.AI_MODEL == "gemini-2.5-flash"
    assert "switching to gemini-2.5-flash" in caplog.text


def test_404_with_no_better_model(gemini, monkeypatch):
    _, responses = gemini
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"models": []})
    responses.append(StreamResponse(status_code=404, text="not found"))
    ctx = make_ctx()
    handle(ctx, "hi there friend")
    assert ctx.voice.last.startswith("The AI model gemini-2.5-flash wasn't found.")


def test_gmail_summary_uses_short_non_streaming_call(gemini):
    from jarvis.skills import gmail

    calls, responses = gemini
    responses.append(Response(data=reply("Dhruv moved the demo to 5.")))
    assert gmail.summarize("Hi. The demo moved to 5 pm. Bring the laptop.") == "Dhruv moved the demo to 5."
    assert calls[0]["url"].endswith(":generateContent")
    assert calls[0]["payload"]["systemInstruction"]["parts"][0]["text"].startswith("Summarise this email")
    assert calls[0]["payload"]["generationConfig"]["maxOutputTokens"] == 150
