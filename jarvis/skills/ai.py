"""AI fallback: anything no other skill handled goes to Gemini (native API) or an OpenAI-compatible chat API.

If GEMINI_API_KEY is set and the main AI is Groq (or another OpenAI-compatible API), Gemini is the automatic
backup: see failover().
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import config
from jarvis import grammar, http, persona
from jarvis.router import fallback, on_startup, skill

log = logging.getLogger(__name__)

MAX_TURNS = 10  # chat memory, separate from the routing context (3 turns)
HISTORY_TOKENS = 600  # ...sent within about this many tokens; older turns become one summary line
CHAT_SKILLS = ("ai", "chat follow-up", "chat direct", "offer", "feeling")


def _tokens(text):
    return len(text) // 4 + 1  # rough: about 4 characters a token


def compact_history(history, budget=HISTORY_TOKENS):
    """(recent messages that fit in `budget` tokens, one-line summary of the older ones or "").

    The newest exchanges are sent whole; older ones are folded into "Earlier: you talked about X; Y", so a long
    chat doesn't eat Groq's per-minute token budget."""
    kept, used = [], 0
    pairs = [history[i:i + 2] for i in range(0, len(history), 2)]
    for pair in reversed(pairs):
        cost = sum(_tokens(m["content"]) for m in pair)
        if used + cost > budget and kept:
            break
        kept[:0] = pair
        used += cost
    older = history[:len(history) - len(kept)]
    topics = [" ".join(m["content"].split()[:8]) for m in older if m["role"] == "user"]
    summary = ("Earlier in this chat the user talked about: " + "; ".join(topics))[:300] if topics else ""
    return kept, summary


def system_prompt():
    length = ("" if persona.is_friend() else
              "Answer in 1 or 2 short sentences of plain speech, unless the user asks you to explain in detail. ")
    return (
        f"You are {config.ASSISTANT_NAME}, {persona.nickname()}'s voice assistant; {persona.nickname()} lives in "
        f"India. The current date and time is {datetime.now():%A %d %B %Y, %I:%M %p}. "
        "Your replies are spoken aloud: plain speech, never markdown, bullet points, code blocks, emojis or URLs. "
        + length + persona.ai_style() +
        "Follow-ups like 'aur?', 'kyun?', 'uska kya?' refer to the conversation so far. "
        "You can't browse here: for live facts (prices, news, scores) say you'd have to check online rather than "
        "guess."
    )


KEY_WORDS = ("api key", "auth key", "api_key_invalid", "access_token_type_unsupported", "unauthenticated")


def error_message(status, body=""):
    """A clear spoken message for an HTTP error from the AI service (in the current personality)."""
    body = (body or "").lower()
    if status in (401, 403) or (status == 400 and any(w in body for w in KEY_WORDS)):
        return "My AI key was rejected. Please check it in Settings, AI brain."
    if status == 404:
        return (f"The AI model {config.AI_MODEL} wasn't found. "
                "Please check the AI brain in Settings.")
    if status == 429:
        return persona.line("ai_busy")
    if status >= 500:
        return persona.line("ai_trouble")
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


def _gemini_headers(key=None):
    return {"x-goog-api-key": key or config.AI_API_KEY}  # works for both AQ. and AIza keys


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
                     r"vistaar se|detail mein|detail me|explain karo|explain kar|samjhao|samjha do|elaborate|"
                     r"tell me more|aur detail)\b")


def max_tokens_for(command):
    return DETAIL_TOKENS if _DETAIL.search(command or "") else SHORT_TOKENS


# ---------------------------------------------------------------- Gemini
def _gemini_url(stream=False, model=None):
    method = "streamGenerateContent?alt=sse" if stream else "generateContent"
    return f"{GEMINI_BASE}/models/{model or config.AI_MODEL}:{method}"


def _gemini_request(payload, stream=False, model=None, key=None):
    """model/key: the backup's (GEMINI_MODEL, GEMINI_API_KEY) instead of AI_MODEL, AI_API_KEY."""
    send = http.post_stream if stream else http.post_json
    return send(_gemini_url(stream, model), payload, headers=_gemini_headers(key), timeout=config.AI_TIMEOUT)


