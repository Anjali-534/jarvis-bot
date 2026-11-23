"""Natural-language actions: when no regex skill matches, let the AI pick one of the registered skills.

Every skill registered with params= becomes a tool (OpenAI-compatible "tools" for Groq/OpenAI,
functionDeclarations for Gemini). If the AI calls a tool, its arguments are turned back into a canonical
English command ("remind me tomorrow at 9 am to go to the gym") that runs through the normal skill, so all
existing confirmations still apply. The AI can only reach registered skills: the canonical command must be
handled by the tool's own skill (or the few listed in also=), and risky tools always ask first.

If the AI answers instead of calling a tool, that answer is spoken directly (one AI call, not two).
The last 3 turns are sent along so follow-ups work: "what about Noida?", "and tomorrow?".
"""
import json
import logging
import re

import config
from jarvis import http

log = logging.getLogger(__name__)

# Phrases that point back at the conversation ("what about Noida?", "make it 5 minutes instead")
FOLLOW_UP = re.compile(r"\b(?:what about|how about|and (?:for|in|on)?|instead|same|again|also|too|that|this|it|"
                       r"him|her|them|there|wahan|waha|bhi|wala|wali)\b|^and\b")
TYPES = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}
MAX_ARG = 200

RULES = (
    "If the user's message asks for something a tool does (volume, music, apps, reminders, timers, notes, weather, "
    "air quality, WhatsApp, email, calendar, laptop power), call exactly ONE tool. Tool arguments are plain English: "
    "translate Hindi/Hinglish ('kal subah 9 baje' = 'tomorrow at 9 am'). Reminder times: 'in 10 minutes', "
    "'at 6 pm', 'tomorrow at 9 am'. Follow-ups ('what about Noida?', 'and tomorrow?', 'make it 5 minutes instead', "
    "'send the same to Riya') reuse the previous turn's command with the changed argument. "
    "NEVER state live data yourself (weather, temperature, air quality, news, calendar, email): always call the tool. "
    "Use web_search for current events, prices, scores, match results, 'latest' or 'today' questions, who holds a "
    "role now, and anything after your training data; not for maths, jokes, chat or settled facts. "
    "Don't ask which app to use for music: leave platform out unless the user names Spotify. Keep the user's own "
    "words for message texts. "
    "Otherwise (chat, questions, facts) call the reply tool with 1 or 2 short spoken sentences, no "
    "markdown; if they wrote Hindi or Hinglish, reply in Hinglish using only Roman letters."
)


# ---------------------------------------------------------------- tools from the skill registry
def _skills():
    from jarvis import router

    return [sk for sk in router.SKILLS if sk.params is not None]


def tool_name(sk):
    return re.sub(r"[^a-z0-9_]+", "_", (sk.tool or sk.name).lower()).strip("_")


def schema(params):
    """{"place?": "city", "day?": ["today", "tomorrow"], "amount?": ("integer", "percent")} -> JSON schema."""
    properties, required = {}, []
    for raw, spec in (params or {}).items():
        name = raw.rstrip("?")
        if isinstance(spec, (list, tuple)) and spec and spec[0] in TYPES and len(spec) == 2 and \
                not isinstance(spec, list):
            prop = {"type": TYPES[spec[0]], "description": spec[1]}
        elif isinstance(spec, list):
            prop = {"type": "string", "enum": list(spec)}
        else:
            prop = {"type": "string", "description": str(spec)}
        properties[name] = prop
        if not raw.endswith("?"):
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def description(sk):
    """Kept short on purpose: the tool list is sent with every routed command (free tiers count tokens)."""
    text = f'e.g. "{sk.examples[0]}"' if sk.examples else sk.name
    return text + (" (asks first)" if sk.risky else "")


def tools():
    """[(name, description, JSON schema, Skill)] for every actionable skill."""
    seen, result = set(), []
    for sk in _skills():
        name = tool_name(sk)
        if name in seen:
            continue
        seen.add(name)
        result.append((name, description(sk), schema(sk.params), sk))
    return result


# The model must always call a tool: a skill, or "reply" for chat. Forcing an explicit choice stops it from
# answering live-data questions from memory ("Noida ka AQI abhi 142 hai") instead of calling air_quality.
REPLY_TOOL = "reply"
REPLY_SCHEMA = {"type": "object", "properties": {"text": {
    "type": "string", "description": "your short spoken answer, for chat, questions and facts (never live data)"}},
    "required": ["text"]}


def openai_tools():
    skills = [{"type": "function", "function": {"name": n, "description": d, "parameters": s}} for n, d, s, _ in tools()]
    return skills + [{"type": "function", "function": {
        "name": REPLY_TOOL, "description": "answer the user without doing anything", "parameters": REPLY_SCHEMA}}]


