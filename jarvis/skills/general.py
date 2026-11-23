"""Small talk, time/date, exit/sleep, help, calculator and jokes."""
import logging
import ast
import math
import operator
import random
import re
from datetime import datetime

import config
from jarvis.router import skill, speak_list
from jarvis.timeparse import NUMBER_WORDS

log = logging.getLogger(__name__)

PC_WORDS = re.compile(r"\b(?:laptop|pc|computer|system|windows)\b")


def time_of_day(now=None):
    hour = (now or datetime.now()).hour
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"


# ---------------------------------------------------------------- exit / sleep
@skill(
    "exit",
    r"^(?:please\s+)?(?:exit|quit|bye|bye bye|goodbye|good bye|see you|see you later|that's all|that is all)(?:\s+jarvis)?$",
    r"^(?:please\s+)?(?:turn off|shut ?down|stop|close|exit|quit|bye|goodbye)\s+(?:jarvis|yourself)$",
    r"^(?:बाय|बाय बाय|अलविदा)$",  # Hindi speech recognition writes "bye" in Devanagari
    category="general", help=["goodbye", "turn off Jarvis"],
)
def exit_skill(ctx, command, match):
    ctx.running = False
    return f"Goodbye, {config.USER_NAME}. Stay safe!"


@skill("sleep", r"\bgo to sleep\b", r"\bstop listening\b", r"\bsleep mode\b", r"\bpause listening\b", category='general', help=['go to sleep', 'stop listening'])
def sleep_skill(ctx, command, match):
    if PC_WORDS.search(command):
        return None  # "put the laptop to sleep" belongs to the system skill
    ctx.sleeping = True
    return f"Okay, going to sleep. Say {config.ASSISTANT_NAME} when you need me."


# ---------------------------------------------------------------- help
# Help categories: key -> (spoken title, words people might use for it).
# Every @skill sets category= to one of these keys and help= to example phrases.
CATEGORIES = {
    "air quality": ("air quality", {"air quality", "air", "aqi", "pollution", "pm2.5", "pm 2.5", "smog", "red zones",
                                    "mask", "pollutants"}),
    "time and weather": ("time and weather", {"time and weather", "time", "weather", "rain", "date", "forecast",
                                              "temperature"}),
    "apps": ("apps", {"apps", "app", "applications", "application", "websites", "website", "open apps"}),
    "laptop": ("laptop controls", {"laptop", "laptop controls", "controls", "computer", "pc", "system", "volume",
                                   "brightness", "battery", "media", "music", "screenshot", "shutdown", "shut down"}),
    "web": ("web", {"web", "internet", "youtube", "google", "news", "search", "browser"}),
    "notes": ("notes and reminders", {"notes and reminders", "notes", "note", "reminders", "reminder", "timers",
                                      "timer", "alarms"}),
    "whatsapp": ("WhatsApp", {"whatsapp", "whats app", "contacts", "messaging"}),
    "email": ("email", {"email", "emails", "e-mail", "e mail", "mail", "gmail", "inbox", "google", "drafts"}),
    "calendar": ("calendar", {"calendar", "google calendar", "meetings", "meeting", "events", "event", "schedule",
                              "agenda"}),
    "spotify": ("Spotify", {"spotify", "playlist", "playlists", "liked songs", "songs"}),
    "vs code": ("VS Code", {"vs code", "vscode", "visual studio code", "code", "coding", "projects", "project", "git",
                            "github"}),
    "general": ("general questions", {"general questions", "general", "questions", "maths", "math", "calculator",
                                      "jokes", "joke", "ai", "chat", "hindi", "language", "languages", "other"}),
}

SHORT_HELP = (
    "I can help with air quality, time and weather, apps, laptop controls, web, notes and reminders, "
    "WhatsApp, email, calendar, Spotify, VS Code, and general questions. Say 'help with' and a category to hear its commands."
)


