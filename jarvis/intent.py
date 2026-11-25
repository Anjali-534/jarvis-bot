"""Natural-language actions: when no regex skill matches, let the AI pick one of the registered skills.

Every skill registered with params= becomes a tool (OpenAI-compatible "tools" for Groq/OpenAI,
functionDeclarations for Gemini). If the AI calls a tool, its arguments are turned back into a canonical
English command ("remind me tomorrow at 9 am to go to the gym") that runs through the normal skill, so all
existing confirmations still apply. The AI can only reach registered skills: the canonical command must be
handled by the tool's own skill (or the few listed in also=), and risky tools always ask first.

Anything that isn't a skill is never a dead end. The AI must call one tool:
  a) current or real-world facts                 -> web_search (answered from the sources)
  b) knowledge, advice, opinions, maths, chat    -> reply (spoken directly: one AI call, not two)
  c) an action Jarvis can't do                   -> cant_do: says so casually and offers the closest thing it
                                                    CAN do; "haan" runs it (only safe skills: open, search...)
  d) too unclear to act on                       -> clarify: ONE short question, like a friend would ask
The last 3 turns are sent along so follow-ups work: "what about Noida?", "and tomorrow?". Chat follow-ups
("aur?", "kyun?") go straight to the chat AI with its own 10-turn memory (skills/ai.py).

Two-stage routing keeps each call small (Groq's free tier allows 7,000 input tokens a minute): a local
pre-filter (keywords, Hinglish synonyms, fuzzy matching against each skill's name, examples and parameters)
picks the 5 likeliest tools, and only those go to the AI, with web_search, the previous turn's tool and the
reply / cant_do / clarify tools. About 800 input tokens per routed question instead of 2,500.
"""
import difflib
import json
import logging
import re

import config
from jarvis import http, persona, timing

log = logging.getLogger(__name__)

# Phrases that point back at the conversation ("what about Noida?", "make it 5 minutes instead")
FOLLOW_UP = re.compile(r"\b(?:what about|how about|and (?:for|in|on)?|instead|same|again|also|too|that|this|it|"
                       r"him|her|them|there|wahan|waha|bhi|wala|wali)\b|^and\b")
TYPES = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}
MAX_ARG = 200