def _gemini_body(system, history, command, max_tokens, model=None):
    payload = gemini_payload(system, history, command)
    payload["generationConfig"]["maxOutputTokens"] = max_tokens
    if "2.5-flash" in (model or config.AI_MODEL):  # don't spend the short token budget on hidden "thinking"
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    return payload


def ask_gemini(system, history, command, max_tokens=SHORT_TOKENS, model=None, key=None):
    """Returns (status, reply_or_error_body)."""
    response = _gemini_request(_gemini_body(system, history, command, max_tokens, model), model=model, key=key)
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


def stream_gemini(system, history, command, max_tokens=SHORT_TOKENS, model=None, key=None):
    """Returns (status, text chunks) or (status, error body)."""
    response = _gemini_request(_gemini_body(system, history, command, max_tokens, model), stream=True,
                               model=model, key=key)
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


# ---------------------------------------------------------------- surviving model retirements
SWITCHED = "I switched to a newer AI model."
_RETIRED = re.compile(r"model_decommissioned|decommissioned|model_not_found|model[^\n]{0,80}(?:not found|does not "
                      r"exist|is not supported|no longer)|not found for api version", re.IGNORECASE)
_NOT_CHAT = re.compile(r"whisper|tts|orpheus|guard|embed|moderation|dall-e|image|audio|transcri|realtime|"
                       r"rerank|search|vision-only", re.IGNORECASE)
_switch_pending = False  # say SWITCHED once, with the next answer


def is_retired(status, body):
    """Does this error mean AI_MODEL no longer exists (rather than a bad key or a quota)?"""
    if status in (401, 403, 429) or status < 400:
        return False
    return status == 404 or bool(_RETIRED.search(body or ""))


def list_models():
    """Model names the provider offers for chat right now, in the provider's order."""
    if provider() == "gemini":
        return [m["name"].removeprefix("models/") for m in list_gemini_models()
                if "generateContent" in m.get("supportedGenerationMethods", [])]
    data = http.get_json(f"{config.AI_BASE_URL}/models", headers=_openai_headers(), timeout=6)
    return [m["id"] for m in data.get("data", []) if m.get("id") and m.get("active", True)]


