"""Typing, keys, shortcuts and the clipboard, into whatever window is in front.

Safety:
* never types, pastes or presses Enter in a terminal unless you say "type in terminal ..." / "press enter in terminal"
* asks before typing into a password box, and before typing more than 200 characters
* keeps its hands off banking and payment sites
* never reads out clipboard text that looks like a password, OTP or API key
"""
import logging
import re

import config
from jarvis import actions, desktop, hinglish as hi
from jarvis.router import skill
from jarvis.skills import system

log = logging.getLogger(__name__)

NOT_WINDOWS = "Sorry, that only works on Windows."
BANKING = "That looks like a banking or payment site, so I'll keep my hands off it."
IN_TERMINAL = re.compile(r"\b(?:in|into|on)\s+(?:the\s+)?(?:terminal|command prompt|powershell|cmd|console)\b")


def front_window():
    try:
        return desktop.active_window()
    except Exception:
        log.debug("Couldn't read the active window", exc_info=True)
        return None


def check_window(allow_terminal=False, what="type"):
    """None if it's fine to send keys to the front window, else the reason to say instead."""
    window = front_window()
    if window is None:
        return None
    if window.is_banking:
        actions.note(f"Refused to {what} on a banking window ({window.title[:60]})")
        return BANKING
    if window.is_terminal and not allow_terminal:
        actions.note(f"Refused to {what} into a terminal")
        return (f"That's a terminal, so I won't {what} there. Say '{what} in terminal' if you really mean it.")
    return None


# ---------------------------------------------------------------- type
def clean_dictation(text):
    """'hello comma how are you question mark' -> 'hello, how are you?'"""
    marks = {"comma": ",", "full stop": ".", "period": ".", "question mark": "?", "exclamation mark": "!",
             "colon": ":", "semicolon": ";", "new line": "\n", "next line": "\n", "new paragraph": "\n\n"}
    for word, mark in marks.items():
        text = re.sub(rf"\s*\b{word}\b\s*", mark if mark.startswith("\n") else mark + " ", text, flags=re.IGNORECASE)
    return re.sub(r" +", " ", text).strip(" ").replace(" \n", "\n")


@skill("type text", r"^(?:please\s+)?(?:type|write out|dictate)(\s+(?:in|into|on)\s+(?:the\s+)?(?:terminal|command prompt|"
                    r"powershell|cmd|console))?\s*:?\s+(.+)$",
       rf"{hi.LEAD}(.+?)\s+type{hi.KAR}$",
       category="keyboard", help=["type hello, how are you", "type in terminal git status"],
       params={"text": "exactly what to type"}, command="type {text}", risky=True)
def type_text(ctx, command, match):
    original = ctx.state.get("original") or ""
    if original.lower() == command:  # the same words with their capitals: "type Hello Riya"
        match = match.re.search(original) or match
    terminal = bool(match.re.groups >= 2 and match.group(1))
    raw = match.group(2) if match.re.groups >= 2 else match.group(1)
    text = clean_dictation(raw)
    if not text:
        return "What should I type?"
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    problem = check_window(allow_terminal=terminal)
    if problem:
        return problem
    if desktop.focused_is_password():
        if not ctx.confirm("That's a password box. Type into it anyway?"):
            actions.note("Didn't type into a password box (you said no)")
            return "Okay, I didn't type anything."
        desktop.type_text(text)
        actions.record(f"Typed {len(text)} characters into a password box (text not logged)")
        return ""
    if len(text) > config.TYPE_CONFIRM_CHARS and not ctx.confirm(
            f"That's {len(text)} characters. Type it all?"):
        actions.note("Didn't type a long text (you said no)")
        return "Okay, I didn't type it."
    desktop.type_text(text)
    preview = text if len(text) <= 40 else text[:40] + "..."
    actions.record(f"Typed '{preview}'" + (" in the terminal" if terminal else ""),
                   undo=lambda: system.hotkey("ctrl", "z"), undo_text="press Ctrl+Z")
    return ""


# ---------------------------------------------------------------- single keys
KEYS = {
    "enter": "enter", "return": "enter", "escape": "esc", "esc": "esc", "tab": "tab", "space": "space",
    "spacebar": "space", "backspace": "backspace", "back space": "backspace", "delete": "delete", "del": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right", "up arrow": "up", "down arrow": "down",
    "left arrow": "left", "right arrow": "right", "home": "home", "end": "end", "page up": "pageup",
    "page down": "pagedown", "insert": "insert", "caps lock": "capslock", "print screen": "printscreen",
    "windows": "win", "windows key": "win", "start": "win",
    **{f"f{n}": f"f{n}" for n in range(1, 13)},
}
_KEY_NAMES = "|".join(sorted(map(re.escape, KEYS), key=len, reverse=True))
TIMES = {"once": 1, "twice": 2, "thrice": 3, "two": 2, "three": 3, "four": 4, "five": 5}


