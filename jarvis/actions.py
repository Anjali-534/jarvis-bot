"""The action log, the undo stack and the protected-folder guard shared by the laptop-control skills.

* record("Moved resume.pdf to Documents", undo=fn, undo_text="move resume.pdf back to Downloads")
  appends a line to data/logs/actions.log and remembers the action for "what did you just do" / "undo that".
* guard(path) raises SpokenError for anything in Windows, Program Files, AppData or other system folders.
"""
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import config
from jarvis.router import SpokenError

log = logging.getLogger(__name__)

ACTIONS_LOG = "actions.log"
HISTORY = 30


@dataclass
class Action:
    what: str                          # "Moved resume.pdf to Documents"
    undo: Callable | None = None       # reverts it; None = can't be undone
    undo_text: str = ""                # "move resume.pdf back to Downloads"
    when: datetime = field(default_factory=datetime.now)


_history: deque = deque(maxlen=HISTORY)
_lock = threading.Lock()


def log_path():
    return config.LOG_DIR / ACTIONS_LOG


def write_log(line):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as out:
            out.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")
    except OSError:
        log.warning("Couldn't write the action log", exc_info=True)


def record(what, undo=None, undo_text=""):
    """Log an action Jarvis did, and remember it (with how to undo it) for this session."""
    action = Action(what, undo, undo_text)
    with _lock:
        _history.append(action)
    write_log(what + ("" if undo else "  [no undo]"))
    log.info("[action] %s", what)
    return action


def note(what):
    """Log something that isn't an action on its own (a refusal, a skipped step)."""
    write_log(what)


def recent(count=3):
    with _lock:
        return list(_history)[-count:][::-1]


def last_undoable():
    with _lock:
        for action in reversed(_history):
            if action.undo is not None:
                return action
    return None


def undo_last():
    """Undo the newest action that can be undone. Returns (action, error message or None)."""
    action = last_undoable()
    if action is None:
        return None, None
    with _lock:
        if action in _history:
            _history.remove(action)
    try:
        action.undo()
    except SpokenError as exc:
        return action, str(exc)
    except Exception as exc:
        log.exception("Undo failed for %s", action.what)
        return action, f"I couldn't undo that: {exc}"
    write_log(f"Undid: {action.what}")
    return action, None


def clear():
    with _lock:
        _history.clear()


def read_log_tail(count=3):
    """The last few lines of actions.log (for "what did you just do" after a restart)."""
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.split("  ", 1)[-1] for line in lines[-count:]][::-1]


# ---------------------------------------------------------------- protected folders
def _env_path(name, default):
    return Path(os.environ.get(name) or default)


def protected_roots():
    home = Path.home()
    windir = _env_path("SystemRoot", r"C:\Windows")
    roots = [
        windir,
        _env_path("ProgramFiles", r"C:\Program Files"),
        _env_path("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        _env_path("ProgramW6432", r"C:\Program Files"),
        _env_path("ProgramData", r"C:\ProgramData"),
        home / "AppData",
        Path(windir.drive + "\\") / "$Recycle.Bin",
        Path(windir.drive + "\\") / "System Volume Information",
        Path(windir.drive + "\\") / "Recovery",
        Path(windir.drive + "\\") / "Boot",
        Path(windir.drive + "\\") / "PerfLogs",
    ]
    return [r for r in roots if str(r)]


# Folder names that are never yours, wherever they turn up (a copy of AppData on another drive, say)
SYSTEM_NAMES = {"program files", "program files (x86)", "programdata", "appdata", "$recycle.bin",
                "system volume information", "system32", "syswow64", "winsxs"}


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def is_protected(path):
    """Never changed: system folders, drive roots, your user folder itself, and Jarvis's own folder."""
    target = _norm(path)
    if os.path.splitdrive(target)[1] in ("\\", "/", ""):
        return True  # a whole drive
    if target in (_norm(Path.home()), _norm(config.BASE_DIR)):
        return True
    return is_system(path)


def is_system(path):
    """Windows, Program Files, ProgramData, AppData and friends: never indexed, opened or changed."""
    target = _norm(path)
    for root in protected_roots():
        root = _norm(root)
        if target == root or target.startswith(root.rstrip("\\/") + os.sep):
            return True
    parts = {p.lower() for p in Path(target).parts}
    return bool(parts & SYSTEM_NAMES)


def guard(path, verb="touch"):
    """Refuse (SpokenError) to act on anything in a system folder."""
    if is_protected(path):
        note(f"Refused to {verb} {path} (system folder)")
        raise SpokenError(f"I won't {verb} anything in system folders like Windows, Program Files or AppData. "
                          "That's off limits.")
    return Path(path)
