"""Global hotkey (default Ctrl+Alt+J) using pynput, which needs no admin rights on Windows."""
import logging

import config

log = logging.getLogger(__name__)

_SPECIAL = {"ctrl", "alt", "shift", "cmd", "win", "space", "enter", "tab", "esc", "backspace", "delete", "home",
            "end", "insert", "page_up", "page_down", "up", "down", "left", "right"} | {f"f{i}" for i in range(1, 25)}


def to_pynput(spec):
    """'ctrl+alt+j' -> '<ctrl>+<alt>+j' (pynput's GlobalHotKeys format)."""
    keys = [k.strip().lower() for k in (spec or "").replace(" ", "").split("+") if k.strip()]
    if not keys:
        raise ValueError("empty hotkey")
    parts = []
    for key in keys:
        key = {"control": "ctrl", "windows": "cmd", "win": "cmd", "escape": "esc", "return": "enter"}.get(key, key)
        if key in _SPECIAL:
            parts.append(f"<{key}>")
        elif len(key) == 1:
            parts.append(key)
        else:
            raise ValueError(f"unknown key {key!r} in hotkey {spec!r}")
    return "+".join(parts)


def start(callback, spec=None):
    """Start listening for the hotkey. Returns the listener, or None if disabled/unavailable."""
    spec = config.HOTKEY if spec is None else spec
    if not spec:
        return None
    try:
        combo = to_pynput(spec)
        from pynput import keyboard
    except ImportError:
        log.warning("[hotkey] pynput is not installed; run pip install -r requirements.txt")
        return None
    except ValueError as exc:
        log.warning("[hotkey] %s", exc)
        return None

    def fire():
        try:
            callback()
        except Exception:
            log.exception("hotkey callback failed")

    listener = keyboard.GlobalHotKeys({combo: fire})
    listener.daemon = True
    listener.start()
    log.info("[hotkey] Press %s to wake %s.", spec.upper(), config.ASSISTANT_NAME)
    return listener