@skill("press key", rf"^(?:please\s+)?(?:press|hit|tap|push)\s+(?:the\s+)?({_KEY_NAMES})(?:\s+(?:key|button))?"
                    r"(?:\s+(\d{1,2}|once|twice|thrice|two|three|four|five)(?:\s+times)?)?"
                    r"(\s+(?:in|on)\s+(?:the\s+)?terminal)?$",
       rf"{hi.LEAD}({_KEY_NAMES})\s+(?:press|daba|dabao|daba do|daba de|maar|maaro){hi.KAR}()()$",
       category="keyboard", help=["press enter", "press escape", "press tab 3 times"])
def press_key(ctx, command, match):
    key = KEYS[match.group(1).lower()]
    count = match.group(2) or "1"
    count = TIMES.get(count, None) or (int(count) if count.isdigit() else 1)
    count = max(1, min(20, count))
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    risky_key = key in ("enter", "delete", "backspace")
    problem = check_window(allow_terminal=bool(match.group(3)) or not risky_key, what="press " + match.group(1))
    if problem:
        return problem
    system.press(key, count)
    actions.record(f"Pressed {match.group(1)}" + (f" {count} times" if count > 1 else ""))
    return ""


# ---------------------------------------------------------------- shortcuts
# spoken -> (keys, what it did, (undo keys, undo text) or None, refuse in terminal?)
SHORTCUTS = {
    "select all": (("ctrl", "a"), "Selected everything", None, False),
    "copy": (("ctrl", "c"), "Copied the selection", None, False),
    "cut": (("ctrl", "x"), "Cut the selection", (("ctrl", "z"), "undo the cut"), False),
    "paste": (("ctrl", "v"), "Pasted", (("ctrl", "z"), "undo the paste"), True),
    "undo": (("ctrl", "z"), "Pressed undo (Ctrl+Z)", (("ctrl", "y"), "redo it"), False),
    "redo": (("ctrl", "y"), "Pressed redo (Ctrl+Y)", (("ctrl", "z"), "undo it"), False),
    "save": (("ctrl", "s"), "Saved (Ctrl+S)", None, False),
    "new tab": (("ctrl", "t"), "Opened a new tab", (("ctrl", "w"), "close that tab"), False),
    "close tab": (("ctrl", "w"), "Closed the tab", (("ctrl", "shift", "t"), "reopen it"), False),
    "reopen tab": (("ctrl", "shift", "t"), "Reopened the last closed tab", (("ctrl", "w"), "close it again"), False),
    "next tab": (("ctrl", "tab"), "Went to the next tab", (("ctrl", "shift", "tab"), "go back a tab"), False),
    "previous tab": (("ctrl", "shift", "tab"), "Went to the previous tab", (("ctrl", "tab"), "go forward a tab"), False),
    "go back": (("alt", "left"), "Went back", (("alt", "right"), "go forward again"), False),
    "go forward": (("alt", "right"), "Went forward", (("alt", "left"), "go back again"), False),
    "refresh": (("f5",), "Refreshed", None, False),
    "zoom in": (("ctrl", "="), "Zoomed in", (("ctrl", "-"), "zoom back out"), False),
    "zoom out": (("ctrl", "-"), "Zoomed out", (("ctrl", "="), "zoom back in"), False),
    "reset zoom": (("ctrl", "0"), "Reset the zoom", None, False),
    "find": (("ctrl", "f"), "Opened find", None, False),
}
SPOKEN = {
    "select all": r"select (?:all|everything)(?: the text)?|sab select(?: kar\w*)?",
    "copy": r"copy(?: (?:it|this|the selection|selected text|the text))?",
    "cut": r"cut(?: (?:it|this|the selection))?",
    "paste": r"paste(?: (?:it|here|that))?|paste kar\w*",
    "undo": r"undo|ctrl z|control z",
    "redo": r"redo|ctrl y|control y",
    "save": r"save(?: (?:it|this|the file|my work|the document))?|ctrl s|control s|save kar\w*",
    "new tab": r"(?:open )?(?:a )?new tab|naya tab(?: khol\w*)?",
    "close tab": r"close (?:the |this |current )?tab|tab band kar\w*",
    "reopen tab": r"(?:reopen|restore|bring back|undo close) (?:the )?(?:last |closed |last closed )?tab",
    "next tab": r"(?:go to )?(?:the )?next tab",
    "previous tab": r"(?:go to )?(?:the )?previous tab",
    "go back": r"go back(?: a page)?|back (?:a )?page|previous page|peeche jao|piche jao",
    "go forward": r"go forward(?: a page)?|forward (?:a )?page|next page",
    "refresh": r"refresh(?: (?:the |this )?(?:page|tab|window))?|reload(?: (?:the |this )?(?:page|tab))?",
    "zoom in": r"zoom in(?: (?:a bit|more))?|make (?:it|the text|text) bigger",
    "zoom out": r"zoom out(?: (?:a bit|more))?|make (?:it|the text|text) smaller",
    "reset zoom": r"reset (?:the )?zoom|zoom (?:to )?(?:100|normal|reset)",
    "find": r"find on (?:this )?page|ctrl f|control f",
}
_SHORTCUT = re.compile("^(?:please\\s+)?(?:" + "|".join(f"(?P<{k.replace(' ', '_')}>{v})" for k, v in SPOKEN.items())
                       + ")$")