# Housekeeping skills: listed in the printed help, but not worth speaking as examples.
NOT_SPOKEN = {"exit", "sleep", "help", "help category", "greet", "who are you", "how are you", "thanks",
              "forget conversation", "connect google", "disconnect google", "connect spotify"}


def examples_for(category, limit=None, spoken=False):
    """Example phrases for a category: each skill's first example, then their second ones, and so on."""
    from jarvis.router import skills_in

    lists = [s.examples for s in skills_in(category) if s.examples and not (spoken and s.name in NOT_SPOKEN)]
    ordered = [ex for depth in range(max(map(len, lists), default=0)) for exs in lists if depth < len(exs)
               for ex in [exs[depth]]]
    return ordered[:limit] if limit else ordered


def full_command_list():
    lines = ["", "=== What you can say (full list in COMMANDS.md) ==="]
    for key, (title, _) in CATEGORIES.items():
        lines.append(f"\n{title[:1].upper()}{title[1:]}:")
        lines.extend(f'  - "{example}"' for example in examples_for(key))
    return "\n".join(lines) + "\n"


def find_category(text):
    """Map 'whatsapp', 'the laptop', 'vs cod' ... to a category key, or None."""
    import difflib

    text = re.sub(r"[^\w\s.]", " ", (text or "").lower())
    text = re.sub(r"\b(?:the|my|your|a|commands?|skills?|features?|stuff|things|options|please|jarvis)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    alias_to_key = {alias: key for key, (_, aliases) in CATEGORIES.items() for alias in aliases}
    if text in alias_to_key:
        return alias_to_key[text]
    for alias in sorted(alias_to_key, key=len, reverse=True):  # "help me with my notes"
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text):
            return alias_to_key[alias]
    close = difflib.get_close_matches(text, list(alias_to_key), n=1, cutoff=0.75)
    return alias_to_key[close[0]] if close else None


@skill("help category", r"^(?:help|commands|options|examples)\s+(?:me\s+)?(?:with|on|for|about|in|of)?\s*(.+)$",
       r"\bwhat (?:all |else )?can (?:you|i) do (?:with|in|for|on|about)\s+(.+)$",
       r"\bhow (?:do|can) i use\s+(.+?)$", r"\bwhat (?:are the )?commands (?:for|of)\s+(.+)$",
       category="general", help=["help with laptop", "what can you do with VS Code"])
def help_category(ctx, command, match):
    if re.search(r"\b(?:write|draft|compose|send|make|create|plan|fix|solve|choose|pick|find)\b", match.group(1)):
        return None  # "help me write an email to my teacher" is a task for the AI, not the email commands
    key = find_category(match.group(1))
    if key is None:
        return None  # "help me write an email" goes to the AI
    title = CATEGORIES[key][0]
    examples = [e[:1].upper() + e[1:] for e in examples_for(key, spoken=True)]
    log.info(f"\n{title[:1].upper()}{title[1:]}:\n" + "\n".join(f'  - "{e}"' for e in examples_for(key)) + "\n")
    return speak_list(ctx, f"For {title}, you can say:", examples, join=". ".join, print_full=False)


@skill("help", r"^(?:help|help me|commands|options|menu)$", r"\bwhat (?:all |else )?can you do\b",
       r"\bwhat (?:are your|commands do you)\b", r"\b(?:list|show) (?:your )?(?:commands|features)\b",
       category="general", help=["help", "help with WhatsApp"])
def help_skill(ctx, command, match):
    log.info(full_command_list())
    return SHORT_HELP


# ---------------------------------------------------------------- speech language
LANGUAGE_WORDS = {
    "hindi": "hindi", "hinglish": "hindi", "हिंदी": "hindi", "हिन्दी": "hindi",
    "english": "english", "इंग्लिश": "english", "अंग्रेजी": "english", "अंग्रेज़ी": "english",
}
LANGUAGES = {"hindi": ("hi-IN", "Hindi"), "english": ("en-IN", "English")}
_LANG = "|".join(sorted(LANGUAGE_WORDS, key=len, reverse=True))