def choose_replacement(available, current):
    """The first AI_MODEL_FALLBACKS entry on offer, else (Gemini) the newest Flash, else the first chat model."""
    offered = set(available)
    for name in config.AI_MODEL_FALLBACKS:
        if name in offered and name != current:
            return name
    if provider() == "gemini":
        flash = pick_flash_model([{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
                                  for n in available])
        if flash and flash != current:
            return flash
    return next((n for n in available if n != current and not _NOT_CHAT.search(n)), None)


def update_env_model(model, path=None):
    """Rewrite only the AI_MODEL line of .env (append it if missing). Other lines, including keys, are untouched."""
    path = path or config.ENV_FILE
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    line = re.compile(r"^(\s*AI_MODEL\s*=)[^\r\n]*", re.MULTILINE)
    if line.search(text):
        text = line.sub(lambda m: m.group(1) + model, text, count=1)
    else:
        newline = "\r\n" if "\r\n" in text else "\n"
        text += ("" if not text or text.endswith(("\n", "\r")) else newline) + f"AI_MODEL={model}{newline}"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def recover_model():
    """AI_MODEL was retired: pick a replacement, use it from now on, save it to .env. Returns it, or None."""
    global _switch_pending
    old = config.AI_MODEL
    try:
        replacement = choose_replacement(list_models(), old)
    except Exception as exc:
        log.warning("[ai] couldn't list the provider's models: %s", exc)
        return None
    if not replacement:
        log.warning("[ai] model %s is retired and no replacement is available", old)
        return None
    log.warning("[ai] model %s is retired or unavailable, switching to %s (saved to .env)", old, replacement)
    config.AI_MODEL = replacement
    try:
        from jarvis import settings_store

        has_settings, has_env = config.SETTINGS_FILE.exists(), config.ENV_FILE.exists()
        if has_settings or not has_env:
            settings_store.update({"AI_MODEL": replacement})  # settings.json wins over .env, so it goes there
        if has_env or not has_settings:
            update_env_model(replacement)  # a .env setup (or no settings at all): keep .env right too
    except OSError as exc:
        log.warning("[ai] couldn't save the new AI_MODEL: %s", exc)
    from jarvis import cache

    cache.put(_model_cache_key(), True)
    _switch_pending = True
    return replacement


def _with_recovery(ask, *args):
    status, result = ask(*args)
    if status >= 400 and is_retired(status, result if isinstance(result, str) else "") and recover_model():
        status, result = ask(*args)  # the same question once more, with the new model
    return status, result


# ---------------------------------------------------------------- the backup AI (Gemini behind Groq)
BACKUP_SWITCH = "Switching to my backup brain."
BOTH_DOWN = ("Sorry, my main AI and my backup AI are both unavailable right now. "
             "Please try again in a minute.")
BACKUP_UNTIL = "ai-backup-until"  # data/cache.json: after Groq's daily limit, Gemini goes first until then
IST = timezone(timedelta(hours=5, minutes=30))
_DAILY = re.compile(r"per day|\b(?:TPD|RPD)\b|daily", re.IGNORECASE)
_on_backup = False        # did the last answer come from the backup?
_backup_pending = False   # say BACKUP_SWITCH with the next answer


class BothFailed(Exception):
    """The main AI and the backup both failed."""


def has_backup():
    return bool(config.GEMINI_API_KEY) and provider() != "gemini"


def now():
    return time.time()


def next_midnight_ist(timestamp=None):
    moment = datetime.fromtimestamp(now() if timestamp is None else timestamp, IST)
    return (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def backup_until():
    """While Groq's daily limit is used up: the time (epoch seconds) to go back to Groq. Otherwise None."""
    from jarvis import cache

    until = cache.get(BACKUP_UNTIL, 2 * 24 * 3600)
    return until if until and now() < until else None


def is_daily_limit(status, body):
    return status == 429 and bool(_DAILY.search(body or ""))


def should_fail_over(status, body):
    """Rate limit / quota, server trouble, or a key or model problem: worth asking the other AI."""
    if status < 400:
        return False
    lowered = (body or "").lower()
    return (status in (401, 403, 404, 429) or status >= 500 or is_retired(status, body)
            or (status == 400 and any(w in lowered for w in KEY_WORDS)))


def main_label():
    return f"{provider_name()} ({config.AI_MODEL})"


def backup_label():
    return f"Gemini ({config.GEMINI_MODEL})"


def _answered_by(which):
    global _on_backup, _backup_pending
    if which == "backup" and not _on_backup:
        _on_backup, _backup_pending = True, True
        log.warning("[ai] switching to the backup AI, %s", backup_label())
    elif which == "main" and _on_backup:
        _on_backup = False
        log.warning("[ai] back on the main AI, %s", main_label())


def failover(main, backup, what="chat"):
    """Call main(); if it fails in a way the backup might not (rate limit, 5xx, timeout, key or model error),
    call backup() once. Each returns (status, result). After a daily-limit 429, backup() goes first until
    midnight IST (main() is still tried if the backup fails). Raises BothFailed if neither answers.
    """
    if not has_backup():
        return main()
    sticky = backup_until()
    order = [("backup", backup), ("main", main)] if sticky else [("main", main), ("backup", backup)]
    for which, call in order:
        label = backup_label() if which == "backup" else main_label()
        try:
            status, result = call()
        except Blocked:
            raise
        except Exception as exc:  # timeout, offline, odd response
            log.warning("[ai] %s %s failed: %s", label, what, exc)
            continue
        if status < 400:
            _answered_by(which)
            return status, result
        body = result if isinstance(result, str) else ""
        log.warning("[ai] %s %s: HTTP %s %s", label, what, status, body[:200])
        if which == "main" and not sticky and is_daily_limit(status, body):
            from jarvis import cache

            until = next_midnight_ist()
            cache.put(BACKUP_UNTIL, until)
            log.warning("[ai] %s's daily limit is used up: using the backup until %s IST",
                        provider_name(), datetime.fromtimestamp(until, IST).strftime("%d %b %H:%M"))
        if not should_fail_over(status, body):
            return status, result  # e.g. a malformed request: the other AI wouldn't do better
    raise BothFailed(what)


def _backup_args():
    return {"model": config.GEMINI_MODEL, "key": config.GEMINI_API_KEY}


def complete(system, history, command, max_tokens=SHORT_TOKENS):
    """Ask the configured AI. Returns (status, text): text is the reply, or the error body if status >= 400.

    Raises Blocked if Gemini refused to answer, BothFailed if the main AI and the backup both failed.
    A retired model is replaced automatically.
    """
    ask = ask_gemini if provider() == "gemini" else ask_openai
    return failover(lambda: _with_recovery(ask, system, history, command, max_tokens),
                    lambda: ask_gemini(system, history, command, max_tokens, **_backup_args()))


def stream(system, history, command, max_tokens=SHORT_TOKENS):
    """Like complete(), but the reply comes back as an iterator of text chunks as they're generated."""
    ask = stream_gemini if provider() == "gemini" else stream_openai
    return failover(lambda: _with_recovery(ask, system, history, command, max_tokens),
                    lambda: stream_gemini(system, history, command, max_tokens, **_backup_args()))


def _model_cache_key():
    return f"ai-model-ok|{provider()}|{config.AI_BASE_URL}|{config.AI_MODEL}"


MODEL_CHECK_SECONDS = 24 * 3600


def check_model():
    """Make sure AI_MODEL still exists (at most once a day); switch to a replacement if it doesn't.

    Returns True if the model is fine, the new model's name if it switched, or None if it couldn't check.
    """
    from jarvis import cache

    if not config.AI_API_KEY:
        return None
    if cache.get(_model_cache_key(), MODEL_CHECK_SECONDS):
        return True
    try:
        available = list_models()
    except Exception as exc:  # offline: try again next start
        log.debug("[ai] model check skipped: %s", exc)
        return None
    if config.AI_MODEL in available:
        cache.put(_model_cache_key(), True)
        return True
    return recover_model()


@on_startup
def _check_model_in_background(ctx):
    import threading

    if config.AI_API_KEY:
        threading.Thread(target=check_model, name="ai-model-check", daemon=True).start()


def provider_name():
    if provider() == "gemini":
        return "Google Gemini"
    host = re.sub(r"^https?://(?:api\.)?", "", config.AI_BASE_URL).split("/")[0]
    known = {"groq.com": "Groq", "openai.com": "OpenAI", "openrouter.ai": "OpenRouter", "mistral.ai": "Mistral",
             "together.xyz": "Together"}
    return next((label for domain, label in known.items() if host.endswith(domain)), host)


def spoken_model(model=None):
    """'qwen/qwen3.8-27b' -> 'qwen 3.8 27b' (easier to listen to)."""
    name = (model or config.AI_MODEL).split("/")[-1]
    return re.sub(r"(?<=[a-z])(?=\d)", " ", name.replace("-", " ")).strip()


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


def announce_switch(ctx):
    """Say "Switching to my backup brain." / "I switched to a newer AI model." once after a switch."""
    global _switch_pending, _backup_pending
    if _backup_pending:
        _backup_pending = False
        ctx.say(persona.line("backup_switch"), wait=False)
    if _switch_pending:
        _switch_pending = False
        ctx.say(SWITCHED, wait=False)


# Chat follow-ups go straight to the chat AI (with its 10-turn memory), skipping the routing call
_FOLLOW_UP = (r"^(?:aur|aur\?|aur batao|aur bata|aur bataiye|aur kuch|phir|phir kya|then|and then|and|and\?|kyun|kyu|"
              r"kyon|why|why not|how|kaise|how come|uska kya|uske baare mein|uske bare me|us ka kya|matlab|means|"
              r"really|sach mein|sach me|seriously|accha|achha|okay and|ok and|go on|continue)\??$",
              r"^(?:explain karo|explain kar|detail mein bata\w*|detail me bata\w*|samjhao|samjha do|elaborate|"
              r"explain (?:it |that )?(?:in detail|more)|tell me more|aur detail mein)\??$")


# Obvious chat skips the routing call too (it would only answer with "reply"): opinions, advice, explanations,
# how you feel, health worries, "tu kaisa hai". Greetings, jokes and simple feelings have their own skills.
_DIRECT = (r"\b(?:tu|tum|aap)\s+kya\s+soch\w*", r"\bwhat do you think\b", r"\byour (?:opinion|take|view)\b",
           r"^(?:should i|kya main|kya mujhe|mujhe kya karna)\b", r"^(?:explain|samjhao|samjha do)\b",
           r"\b(?:explain karo|explain kar do)\b", r"\b(?:advice|tips? for|suggest(?:ion)?s? for|kya karun|kya karoon)\b",
           r"^(?:tu|tum|aap)\s+(?:kaisa|kaisi|kaise)\b", r"^(?:kya haal|kya chal raha|what'?s up|sup)\b",
           r"\b(?:neend nahi|can'?t sleep|cannot sleep|insomnia|sar ?dard|headache|dard ho|pain|bukhar|fever|"
           r"khansi|cough|feel(?:ing)? (?:really )?(?:low|sick|unwell|anxious|lonely)|akel[ai] (?:feel|lag)|"
           r"mann nahi lag)\b")


@skill("chat direct", *_DIRECT, category="general", help=["tu kya sochta hai", "explain recursion simply",
                                                           "mujhe neend nahi aa rahi"])
def chat_direct(ctx, command, match):
    """Obvious chat goes straight to the chat AI: one AI call instead of two, and the full good-friend rules."""
    if not config.AI_API_KEY:
        return None
    return ai(ctx, command)


@skill("chat follow-up", *_FOLLOW_UP, category="general", help=["aur?", "kyun?", "detail mein bata"])
def chat_follow_up(ctx, command, match):
    """"aur?", "kyun?", "detail mein bata" right after a chat answer: the chat AI, which remembers 10 turns."""
    turns = ctx.state.get("turns") or []
    if not ctx.state.get("ai_history") or not turns or turns[-1]["skill"] not in CHAT_SKILLS:
        return None  # nothing to follow up on: routing decides
    return ai(ctx, command)


def _no_ai_key(ctx, command):
    """No AI key: still not a dead end. Look it up on the web (read out from the search results)."""
    from jarvis.skills import browse

    answer = browse.look_up(ctx, command, question=command)
    if answer and persona.is_line("nothing_found", answer):
        answer += " Add a free AI key in Settings, AI brain, and I can answer anything."
    return answer


@fallback
def ai(ctx, command, match=None):
    if not config.AI_API_KEY:
        return _no_ai_key(ctx, command)
    if ctx.state.pop("ai_down", None) == command:
        return persona.line("ai_down")  # the AI router just tried both for this command: don't make the user wait
    history = ctx.state.setdefault("ai_history", [])
    recent, summary = compact_history(history)
    from jarvis.skills import memory

    system = system_prompt() + memory.context_for(command) + (f" {summary}." if summary else "")
    from jarvis import timing

    timing.add_tokens("chat", _tokens(system) + sum(_tokens(m["content"]) for m in recent) + _tokens(command), True)
    spoken = []
    try:
        status, chunks = stream(system, recent, command, max_tokens_for(command))
        if status >= 400:
            log.warning("[ai] HTTP %s: %s", status, chunks[:300])
            if is_retired(status, chunks):
                return (f"The AI model {spoken_model()} has been retired, and I couldn't find a replacement. "
                        "Please pick a current model in Settings.")
            return error_message(status, chunks)
        announce_switch(ctx)
        for sentence in sentences(chunks):
            text = grammar.fix(strip_markdown(sentence))  # "tu kaisi hai", "main kar dunga"
            if not text:
                continue
            if getattr(ctx.voice, "_hushed", False):
                break  # barge-in: stop reading the rest
            ctx.say(text, wait=False)  # speak this sentence while the next one is still being generated
            spoken.append(text)
    except BothFailed:
        return persona.line("ai_down")
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
        return persona.line("no_reply")
    reply = " ".join(spoken)
    history.extend([{"role": "user", "content": command}, {"role": "assistant", "content": reply}])
    del history[:-MAX_TURNS * 2]
    return ""