@skill("shortcut", _SHORTCUT.pattern, category="keyboard",
       help=["select all", "copy", "paste", "undo", "save", "new tab", "close tab", "reopen tab", "go back",
             "refresh", "zoom in"],
       tool="keyboard_shortcut", params={"action": list(SHORTCUTS)}, command="{action}")
def shortcut(ctx, command, match):
    name = next(k.replace("_", " ") for k, v in match.groupdict().items() if v)
    keys, did, undo, terminal_risky = SHORTCUTS[name]
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    problem = check_window(allow_terminal=not terminal_risky, what=name)
    if problem:
        return problem
    system.hotkey(*keys)
    if undo:
        actions.record(did, undo=lambda: system.hotkey(*undo[0]), undo_text=undo[1])
    else:
        actions.record(did)
    return ""


@skill("scroll", r"^(?:please\s+)?scroll\s+(up|down|left|right)(?:\s+(a lot|a little|a bit|more|to the (?:top|bottom)))?$",
       r"^(?:please\s+)?(?:go|jump)\s+to\s+the\s+(top|bottom)(?:\s+of the page)?$",
       rf"{hi.LEAD}(?:neeche|niche|upar|oopar)\s+(?:scroll|jao){hi.KAR}()$",
       category="keyboard", help=["scroll down", "scroll up a lot"])
def scroll(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    word = (match.group(1) or "").lower() or ("down" if re.search(r"\b(?:neeche|niche)\b", command) else "up")
    amount = (match.group(2) or "").lower() if match.re.groups >= 2 else ""
    if word in ("top", "bottom") or "top" in amount or "bottom" in amount:
        system.hotkey("ctrl", "home" if "top" in (word + amount) else "end")
        return ""
    clicks = {"a lot": 15, "more": 10, "a little": 3, "a bit": 3}.get(amount, 6)
    scroll_by(word, clicks)
    return ""


def scroll_by(direction, clicks):
    import pyautogui

    if direction in ("left", "right"):
        pyautogui.hscroll(clicks * 100 * (1 if direction == "right" else -1))
    else:
        pyautogui.scroll(clicks * 100 * (1 if direction == "up" else -1))


# ---------------------------------------------------------------- clipboard
SECRET = [
    re.compile(r"^(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}$"), re.compile(r"^AIza[0-9A-Za-z_-]{20,}$"),
    re.compile(r"^AQ\.[0-9A-Za-z_.-]{20,}$"), re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"),
    re.compile(r"^gsk_[A-Za-z0-9]{20,}$"), re.compile(r"^xox[abpr]-"), re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\."),
    re.compile(r"^\d{4,8}$"),  # an OTP or PIN
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def looks_secret(text):
    """A password, OTP, PIN or API key: one 'word' mixing letters, digits and symbols, or a known key format."""
    text = (text or "").strip()
    if any(p.search(text) for p in SECRET):
        return True
    if " " in text or not 6 <= len(text) <= 64 or re.match(r"^https?://", text):
        return False
    kinds = sum(bool(re.search(p, text)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]"))
    return kinds >= 3 and not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.]+", text)


def first_sentences(text, count=2):
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(parts[:count])
    return short if len(short) <= 300 else short[:297].rsplit(" ", 1)[0] + "..."


@skill("read clipboard", r"\b(?:read|what(?:'s| is)(?: on| in)?)\s+(?:out\s+)?(?:my\s+|the\s+)?clipboard\b",
       r"\bclipboard\s+(?:mein|me|pe)\s+(?:kya|kya hai)\b", r"\bwhat did i (?:just )?copy\b",
       category="keyboard", help=["read my clipboard"])
def read_clipboard(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    text = desktop.get_clipboard()
    if not text or not text.strip():
        return "There's no text on your clipboard."
    if looks_secret(text):
        actions.note("Didn't read the clipboard aloud (it looks like a password or key)")
        return "That looks like a password or a key, so I won't read it out."
    return "Your clipboard says: " + first_sentences(text)


@skill("copy answer", r"^(?:please\s+)?copy\s+(?:that|what you (?:just )?said|your (?:last )?answer|the answer|it to (?:the |my )?clipboard)$",
       r"\b(?:yeh|ye|woh|wo)\s+copy\s+kar\w*", category="keyboard", help=["copy that"])
def copy_answer(ctx, command, match):
    text = ctx.state.get("last_answer") or ""
    if not text:
        return "I haven't said anything to copy yet."
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    desktop.set_clipboard(text)
    actions.record("Copied my last answer to the clipboard")
    return "Copied."