@skill("language",
       rf"\b(?:switch|change|set)\s+(?:the\s+)?(?:language\s+)?(?:back\s+)?(?:to|into)\s+({_LANG})",
       rf"^({_LANG})\s+(?:mode|please)$",
       rf"(?:स्विच|switch)\s*(?:टू|to)?\s*({_LANG})",  # Hindi recognition: "स्विच टू इंग्लिश"
       rf"({_LANG})\s*(?:में|मे)\s*(?:बात करो|बोलो|स्विच करो)",
       category="general", help=["switch to Hindi", "switch to English"])
def language_skill(ctx, command, match):
    key = LANGUAGE_WORDS[match.group(1)]
    code, label = LANGUAGES[key]
    current = getattr(ctx.voice, "language", None) or config.SPEECH_LANGUAGE
    if current == code:
        return f"I'm already listening in {label}."
    ctx.voice.language = code  # session only: .env is not changed
    ctx.state["speech_language"] = code
    if key == "hindi":
        return ("Okay, switched to Hindi. Ab aap Hindi ya Hinglish mein bol sakte hain. "
                "Say switch to English to go back.")
    return "Okay, switched back to English."


@skill("ai model", r"\b(?:which|what)\b.*\b(?:ai|llm|language)?\s*models?\b.*\b(?:using|use|running|are you)\b",
       r"\bwhat(?:'s| is) your (?:ai |llm )?model\b", r"\bwhich ai (?:are you|is this)\b",
       category="general", help=["which AI model are you using"])
def ai_model(ctx, command, match):
    from jarvis.skills import ai

    if not config.AI_API_KEY:
        return "No AI is set up yet. Add an AI_API_KEY to the .env file."
    return f"I'm using {ai.provider_name()}, with the model {ai.spoken_model()}."


# ---------------------------------------------------------------- small talk
@skill("greet", r"^(?:hi|hii|hello|hey|hey there|hi there|hello there|yo|hola|namaste|namaskar|"
                r"good (?:morning|afternoon|evening))(?:\s+jarvis)?$", category='general', help=['hello'])
def greet(ctx, command, match):
    return f"Good {time_of_day()}, {config.USER_NAME}. How can I help?"


@skill("who are you", r"\bwho are you\b", r"\bwhat(?:'s| is) your name\b", r"\bintroduce yourself\b", category='general', help=['who are you'])
def who_are_you(ctx, command, match):
    return (f"I'm {config.ASSISTANT_NAME}, your personal voice assistant. "
            "I can check air quality and weather, open apps, set reminders and much more. Say help to hear it all.")


@skill("how are you", r"\bhow are you\b", r"\bhow(?:'s| is) it going\b", r"\bhow do you do\b", category='general', help=['how are you'])
def how_are_you(ctx, command, match):
    return random.choice([
        f"I'm running smoothly, thanks for asking, {config.USER_NAME}. How about you?",
        "All systems online and feeling great. What can I do for you?",
    ])


@skill("thanks", r"^(?:thanks?|thank you|thank you so much|thanks a lot|thx|shukriya|dhanyavaad|dhanyawad)(?:\s+jarvis)?$", category='general', help=['thank you'])
def thanks(ctx, command, match):
    return random.choice(["You're welcome!", "Happy to help.", f"Any time, {config.USER_NAME}."])


@skill("time", r"\bwhat(?:'s| is)? (?:the )?time\b", r"\bwhat time\b", r"\btime (?:is it|now)\b", r"^(?:the )?time$",
       r"\bcurrent time\b", r"\btell me the time\b", category='time and weather', help=['what time is it'])
def time_skill(ctx, command, match):
    now = datetime.now()
    return f"It's {now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}."


@skill("date", r"\bwhat(?:'s| is)? (?:the |today's )?date\b", r"\bwhat day is (?:it|today)\b", r"\btoday's date\b",
       r"^(?:the )?date$", r"\bwhich day is (?:it|today)\b", category='time and weather', help=["what's the date today"])
def date_skill(ctx, command, match):
    now = datetime.now()
    return f"Today is {now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}."


