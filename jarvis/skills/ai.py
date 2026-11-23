"""AI fallback: anything no other skill handled goes to Gemini (native API) or an OpenAI-compatible chat API."""
import logging
import re
from datetime import datetime

import config
from jarvis import http
from jarvis.router import fallback, skill

log = logging.getLogger(__name__)

MAX_TURNS = 8


def system_prompt():
    return (
        f"You are {config.ASSISTANT_NAME}, a friendly voice assistant for {config.USER_NAME}, who lives in India. "
        f"The current date and time is {datetime.now():%A %d %B %Y, %I:%M %p}. "
        "Your replies are spoken aloud, so answer in 1 or 2 short sentences of plain speech, unless the user asks "
        "you to explain in detail. "
        "Never use markdown, bullet points, code blocks, emojis or URLs. "
        "Language: if the user writes in Hindi or Hinglish (in Devanagari or Roman letters), reply in Hinglish "
        "written ONLY in Roman (English) letters, for example 'Haan bilkul, aap mujhse Hindi mein baat kar sakte hain.' "
        "Never use Devanagari script, because the text-to-speech voice cannot read it. "
        "Otherwise reply in English."
    )


def error_message(status, body=""):
    """A clear spoken message for an HTTP error from the AI service."""
    body = (body or "").lower()
    key_words = ("api key", "auth key", "api_key_invalid", "access_token_type_unsupported", "unauthenticated")
    if status in (401, 403) or (status == 400 and any(w in body for w in key_words)):
        return "My AI key was rejected. Please check AI_API_KEY in your .env file."
    if status == 404:
        return (f"The AI model {config.AI_MODEL} wasn't found. "
                "Please check AI_MODEL and AI_BASE_URL in your .env file.")
    if status == 429:
        return ("I've used up the free AI quota or hit the rate limit. Try again in a minute, "
                "or tomorrow if the daily limit is used up.")
    if status >= 500:
        return "The AI service is having problems right now. Please try again in a bit."
    return f"The AI service returned an error, code {status}."


def strip_markdown(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__|\*|_|~~)(.+?)\1", r"\2", text)
    return re.sub(r"\s+", " ", text).strip()


@skill("forget conversation", r"\bforget (?:our|the|this|my) (?:conversation|chat)\b",
       r"\b(?:clear|reset) (?:the |our )?(?:chat|conversation|history)\b", r"\bstart (?:a )?new (?:chat|conversation)\b", category='general', help=['forget our conversation'])
def forget(ctx, command, match):
    ctx.state["ai_history"] = []
    return "Okay, I've forgotten our conversation."


# ---------------------------------------------------------------- providers
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
BLOCKED = "Sorry, I can't help with that one. Let's try something else."


class Blocked(Exception):
    """Gemini refused or returned nothing (safety filter, recitation...)."""


def provider():
    if config.AI_PROVIDER in ("gemini", "openai"):
        return config.AI_PROVIDER
    return "gemini" if "generativelanguage.googleapis.com" in config.AI_BASE_URL else "openai"


def _gemini_headers():
    return {"x-goog-api-key": config.AI_API_KEY}  # works for both AQ. and AIza keys


def gemini_payload(system, history, command):
    contents = [{"role": "model" if turn["role"] == "assistant" else "user", "parts": [{"text": turn["content"]}]}
                for turn in history]
    contents.append({"role": "user", "parts": [{"text": command}]})
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 300},
    }


def gemini_text(data):
    """The reply text from a generateContent response. Raises Blocked if Gemini refused."""
    if (data.get("promptFeedback") or {}).get("blockReason"):
        raise Blocked(data["promptFeedback"]["blockReason"])
    candidates = data.get("candidates") or []
    if not candidates:
        raise Blocked("no candidates")
    candidate = candidates[0]
    text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", []))
    if not text.strip() and candidate.get("finishReason") in ("SAFETY", "RECITATION", "BLOCKLIST",
                                                               "PROHIBITED_CONTENT", "SPII"):
        raise Blocked(candidate["finishReason"])
    return text