RULES = (
    "Call exactly ONE tool.\n"
    "1) Actions: that tool, arguments in plain English ('kal subah 9 baje' = 'tomorrow at 9 am'); follow-ups "
    "('what about Noida?', 'make it 5 minutes instead') reuse the last command with the change; music: no platform "
    "unless Spotify is named; reminder/note text in short English, but WhatsApp/email texts in the user's own words.\n"
    "2) Never state live data yourself. Use web_search for current events, prices, scores, people, places, "
    "'is X still...' and anything after your training data.\n"
    "3) Knowledge, advice, opinions, maths, coding, chat: reply (spoken, no markdown).\n"
    "4) An action no tool does (order food, book a cab): reply kind=cant_do, offering the closest thing as "
    "alternative ('open swiggy', 'search the web for ...').\n"
    "5) Too unclear: reply kind=clarify, ONE short question ('Kaunsa match? India wala?')."
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
# One small tool covers answers, "can't do" offers and clarifying questions: every tool definition costs ~115
# input tokens on Groq, and this one goes with every routed question.
REPLY_SCHEMA = {"type": "object", "properties": {
    "text": {"type": "string", "description": "what to say"},
    "kind": {"type": "string", "enum": ["answer", "cant_do", "clarify"]},
    "alternative": {"type": "string", "description": "cant_do: command to offer, e.g. open swiggy"}},
    "required": ["text"]}
CANT_DO_TOOL, CLARIFY_TOOL = "cant_do", "clarify"  # older names, still understood
EXTRA_TOOLS = [(REPLY_TOOL, "speak: answer, cant_do or clarify", REPLY_SCHEMA)]


# ---------------------------------------------------------------- stage 1: the local pre-filter
SHORTLIST = 3           # at most this many skill tools go to the AI (plus web_search and the previous turn's)
KEEP_RATIO = 0.5        # ...and only those scoring at least half as well as the best match
ALWAYS = ("web_search",)
# Hinglish and everyday words -> words found in the skills' names, examples and parameters
SYNONYMS = {
    "awaaz": "volume", "aawaz": "volume", "awaz": "volume", "sound": "volume", "loud": "volume", "louder": "volume up",
    "quiet": "volume down", "dheere": "volume down", "badha": "up increase", "badhao": "up increase",
    "kam": "down decrease", "ghata": "down", "ghatao": "down", "mute": "mute volume",
    "gaana": "play song music", "gaane": "play song music", "song": "play music", "songs": "play music",
    "bajao": "play", "chalao": "play open", "sunao": "play", "music": "play", "playlist": "playlist spotify",
    "kholo": "open", "khol": "open", "launch": "open", "band": "close shut", "bandh": "close shut",
    "yaad": "remind reminder", "dila": "remind", "dilana": "remind", "remind": "reminder", "alarm": "reminder timer",
    "baje": "at", "subah": "morning am", "shaam": "evening pm", "kal": "tomorrow",
    "mausam": "weather", "barish": "rain weather", "baarish": "rain weather", "garmi": "weather temperature",
    "thand": "weather temperature", "temperature": "weather", "hawa": "air quality aqi", "pradushan": "pollution aqi",
    "pollution": "aqi air quality", "smog": "aqi", "mask": "aqi",
    "message": "whatsapp message send", "msg": "message whatsapp", "bhejo": "send message", "bhej": "send message",
    "mail": "email", "emails": "email", "inbox": "email",
    "meeting": "calendar meeting schedule", "meetings": "calendar", "event": "calendar", "schedule": "calendar",
    "likh": "note write", "likho": "note write", "note": "note", "notes": "note",
    "roshni": "brightness", "screen": "brightness screenshot", "battery": "battery",
    "shutdown": "shut down shutdown laptop", "restart": "restart laptop", "lock": "lock laptop",
    "agla": "next", "agle": "next", "pichla": "previous", "ruko": "pause", "roko": "pause",
    "project": "project code", "repo": "project pull", "git": "pull project", "code": "code project",
}
_STOP = {"the", "a", "an", "to", "for", "of", "in", "on", "at", "and", "or", "is", "it", "me", "my", "i", "you", "please",
         "can", "could", "do", "kar", "karo", "kardo", "de", "do", "na", "yaar", "thoda", "zara", "ka", "ki", "ke", "ko",
         "hai", "se", "mein", "par", "pe", "jarvis", "what", "whats", "e", "g", "with", "about", "this", "that"}
_index = {}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 1]


def _doc_words(sk):
    """Words describing a skill tool: its names, examples and parameter descriptions (and its also= skills')."""
    from jarvis import router

    parts = [sk.name, sk.tool or "", *sk.examples]
    for key, spec in (sk.params or {}).items():
        parts += [key.rstrip("?"), " ".join(spec) if isinstance(spec, (list, tuple)) else str(spec)]
    for other in router.SKILLS:
        if other.name in sk.also:
            parts += [other.name, *other.examples]
    return set(_words(" ".join(parts)))


def _tool_index():
    """{tool name: words}, and how many tools each word appears in (rare words count more)."""
    key = tuple(name for name, *_ in tools())
    if _index.get("key") != key:
        docs = {name: _doc_words(sk) for name, _, _, sk in tools()}
        frequency = {}
        for words in docs.values():
            for w in words:
                frequency[w] = frequency.get(w, 0) + 1
        _index.update(key=key, docs=docs, frequency=frequency, vocabulary=sorted(frequency))
    return _index


