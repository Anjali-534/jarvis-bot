"""Window control: switch to, minimize, maximize, restore, snap, full screen, other screen, virtual desktops.

Windows are found through app_index.windows_for (process name first, then the window title), and every
real call goes through jarvis.desktop or system.hotkey so tests can fake them.
"""
import logging
import re

from jarvis import actions, desktop, hinglish as hi
from jarvis.router import skill
from jarvis.skills import app_index, system

log = logging.getLogger(__name__)

NOT_WINDOWS = "Sorry, that only works on Windows."
THIS = r"(?:this|current|the|active|my)?\s*(?:window|app|screen|one|it)?"
_THIS = re.compile(rf"^(?:{THIS})$")


def _is_this(name):
    return not name or bool(_THIS.match(name.strip()))


def target_window(name):
    """(window, label) for 'chrome' or for 'this window' (the active one). (None, label) if not open."""
    if _is_this(name):
        window = desktop.active_window()
        return window, (app_index.friendly(window) if window else "that window")
    windows = app_index.windows_for(name)
    if not windows:
        return None, app_index.spoken_query(name)
    return windows[0], app_index.friendly(windows[0])


# ---------------------------------------------------------------- switch
@skill("switch window",
       r"^(?:please\s+)?(?:switch|go|change|jump|flip)\s+(?:back\s+)?(?:to|over to)\s+(?:the\s+|my\s+)?(.+?)(?:\s+window)?$",
       r"^(?:please\s+)?(?:bring up|show me|focus(?: on)?|bring|activate)\s+(?:the\s+|my\s+)?(.+?)(?:\s+window)?(?:\s+to the front)?$",
       rf"{hi.LEAD}(.+?){hi.SWITCH}$",
       category="windows", help=["switch to Chrome", "chrome pe switch kar"],
       params={"app": "the open app to bring to the front, e.g. chrome"}, command="switch to {app}")
def switch_window(ctx, command, match):
    name = match.group(1).strip()
    if re.search(r"\b(?:desktop|screen|mode|hindi|english|tab|headphones?|speakers?|earphones?|"
                 r"wi-?fi|bluetooth|light|dark|song|track|music)\b", name):
        return None  # other skills: virtual desktops, dark mode, audio output, languages
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    windows = app_index.windows_for(name)
    if not windows:
        label = app_index.spoken_query(name)
        if app_index.match(label) and ctx.confirm(f"{label.capitalize()} isn't open. Open it?"):
            from jarvis.skills import apps

            opened = apps.open_target(label, ctx)
            return f"Opening {opened}." if opened else ""
        return f"{label.capitalize()} isn't open."
    window = windows[0]
    previous = desktop.active_window()
    desktop.focus(window.hwnd)
    label = app_index.friendly(window)
    actions.record(f"Switched to {label}",
                   undo=(lambda: desktop.focus(previous.hwnd)) if previous else None,
                   undo_text=f"switch back to {app_index.friendly(previous)}" if previous else "")
    return ""


# ---------------------------------------------------------------- minimize / maximize / restore
UNDO_SHOW = {"minimize": "restore", "maximize": "restore", "restore": "minimize"}


def _how(word):
    word = (word or "").lower()
    if word.startswith("unminimi") or word == "restore":
        return "restore"
    if word.startswith("minimi") or word in ("hide", "chhota"):
        return "minimize"
    if word.startswith("maximi") or word == "bada":
        return "maximize"
    return None


@skill("show desktop", r"^(?:please\s+)?(?:minimi[sz]e|hide)\s+(?:everything|all(?: (?:the\s+)?windows| apps)?|all of them)$",
       r"^(?:please\s+)?(?:show|go to)\s+(?:the\s+|my\s+)?desktop$", r"\bdesktop\s+(?:dikhao|dikha)\b",
       rf"{hi.LEAD}(?:sab|saari|sari)\s+(?:windows?\s+)?(?:minimi[sz]e|chhota|chhoti){hi.KAR}$",
       category="windows", help=["minimize everything", "show desktop"])