def _flash_rank(name):
    """Sort key for 'models/gemini-2.5-flash': stable before preview, newest version first, no lite/tts/image."""
    match = re.fullmatch(r"models/gemini-(\d+(?:\.\d+)?)-flash(-[\w.-]+)?", name)
    if not match:
        return None
    suffix = match.group(2) or ""
    if re.search(r"lite|tts|image|audio|live|thinking|exp|8b", suffix):
        return None
    return (0 if not suffix or re.fullmatch(r"-\d{3}", suffix) else 1, -float(match.group(1)), suffix)


def pick_flash_model(models):
    """The newest current gemini-*-flash model that supports generateContent, e.g. 'gemini-2.5-flash'."""
    ranked = sorted((_flash_rank(m["name"]), m["name"]) for m in models
                    if "generateContent" in m.get("supportedGenerationMethods", []) and _flash_rank(m["name"]))
    return ranked[0][1].removeprefix("models/") if ranked else None


def list_gemini_models():
    data = http.get_json(f"{GEMINI_BASE}/models", params={"pageSize": 1000}, headers=_gemini_headers(), timeout=6)
    return data.get("models", [])


# ---------------------------------------------------------------- reply length
SHORT_TOKENS = 150
DETAIL_TOKENS = 600
_DETAIL = re.compile(r"\b(?:in detail|in depth|detailed|step by step|explain (?:it )?(?:fully|properly|everything)|"
                     r"vistaar se|detail mein|detail me)\b")


def max_tokens_for(command):
    return DETAIL_TOKENS if _DETAIL.search(command or "") else SHORT_TOKENS


# ---------------------------------------------------------------- Gemini
def _gemini_url(stream=False):
    method = "streamGenerateContent?alt=sse" if stream else "generateContent"
    return f"{GEMINI_BASE}/models/{config.AI_MODEL}:{method}"


def _gemini_request(payload, stream=False):
    """POST to Gemini; on 404 (model retired) switch to the newest Flash model once and retry."""
    send = http.post_stream if stream else http.post_json
    response = send(_gemini_url(stream), payload, headers=_gemini_headers(), timeout=config.AI_TIMEOUT)
    if response.status_code == 404:
        try:
            newest = pick_flash_model(list_gemini_models())
        except Exception as exc:
            log.warning("[ai] couldn't list Gemini models: %s", exc)
            newest = None
        if newest and newest != config.AI_MODEL:
            log.warning("[ai] model %s not found, switching to %s (set AI_MODEL=%s in .env)",
                        config.AI_MODEL, newest, newest)
            config.AI_MODEL = newest
            response = send(_gemini_url(stream), payload, headers=_gemini_headers(), timeout=config.AI_TIMEOUT)
    return response


def _gemini_body(system, history, command, max_tokens):
    payload = gemini_payload(system, history, command)
    payload["generationConfig"]["maxOutputTokens"] = max_tokens
    if "2.5-flash" in config.AI_MODEL:  # don't spend the short token budget on hidden "thinking"
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    return payload


def ask_gemini(system, history, command, max_tokens=SHORT_TOKENS):
    """Returns (status, reply_or_error_body)."""
    response = _gemini_request(_gemini_body(system, history, command, max_tokens))
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    return 200, gemini_text(response.json())


def _sse_events(response):
    """Parsed JSON objects from a server-sent-events response ('data: {...}' lines)."""
    import json

    response.encoding = "utf-8"
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


def stream_gemini(system, history, command, max_tokens=SHORT_TOKENS):
    """Returns (status, text chunks) or (status, error body)."""
    response = _gemini_request(_gemini_body(system, history, command, max_tokens), stream=True)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""

    def chunks():
        got_text, finish = False, None
        for event in _sse_events(response):
            if (event.get("promptFeedback") or {}).get("blockReason"):
                raise Blocked(event["promptFeedback"]["blockReason"])
            for candidate in event.get("candidates") or []:
                finish = candidate.get("finishReason") or finish
                for part in (candidate.get("content") or {}).get("parts", []):
                    if part.get("text"):
                        got_text = True
                        yield part["text"]
        if not got_text:
            raise Blocked(finish or "no candidates")

    return 200, chunks()