def _decision(name, args):
    if name == REPLY_TOOL:
        text = str((args or {}).get("text", "")).strip()
        return ("text", text) if text else None
    return ("tool", name, args if isinstance(args, dict) else {})


def _gemini_schema(value):
    if isinstance(value, dict):
        return {k: (v.upper() if k == "type" and isinstance(v, str) else _gemini_schema(v)) for k, v in value.items()
                if not (k == "required" and not v)}
    if isinstance(value, list):
        return [_gemini_schema(v) for v in value]
    return value


def gemini_tools():
    declarations = []
    for name, desc, params, _ in tools():
        decl = {"name": name, "description": desc}
        if params["properties"]:
            decl["parameters"] = _gemini_schema(params)
        declarations.append(decl)
    declarations.append({"name": REPLY_TOOL, "description": "answer the user without doing anything",
                         "parameters": _gemini_schema(REPLY_SCHEMA)})
    return [{"functionDeclarations": declarations}]


# ---------------------------------------------------------------- arguments -> canonical command
_SEGMENT = re.compile(r"\[([^\[\]]*)\]")


def clean_args(args, params):
    """Keep only declared arguments, as short single-line strings (or ints)."""
    allowed = {k.rstrip("?") for k in (params or {})}
    cleaned = {}
    for key, value in (args or {}).items():
        if key not in allowed or value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "yes" if value else ""
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int):
            value = re.sub(r"\s+", " ", str(value)).strip()[:MAX_ARG]
        if value != "":
            cleaned[key] = value
    return cleaned


def fill(template, args):
    """'weather[ in {place}][ {day}]' -> 'weather in noida tomorrow'. A [segment] is kept only if all its
    fields were given; a missing required field (outside brackets) gives None."""
    def segment(match):
        fields = re.findall(r"\{(\w+)\}", match.group(1))
        return match.group(1).format(**args) if all(f in args for f in fields) else ""

    text = _SEGMENT.sub(segment, template)
    try:
        return text.format(**args)
    except KeyError:
        return None


def canonical(sk, args):
    args = clean_args(args, sk.params)
    command = sk.command(args) if callable(sk.command) else fill(sk.command or sk.name, args)
    return re.sub(r"\s+", " ", command).strip().lower() if command else None


def target_for(tool_skill, command):
    """The skill (tool_skill itself, or one of its also=) that will handle command, with the regex match."""
    from jarvis import router

    allowed = {tool_skill.name, *tool_skill.also}
    for sk in router.SKILLS:
        if sk.name not in allowed:
            continue
        match = next((m for m in (p.search(command) for p in sk.patterns) if m), None)
        if match:
            return sk, match
    return None, None


# ---------------------------------------------------------------- asking the AI
def _context(ctx):
    """The last 3 turns. Action turns show which command ran, NOT Jarvis's reply text: otherwise the model
    copies old answers and invents live data ("the AQI in Gurgaon is 291") instead of calling the tool."""
    lines = []
    for turn in ctx.state.get("turns", [])[-3:]:
        if turn["skill"] == "ai":
            lines.append(f"User: {turn['you']}")
            if turn.get("jarvis"):
                lines.append(f"You replied: {turn['jarvis'][:120]}")
        else:
            lines.append(f"User: {turn['you']}  ->  ran command: {turn['understood']}")
    return "\n".join(lines)


def system_text(ctx):
    """A short prompt on purpose: it's sent with the tool list on every routed command."""
    from datetime import datetime

    recent = _context(ctx)
    persona = (f"You are {config.ASSISTANT_NAME}, the voice assistant on {config.USER_NAME}'s Windows laptop in India. "
               f"Now: {datetime.now():%A %d %B %Y, %I:%M %p}. ")
    return persona + RULES + (f"\n\nRecent conversation:\n{recent}" if recent else "")


def router_model():
    return config.AI_ROUTER_MODEL or config.AI_MODEL


def ask_openai(ctx, command):
    """Returns (status, decision or None) or (status, error body)."""
    body = {
        "model": router_model(),
        "messages": [{"role": "system", "content": system_text(ctx)}, {"role": "user", "content": command}],
        "tools": openai_tools(), "tool_choice": "required", "temperature": 0, "max_tokens": 200,
    }
    if config.AI_REASONING_EFFORT:
        body["reasoning_effort"] = config.AI_REASONING_EFFORT
    response = http.post_json(f"{config.AI_BASE_URL}/chat/completions", body,
                              headers={"Authorization": f"Bearer {config.AI_API_KEY}"}, timeout=config.AI_ROUTER_TIMEOUT)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    message = response.json()["choices"][0]["message"]
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            args = json.loads(function.get("arguments") or "{}")
        except ValueError:
            args = {}
        return 200, _decision(function.get("name", ""), args)
    text = (message.get("content") or "").strip()
    return 200, (("text", text) if text else None)