def shortlist(ctx, command, limit=SHORTLIST):
    """Stage 1: the tool names most likely to be needed for `command`, scored locally (no AI)."""
    index = _tool_index()
    said = set(_words(command))
    for w in list(said):
        said.update(_words(SYNONYMS.get(w, "")))
    fuzzy = {}
    for w in said:
        if len(w) >= 4 and w not in index["frequency"]:
            for close in difflib.get_close_matches(w, index["vocabulary"], n=2, cutoff=0.84):
                fuzzy[close] = 0.6  # "reminde", "whatsap", "brightnes"
    scores = {}
    for name, words in index["docs"].items():
        score = sum(1.0 / index["frequency"][w] for w in said & words)
        score += sum(weight / index["frequency"][w] for w, weight in fuzzy.items() if w in words)
        if score > 0:
            scores[name] = score
    ranked = sorted(scores, key=lambda n: -scores[n])
    picked = [n for n in ranked[:limit] if scores[n] >= KEEP_RATIO * scores[ranked[0]]]
    previous = _previous_tool(ctx)
    for name in (*ALWAYS, previous):
        if name and name not in picked and name in index["docs"]:
            picked.append(name)
    return picked


def _previous_tool(ctx):
    """The tool behind the last turn, so "make it 5 minutes instead" can reuse it."""
    turns = ctx.state.get("turns") or [] if ctx is not None else []
    if not turns:
        return None
    last = turns[-1]["skill"]
    for name, _, _, sk in tools():
        if last == sk.name or last in sk.also:
            return name
    return None


def openai_tools(names=None):
    skills = [{"type": "function", "function": {"name": n, "description": d, "parameters": s}}
              for n, d, s, _ in tools() if names is None or n in names]
    return skills + [{"type": "function", "function": {"name": n, "description": d, "parameters": s}}
                     for n, d, s in EXTRA_TOOLS]


def _decision(name, args):
    args = args if isinstance(args, dict) else {}
    if name == REPLY_TOOL:
        text = str(args.get("text", "")).strip()
        kind = str(args.get("kind") or "answer").lower()
        if kind == "cant_do":
            return ("offer", text, str(args.get("alternative", "")).strip())
        if kind == "clarify" and text:
            return ("clarify", text)
        return ("text", text) if text else None
    if name == CANT_DO_TOOL:
        return ("offer", str(args.get("text", "")).strip(), str(args.get("alternative", "")).strip())
    if name == CLARIFY_TOOL:
        question = str(args.get("question", "")).strip()
        return ("clarify", question) if question else None
    return ("tool", name, args if isinstance(args, dict) else {})


def _gemini_schema(value):
    if isinstance(value, dict):
        return {k: (v.upper() if k == "type" and isinstance(v, str) else _gemini_schema(v)) for k, v in value.items()
                if not (k == "required" and not v)}
    if isinstance(value, list):
        return [_gemini_schema(v) for v in value]
    return value


def gemini_tools(names=None):
    declarations = []
    for name, desc, params, _ in tools():
        if names is not None and name not in names:
            continue
        decl = {"name": name, "description": desc}
        if params["properties"]:
            decl["parameters"] = _gemini_schema(params)
        declarations.append(decl)
    for name, desc, params in EXTRA_TOOLS:
        declarations.append({"name": name, "description": desc, "parameters": _gemini_schema(params)})
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
        if turn["skill"] in CHAT_SKILLS:
            lines.append(f"User: {turn['you']}")
            if turn.get("jarvis"):
                lines.append(f"You replied: {turn['jarvis'][:120]}")
        else:
            lines.append(f"User: {turn['you']}  ->  ran command: {turn['understood']}")
    return "\n".join(lines)


CHAT_SKILLS = {"ai", "chat follow-up", "chat direct", "offer", "feeling"}


def system_text(ctx):
    """A short prompt on purpose: it's sent with the tool list on every routed command."""
    from datetime import datetime

    recent = _context(ctx)
    me = (f"You are {config.ASSISTANT_NAME}, voice assistant on {config.USER_NAME}'s laptop in India. "
          f"Now: {datetime.now():%a %d %b %Y %I:%M %p}. ")
    return (me + RULES + "\nStyle for reply: " + persona.ai_style(brief=True)
            + (f"\n\nRecent conversation:\n{recent}" if recent else ""))