# ---------------------------------------------------------------- OpenAI-compatible (Groq, OpenAI...)
def _openai_body(system, history, command, max_tokens, stream=False):
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": command}]
    body = {"model": config.AI_MODEL, "messages": messages, "temperature": 0.6, "max_tokens": max_tokens}
    if config.AI_REASONING_EFFORT:  # reasoning models (Groq gpt-oss / qwen3): keep thinking short or off
        body["reasoning_effort"] = config.AI_REASONING_EFFORT
    if stream:
        body["stream"] = True
    return body


def _openai_headers():
    return {"Authorization": f"Bearer {config.AI_API_KEY}"}


def ask_openai(system, history, command, max_tokens=SHORT_TOKENS):
    response = http.post_json(f"{config.AI_BASE_URL}/chat/completions",
                              _openai_body(system, history, command, max_tokens),
                              headers=_openai_headers(), timeout=config.AI_TIMEOUT)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    return 200, response.json()["choices"][0]["message"]["content"] or ""


def stream_openai(system, history, command, max_tokens=SHORT_TOKENS):
    response = http.post_stream(f"{config.AI_BASE_URL}/chat/completions",
                                _openai_body(system, history, command, max_tokens, stream=True),
                                headers=_openai_headers(), timeout=config.AI_TIMEOUT)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""

    def chunks():
        for event in _sse_events(response):
            for choice in event.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece

    return 200, chunks()


def complete(system, history, command, max_tokens=SHORT_TOKENS):
    """Ask the configured AI. Returns (status, text): text is the reply, or the error body if status >= 400.

    Raises Blocked if Gemini refused to answer.
    """
    return (ask_gemini if provider() == "gemini" else ask_openai)(system, history, command, max_tokens)


def stream(system, history, command, max_tokens=SHORT_TOKENS):
    """Like complete(), but the reply comes back as an iterator of text chunks as they're generated."""
    return (stream_gemini if provider() == "gemini" else stream_openai)(system, history, command, max_tokens)


# ---------------------------------------------------------------- speaking as it streams
_SENTENCE_END = re.compile(r"(?<=[.!?।])[\"')\]]*\s+|\n+")
MIN_SENTENCE = 12  # don't speak tiny fragments like "Dr." on their own


def sentences(chunks, min_length=MIN_SENTENCE):
    """Group streamed text chunks into sentences (split on . ! ? and new lines) as soon as each one ends."""
    buffer, search_from = "", 0
    for piece in chunks:
        buffer += piece
        while True:
            end = _SENTENCE_END.search(buffer, search_from)
            if not end:
                break
            head = buffer[:end.start()].strip()
            if len(head) < min_length:
                search_from = end.end()
                continue
            yield head
            buffer, search_from = buffer[end.end():], 0
    if buffer.strip():
        yield buffer.strip()


@fallback
def ai(ctx, command, match=None):
    if not config.AI_API_KEY:
        return ("I don't know how to answer that yet. "
                "To let me answer anything, add an AI_API_KEY to the .env file.")
    history = ctx.state.setdefault("ai_history", [])
    spoken = []
    try:
        status, chunks = stream(system_prompt(), history, command, max_tokens_for(command))
        if status >= 400:
            log.warning("[ai] HTTP %s: %s", status, chunks[:300])
            return error_message(status, chunks)
        for sentence in sentences(chunks):
            text = strip_markdown(sentence)
            if not text:
                continue
            if getattr(ctx.voice, "_hushed", False):
                break  # barge-in: stop reading the rest
            ctx.say(text, wait=False)  # speak this sentence while the next one is still being generated
            spoken.append(text)
    except Blocked as exc:
        log.info("[ai] response blocked: %s", exc)
        if not spoken:
            return BLOCKED
    except Exception as exc:
        if not spoken:
            raise  # nothing said yet: the router turns network errors into a spoken message
        log.warning("[ai] stream broke off: %s", exc)
    wait = getattr(ctx.voice, "wait_idle", None)
    if wait:
        wait()  # finish speaking before listening again
    if not spoken:
        return "I'm not sure what to say to that."
    reply = " ".join(spoken)
    history.extend([{"role": "user", "content": command}, {"role": "assistant", "content": reply}])
    del history[:-MAX_TURNS * 2]
    return ""