# ---------------------------------------------------------------- calculator
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
MAX_EXPONENT = 100
MAX_RESULT = 1e100

_WORD_OPS = [
    (r"\bto the power of\b|\braised to(?: the power of)?\b|\bpower\b|\^", " ** "),
    (r"\bdivided by\b|\bdivide by\b|\bover\b|÷", " / "),
    (r"\bmultiplied by\b|\btimes\b|\binto\b|×|(?<=\d)\s*x\s*(?=\d)", " * "),
    (r"\bplus\b|\band\b", " + "),
    (r"\bminus\b|\bless\b", " - "),
    (r"\bmod(?:ulo)?\b", " % "),
]


def to_expression(text):
    """Convert spoken maths to a Python expression string, or None if it isn't maths."""
    t = text.lower().strip().rstrip("?.! ")
    t = re.sub(r"^(?:the\s+)?(?:value of|answer to|result of|sum of)\s+", "", t)
    for word, value in sorted(NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        if word not in ("a", "an"):
            t = re.sub(rf"\b{word}\b", str(value), t)
    t = t.replace(",", "")
    t = re.sub(r"(\d+(?:\.\d+)?)\s*(?:percent|per cent|%)\s*of\s*(\d+(?:\.\d+)?)", r"(\1 / 100 * \2)", t)
    t = re.sub(r"\b(?:the\s+)?square root of\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*squared\b", r"(\1 ** 2)", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*cubed\b", r"(\1 ** 3)", t)
    for pattern, symbol in _WORD_OPS:
        t = re.sub(pattern, symbol, t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t or not re.fullmatch(r"(?:sqrt|[\d\s.+\-*/%()])+", t):
        return None
    if not re.search(r"\d", t) or not re.search(r"[+\-*/%]|sqrt", t):
        return None
    return t


def safe_eval(expression):
    """Evaluate an arithmetic expression with ast (never eval). Raises ValueError on anything unsafe."""

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > MAX_EXPONENT or abs(left) > 1e6):
                raise ValueError("too big")
            result = _OPS[type(node.op)](left, right)
            if isinstance(result, complex) or abs(result) > MAX_RESULT:
                raise ValueError("too big")
            return result
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt"
                and len(node.args) == 1 and not node.keywords):
            value = ev(node.args[0])
            if value < 0:
                raise ValueError("negative root")
            return math.sqrt(value)
        raise ValueError("unsupported")

    return ev(ast.parse(expression, mode="eval"))


def format_number(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return f"{value:,}" if abs(value) < 10**15 else f"{value:.3e}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


@skill("calculator", r"^(?:what(?:'s| is)|calculate|compute|how much is|solve|evaluate|tell me)\s+(.+)$", category='general', help=['what is 25 times 4', 'what is 10 percent of 250', 'what is the square root of 144'])
def calculator(ctx, command, match):
    expression = to_expression(match.group(1))
    if expression is None:
        return None  # not maths: let Wikipedia / the AI answer "what is python"
    try:
        result = safe_eval(expression)
    except ZeroDivisionError:
        return "You can't divide by zero."
    except (ValueError, SyntaxError, OverflowError, TypeError) as exc:
        if "too big" in str(exc):
            return "That number is too big for me to calculate."
        if "negative root" in str(exc):
            return "I can't take the square root of a negative number."
        return None
    spoken = match.group(1).rstrip("?. ")
    return f"{spoken[:1].upper()}{spoken[1:]} is {format_number(result)}."


# ---------------------------------------------------------------- jokes
_BACKUP_JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break, and it said: no problem, I'll go to sleep.",
    "Why did the developer go broke? Because he used up all his cache.",
]


@skill("joke", r"\bjokes?\b", r"\bmake me laugh\b", r"\bsomething funny\b", category='general', help=['tell me a joke'])
def joke(ctx, command, match):
    try:
        import pyjokes

        return pyjokes.get_joke(language="en", category="neutral")
    except Exception:
        return random.choice(_BACKUP_JOKES)
