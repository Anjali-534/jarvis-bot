"""Small talk, time/date, exit/sleep, help, calculator and jokes."""
import ast
import math
import operator
import random
import re
from datetime import datetime

import config
from jarvis.router import skill
from jarvis.timeparse import NUMBER_WORDS

PC_WORDS = re.compile(r"\b(?:laptop|pc|computer|system|windows)\b")


def time_of_day(now=None):
    hour = (now or datetime.now()).hour
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"


# ---------------------------------------------------------------- exit / sleep
@skill(
    "exit",
    r"^(?:please\s+)?(?:exit|quit|bye|bye bye|goodbye|good bye|see you|see you later|that's all|that is all)(?:\s+jarvis)?$",
    r"^(?:please\s+)?(?:turn off|shut ?down|stop|close|exit|quit|bye|goodbye)\s+(?:jarvis|yourself)$",
    help="say goodbye or exit to close me",
)
def exit_skill(ctx, command, match):
    ctx.running = False
    return f"Goodbye, {config.USER_NAME}. Stay safe!"


@skill("sleep", r"\bgo to sleep\b", r"\bstop listening\b", r"\bsleep mode\b", r"\bpause listening\b")
def sleep_skill(ctx, command, match):
    if PC_WORDS.search(command):
        return None  # "put the laptop to sleep" belongs to the system skill
    ctx.sleeping = True
    return f"Okay, going to sleep. Say {config.ASSISTANT_NAME} when you need me."


# ---------------------------------------------------------------- help
HELP_TEXT = (
    "Here's what I can do. Air quality: ask for the AQI, PM2.5, PM10 or NO2 anywhere in India, "
    "ask if it's safe to go out, or ask for today's red zones. Tell me your area or pincode and I'll remember it. "
    "Weather and rain forecasts. Open or close apps and websites, play songs on YouTube, search Google, read the news. "
    "Send WhatsApp messages to your saved contacts. Open your projects in VS Code, check their git status, "
    "pull the latest changes, or run them. "
    "Take notes, set reminders and timers. Control volume, brightness, media, screenshots, battery, lock, "
    "sleep, restart or shut down the laptop. Do maths, tell jokes, the time and date, "
    "and answer general questions. Say go to sleep to pause me, or goodbye to exit."
)


@skill("help", r"^(?:help|help me|commands|options|menu)$", r"\bwhat (?:all |else )?can you do\b",
       r"\bwhat (?:are your|commands do you)\b", r"\b(?:list|show) (?:your )?(?:commands|features)\b")
def help_skill(ctx, command, match):
    return HELP_TEXT


# ---------------------------------------------------------------- small talk
@skill("greet", r"^(?:hi|hii|hello|hey|hey there|hi there|hello there|yo|hola|namaste|namaskar|"
                r"good (?:morning|afternoon|evening))(?:\s+jarvis)?$")
def greet(ctx, command, match):
    return f"Good {time_of_day()}, {config.USER_NAME}. How can I help?"


@skill("who are you", r"\bwho are you\b", r"\bwhat(?:'s| is) your name\b", r"\bintroduce yourself\b")
def who_are_you(ctx, command, match):
    return (f"I'm {config.ASSISTANT_NAME}, your personal voice assistant. "
            "I can check air quality and weather, open apps, set reminders and much more. Say help to hear it all.")


@skill("how are you", r"\bhow are you\b", r"\bhow(?:'s| is) it going\b", r"\bhow do you do\b")
def how_are_you(ctx, command, match):
    return random.choice([
        f"I'm running smoothly, thanks for asking, {config.USER_NAME}. How about you?",
        "All systems online and feeling great. What can I do for you?",
    ])


@skill("thanks", r"^(?:thanks?|thank you|thank you so much|thanks a lot|thx|shukriya|dhanyavaad|dhanyawad)(?:\s+jarvis)?$")
def thanks(ctx, command, match):
    return random.choice(["You're welcome!", "Happy to help.", f"Any time, {config.USER_NAME}."])


@skill("time", r"\bwhat(?:'s| is)? (?:the )?time\b", r"\bwhat time\b", r"\btime (?:is it|now)\b", r"^(?:the )?time$",
       r"\bcurrent time\b", r"\btell me the time\b")
def time_skill(ctx, command, match):
    now = datetime.now()
    return f"It's {now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}."


@skill("date", r"\bwhat(?:'s| is)? (?:the |today's )?date\b", r"\bwhat day is (?:it|today)\b", r"\btoday's date\b",
       r"^(?:the )?date$", r"\bwhich day is (?:it|today)\b")
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


@skill("calculator", r"^(?:what(?:'s| is)|calculate|compute|how much is|solve|evaluate|tell me)\s+(.+)$",
       help="what is 25 times 4")
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


@skill("joke", r"\bjokes?\b", r"\bmake me laugh\b", r"\bsomething funny\b")
def joke(ctx, command, match):
    try:
        import pyjokes

        return pyjokes.get_joke(language="en", category="neutral")
    except Exception:
        return random.choice(_BACKUP_JOKES)
