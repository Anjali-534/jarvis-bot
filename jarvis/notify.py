"""Windows toast notifications (win11toast, falling back to the tray icon's balloon, then plyer)."""
import logging
import threading

import config

log = logging.getLogger(__name__)

_tray_notifier = None  # set by the tray app: fn(title, message)


def set_tray_notifier(fn):
    global _tray_notifier
    _tray_notifier = fn


def _show(title, message):
    try:
        from win11toast import notify

        notify(f"{config.ASSISTANT_NAME}: {title}", message)  # default app id: an unregistered one may be dropped
        return "win11toast"
    except ImportError:
        pass
    except Exception as exc:
        log.warning("[notify] win11toast failed: %s", exc)
    if _tray_notifier:
        try:
            _tray_notifier(title, message)
            return "tray"
        except Exception as exc:
            log.warning("[notify] tray notification failed: %s", exc)
    try:
        from plyer import notification

        notification.notify(title=title, message=message, app_name=config.ASSISTANT_NAME, timeout=10)
        return "plyer"
    except Exception:
        log.info("[notify] no notification backend available")
        return None


def toast(title, message, background=True):
    """Show a toast if NOTIFY is on. Runs on a background thread so it never blocks a reminder."""
    if not config.NOTIFY:
        return False
    if background:
        threading.Thread(target=_show, args=(title, message), name="toast", daemon=True).start()
        return True
    return _show(title, message) is not None
