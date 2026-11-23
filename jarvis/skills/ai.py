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
        "Your replies are spoken aloud, so answer in 1 to 3 short sentences of plain speech. "
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


def _post_gemini(payload):
    return http.post_json(f"{GEMINI_BASE}/models/{config.AI_MODEL}:generateContent", payload,
                          headers=_gemini_headers(), timeout=config.AI_TIMEOUT)


def ask_gemini(system, history, command):
    """Returns (status, reply_or_error_body)."""
    payload = gemini_payload(system, history, command)
    response = _post_gemini(payload)
    if response.status_code == 404:  # the model was retired: switch to the newest Flash model
        try:
            newest = pick_flash_model(list_gemini_models())
        except Exception as exc:
            log.warning("[ai] couldn't list Gemini models: %s", exc)
            newest = None
        if newest and newest != config.AI_MODEL:
            log.warning("[ai] model %s not found, switching to %s (set AI_MODEL=%s in .env)",
                        config.AI_MODEL, newest, newest)
            config.AI_MODEL = newest
            response = _post_gemini(payload)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    return 200, gemini_text(response.json())


def ask_openai(system, history, command):
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": command}]
    response = http.post_json(
        f"{config.AI_BASE_URL}/chat/completions",
        {"model": config.AI_MODEL, "messages": messages, "temperature": 0.6, "max_tokens": 300},
        headers={"Authorization": f"Bearer {config.AI_API_KEY}"}, timeout=config.AI_TIMEOUT,
    )
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    return 200, response.json()["choices"][0]["message"]["content"] or ""


def complete(system, history, command):
    """Ask the configured AI. Returns (status, text): text is the reply, or the error body if status >= 400.

    Raises Blocked if Gemini refused to answer.
    """
    return (ask_gemini if provider() == "gemini" else ask_openai)(system, history, command)


@fallback
def ai(ctx, command, match=None):
    if not config.AI_API_KEY:
        return ("I don't know how to answer that yet. "
                "To let me answer anything, add an AI_API_KEY to the .env file.")
    history = ctx.state.setdefault("ai_history", [])
    try:
        status, text = complete(system_prompt(), history, command)
    except Blocked as exc:
        log.info("[ai] response blocked: %s", exc)
        return BLOCKED
    if status >= 400:
        log.warning("[ai] HTTP %s: %s", status, text[:300])
        return error_message(status, text)
    reply = strip_markdown(text)
    if not reply:
        return "I'm not sure what to say to that."
    history.extend([{"role": "user", "content": command}, {"role": "assistant", "content": reply}])
    del history[:-MAX_TURNS * 2]
    return reply