def show_desktop(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    system.hotkey("win", "d")
    actions.record("Showed the desktop (minimized everything)", undo=lambda: system.hotkey("win", "d"),
                   undo_text="bring the windows back")
    return ""


@skill("window size", r"^(?:please\s+)?(minimi[sz]e|maximi[sz]e|restore|unminimi[sz]e|hide)(?:\s+(?:the\s+|my\s+)?(.+?))?$",
       rf"{hi.LEAD}(.+?)\s+(?:ko\s+)?(minimi[sz]e|maximi[sz]e|restore|chhota|bada){hi.KAR}$",
       category="windows", help=["minimize Chrome", "maximize this window", "restore Notepad"],
       tool="window", params={"action": ["minimize", "maximize", "restore"], "app?": "open app; empty = this window"},
       command=lambda a: f"{a.get('action', 'minimize')} {a.get('app') or 'this window'}")
def window_size(ctx, command, match):
    first, second = match.group(1), match.group(2)
    how = _how(first)
    verb, name = (first, second) if how else (second, first)
    how = how or _how(verb) or "minimize"
    name = (name or "").strip()
    if re.search(r"\b(?:everything|all)\b", name):
        return None  # show desktop
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    window, label = target_window(name)
    if window is None:
        return f"{label.capitalize()} isn't open."
    desktop.show(window.hwnd, how)
    actions.record(f"{how.capitalize()}d {label}", undo=lambda: desktop.show(window.hwnd, UNDO_SHOW[how]),
                   undo_text=f"{UNDO_SHOW[how]} {label}")
    return ""


# ---------------------------------------------------------------- snap, full screen, other screen
@skill("snap window", r"^(?:please\s+)?(?:snap|move|put|dock)\s+(?:the\s+|my\s+)?(.+?)\s+(?:to\s+(?:the\s+)?)?(left|right)"
                      r"(?:\s+(?:side|half))?$",
       r"^(?:please\s+)?(?:snap|move|put)\s+(?:it\s+)?(?:to\s+(?:the\s+)?)?()(left|right)(?:\s+(?:side|half))?$",
       rf"{hi.LEAD}(.+?)\s+(?:ko\s+)?(left|right|baayein|daayein)\s+(?:mein|me|pe|par|side)?\s*(?:snap|lagao|laga|kar|karo|daal)"
       rf"{hi.KAR}$",
       category="windows", help=["snap Chrome left", "snap this window right"])
def snap_window(ctx, command, match):
    name, side = match.group(1).strip(), match.group(2).lower()
    side = {"baayein": "left", "daayein": "right"}.get(side, side)
    if re.search(r"\b(?:desktop|screen|monitor)\b", name):
        return None
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    window, label = target_window(name)
    if window is None:
        return f"{label.capitalize()} isn't open."
    desktop.focus(window.hwnd)
    system.hotkey("win", side)
    actions.record(f"Snapped {label} to the {side}")
    return ""


@skill("full screen", r"^(?:please\s+)?(?:go\s+|make\s+(?:it|this)\s+|toggle\s+|enter\s+|exit\s+|leave\s+)?full\s?-?screen"
                      r"(?:\s+mode)?(?:\s+(?:on|off))?$",
       r"^(?:please\s+)?(?:exit|leave|close)\s+full\s?-?screen(?:\s+mode)?$",
       rf"{hi.LEAD}full\s?-?screen{hi.KAR}$", category="windows", help=["full screen"])
def full_screen(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    window = desktop.active_window()
    if window is not None and window.is_banking:
        return "That looks like a banking site, so I'll keep my hands off it."
    system.press("f11")
    actions.record("Toggled full screen", undo=lambda: system.press("f11"), undo_text="toggle full screen back")
    return ""


@skill("other screen", r"\b(?:move|send|put|throw)\s+(?:this|it|the window|this window|(?:the\s+)?(.+?))\s+to\s+(?:the\s+)?"
                       r"(?:other|second|next|external|another)\s+(?:screen|monitor|display)\b",
       r"\b(?:dusri|doosri|dusre|doosre)\s+(?:screen|monitor)\s+(?:pe|par|mein)\b",
       category="windows", help=["move this to the other screen"])
def other_screen(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    if desktop.monitor_count() < 2:
        return "There's only one screen connected right now."
    name = (match.group(1) or "") if match.re.groups else ""
    window, label = target_window(name)
    if window is None:
        return f"{label.capitalize()} isn't open."
    desktop.focus(window.hwnd)
    system.hotkey("win", "shift", "right")
    actions.record(f"Moved {label} to the other screen", undo=lambda: system.hotkey("win", "shift", "left"),
                   undo_text=f"move {label} back")
    return ""


# ---------------------------------------------------------------- virtual desktops
@skill("virtual desktop", r"^(?:please\s+)?(?:new|create|add|make|open)\s+(?:a\s+)?(?:new\s+)?(?:virtual\s+)?desktop$",
       r"^(?:please\s+)?(?:switch\s+to\s+|go\s+to\s+)?(?:the\s+)?(next|previous|prev|last|left|right)\s+(?:virtual\s+)?desktop$",
       r"^(?:please\s+)?(?:close|remove|delete)\s+(?:this\s+|the\s+current\s+)?(?:virtual\s+)?desktop$",
       rf"{hi.LEAD}(?:naya|nayi)\s+desktop{hi.KAR}$", rf"{hi.LEAD}(agla|agle|pichla|pichle)\s+desktop(?:\s+pe)?{hi.KAR}$",
       category="windows", help=["new desktop", "next desktop", "previous desktop"])
def virtual_desktop(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    if re.search(r"\b(?:new|create|add|make|naya|nayi)\b", command) or re.search(r"\bopen\b", command):
        system.hotkey("win", "ctrl", "d")
        actions.record("Made a new virtual desktop", undo=lambda: system.hotkey("win", "ctrl", "f4"),
                       undo_text="close that desktop")
        return "New desktop ready."
    if re.search(r"\b(?:close|remove|delete)\b", command):
        system.hotkey("win", "ctrl", "f4")
        actions.record("Closed the current virtual desktop (its windows moved next door)")
        return "Closed this desktop. Its windows moved to the one next to it."
    forward = bool(re.search(r"\b(?:next|right|agla|agle)\b", command))
    system.hotkey("win", "ctrl", "right" if forward else "left")
    actions.record(f"Went to the {'next' if forward else 'previous'} desktop",
                   undo=lambda: system.hotkey("win", "ctrl", "left" if forward else "right"),
                   undo_text="go back to the other desktop")
    return ""


# ---------------------------------------------------------------- what's on screen, close this window
@skill("whats on screen", r"\bwhat(?:'s| is)\s+(?:on|in front of me on)\s+(?:my|the)\s+screen\b",
       r"\bwhat\s+(?:app|window)\s+(?:is\s+)?(?:this|open|in front|active)\b", r"\bwhich\s+(?:app|window)\s+(?:is\s+)?(?:this|open|active)\b",
       r"\bscreen\s+(?:pe|par)\s+(?:kya|kaunsa)\b", category="windows", help=["what's on my screen right now"])
def whats_on_screen(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    window = desktop.active_window()
    if window is None or not window.title:
        return "Just the desktop."
    label = app_index.friendly(window)
    title = window.title
    if title.lower() == label.lower():
        return f"{label} is in front."
    segments = re.split(r"\s+[-—|]\s+", title)
    if len(segments) > 1 and label.lower() in segments[-1].lower():  # "Inbox - Gmail - Google Chrome"
        title = " - ".join(segments[:-1])
    return f"{label} is in front, showing {title[:120]}."


def close_active(ctx):
    """'close this window': ask first if the app might have unsaved work, then close it like its X button."""
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    window = desktop.active_window()
    if window is None:
        return "There's no window to close."
    label = app_index.friendly(window)
    if window.is_terminal:
        if not ctx.confirm(f"That's a terminal ({label}). Close it anyway?"):
            return f"Okay, I'll leave {label} open."
    elif app_index.needs_confirm(label, [window]) and not ctx.confirm(f"{label} might have unsaved work. Close it anyway?"):
        actions.note(f"Didn't close {label} (you said no)")
        return f"Okay, I'll leave {label} open."
    desktop.close(window.hwnd)
    actions.record(f"Closed the {label} window")
    return ""


@skill("close window", r"^(?:please\s+)?(?:close|shut)\s+(?:this|the current|the active|current|the)\s+window$",
       r"^(?:please\s+)?close\s+(?:it|this)$", rf"{hi.LEAD}(?:yeh|ye|is)\s+window{hi.CLOSE}$",
       category="windows", help=["close this window"])
def close_window(ctx, command, match):
    return close_active(ctx)