def ask_gemini(ctx, command, model=None, key=None, timeout=None):
    """Returns (status, decision or None) or (status, error body). model/key/timeout: the backup's."""
    from jarvis.skills import ai

    body = {
        "systemInstruction": {"parts": [{"text": system_text(ctx)}]},
        "contents": [{"role": "user", "parts": [{"text": command}]}],
        "tools": gemini_tools(),
        "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},  # must call a tool (a skill, or "reply")
        "generationConfig": {"temperature": 0, "maxOutputTokens": 200},
    }
    url = f"{ai.GEMINI_BASE}/models/{model or router_model()}:generateContent"
    response = http.post_json(url, body, headers=ai._gemini_headers(key), timeout=timeout or config.AI_ROUTER_TIMEOUT)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    parts = (((response.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    for part in parts:
        if part.get("functionCall"):
            call = part["functionCall"]
            return 200, _decision(call.get("name", ""), call.get("args") or {})
    text = "".join(p.get("text", "") for p in parts).strip()
    return 200, (("text", text) if text else None)


def ask_backup(ctx, command):
    """Pick the action with the backup AI (Gemini): slower, so a longer timeout and "One sec." after 1.5 s."""
    from jarvis import router

    with router.one_sec(ctx):
        return ask_gemini(ctx, command, model=config.GEMINI_MODEL, key=config.GEMINI_API_KEY,
                          timeout=config.GEMINI_ROUTER_TIMEOUT)


def decide(ctx, command):
    """('tool', name, args), ('text', answer) or None (couldn't decide: fall back to normal chat)."""
    if not (config.AI_ROUTING and config.AI_API_KEY):
        return None
    key = command if not FOLLOW_UP.search(command) else None  # follow-ups depend on context: never cached
    cache = ctx.state.setdefault("intent_cache", {})
    if key in cache:
        return cache[key]
    from jarvis.skills import ai

    ask = ask_gemini if ai.provider() == "gemini" else ask_openai
    try:
        status, decision = ai.failover(lambda: ask(ctx, command), lambda: ask_backup(ctx, command), "routing")
    except ai.BothFailed:
        ctx.state["ai_down"] = command  # the chat fallback says so at once instead of trying both again
        return None
    except Exception as exc:  # timeout (4 s), offline, odd response: plain chat will handle it
        log.info("[intent] AI router unavailable: %s", exc)
        return None
    if status >= 400:
        log.info("[intent] AI router HTTP %s: %s", status, (decision or "")[:200])
        return None
    if decision and decision[0] == "tool" and key is not None:
        cache[key] = decision  # only actions are cached; chat answers should stay fresh
    return decision


# ---------------------------------------------------------------- running the decision
def resolve(ctx, command):
    """Handle a command no regex skill matched. Returns the handling skill's name, or None for plain chat."""
    decision = decide(ctx, command)
    if decision is None:
        return None
    if decision[0] == "text":
        return _answer(ctx, command, decision[1])
    _, name, args = decision
    sk = next((t[3] for t in tools() if t[0] == name), None)
    if sk is None:
        log.warning("[intent] AI picked an unknown tool %r; ignoring it", name)
        return None
    phrase = canonical(sk, args)
    target, match = target_for(sk, phrase) if phrase else (None, None)
    if target is None:
        log.warning("[intent] %s%s didn't produce a command its skill accepts (%r); ignoring it", name, args, phrase)
        return None
    log.info(f"(understood as: {phrase})")
    ctx.state["understood"] = phrase
    from jarvis.skills import ai

    ai.announce_switch(ctx)
    from jarvis import router

    if (sk.risky or target.risky) and not (target.confirms):
        if not ctx.confirm(f"Just to check: {phrase}. Shall I go ahead?"):
            ctx.say("Okay, I didn't do it.")
            return target.name
    result = router._run(ctx, target, phrase, match)
    if result is None:
        return None
    return router._deliver(ctx, target, result)


def _answer(ctx, command, text):
    """The AI answered instead of picking a tool: speak it as the chat reply."""
    from jarvis import router
    from jarvis.skills import ai

    reply = ai.strip_markdown(text)
    if not reply:
        return None
    ai.announce_switch(ctx)
    for sentence in ai.sentences([reply]):
        ctx.say(sentence, wait=False)
    wait = getattr(ctx.voice, "wait_idle", None)
    if wait:
        wait()
    history = ctx.state.setdefault("ai_history", [])
    history.extend([{"role": "user", "content": command}, {"role": "assistant", "content": reply}])
    del history[:-ai.MAX_TURNS * 2]
    ctx.last_skill = "ai"
    ctx.state["last_said"] = reply
    return router.FALLBACK.name if router.FALLBACK else "ai"