def router_model():
    return config.AI_ROUTER_MODEL or config.AI_MODEL


def count_tokens(kind, reported, payload):
    """Log the request's input tokens (the provider's count, or an estimate) and add them to --timing."""
    estimated = not reported
    tokens = reported or len(json.dumps(payload, ensure_ascii=False)) // 4
    timing.add_tokens(kind, tokens, estimated)
    log.debug("[tokens] %s input: %s%d", kind, "~" if estimated else "", tokens)
    return tokens


def ask_openai(ctx, command):
    """Returns (status, decision or None) or (status, error body)."""
    names = shortlist(ctx, command)
    body = {
        "model": router_model(),
        "messages": [{"role": "system", "content": system_text(ctx)}, {"role": "user", "content": command}],
        "tools": openai_tools(names), "tool_choice": "required", "temperature": 0, "max_tokens": 200,
    }
    if config.AI_REASONING_EFFORT:
        body["reasoning_effort"] = config.AI_REASONING_EFFORT
    response = http.post_json(f"{config.AI_BASE_URL}/chat/completions", body,
                              headers={"Authorization": f"Bearer {config.AI_API_KEY}"}, timeout=config.AI_ROUTER_TIMEOUT)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    data = response.json()
    count_tokens("routing", (data.get("usage") or {}).get("prompt_tokens"), body)
    log.info("[intent] tools offered: %s", ", ".join(names))
    message = data["choices"][0]["message"]
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
        "tools": gemini_tools(shortlist(ctx, command)),
        "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},  # must call a tool (a skill, or "reply")
        "generationConfig": {"temperature": 0, "maxOutputTokens": 200},
    }
    url = f"{ai.GEMINI_BASE}/models/{model or router_model()}:generateContent"
    response = http.post_json(url, body, headers=ai._gemini_headers(key), timeout=timeout or config.AI_ROUTER_TIMEOUT)
    if response.status_code >= 400:
        return response.status_code, getattr(response, "text", "") or ""
    data = response.json()
    count_tokens("routing", (data.get("usageMetadata") or {}).get("promptTokenCount"), body)
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
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
    if decision[0] in ("text", "clarify"):
        return _answer(ctx, command, decision[1])
    if decision[0] == "offer":
        return _offer(ctx, command, decision[1], decision[2])
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
        if not ctx.confirm(persona.line("confirm_action", action=phrase)):
            ctx.say(persona.line("not_done"))
            return target.name
    result = router._run(ctx, target, phrase, match)
    if result is None:
        return None
    return router._deliver(ctx, target, result)


# Alternatives the AI may offer for "can't do": only harmless skills (open a site or app, search, play music).
SAFE_ALTERNATIVES = {"open", "web search", "web question", "google in browser", "youtube search", "play youtube",
                     "spotify play", "news", "search more"}


def safe_alternative(command):
    """The command if a harmless skill would run it, else None."""
    from jarvis import router

    command = re.sub(r"\s+", " ", (command or "").strip().lower()).strip(" .?!")
    if not command:
        return None
    for sk in router.SKILLS:
        if any(p.search(command) for p in sk.patterns):
            return command if sk.name in SAFE_ALTERNATIVES else None
    return None


def _offer(ctx, command, text, alternative):
    """c) Can't do it: say so casually, offer the closest thing; "haan" runs it (router.handle)."""
    alternative = safe_alternative(alternative)
    if not alternative:  # the AI offered nothing usable: never a dead end, offer Google
        topic = re.sub(r"^(?:please\s+)?(?:can you\s+|could you\s+)?", "", command).strip(" ?.!")
        alternative = safe_alternative(f"show me {topic} on google")
        if text and alternative:
            text = text.rstrip() + " " + persona.line("offer_google")
    if not text:
        text = persona.line("cant_do", alternative=alternative or "Google pe dhoondh")
    handled = _answer(ctx, command, text)
    if alternative:
        ctx.state["offer"] = alternative
        log.info("(offered: %s)", alternative)
    return "offer" if handled else None


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
