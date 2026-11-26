"""Secrets (API keys, tokens) in the Windows Credential Manager via `keyring`, never in plain-text files.

Look in Control Panel > Credential Manager > Windows Credentials: entries are named "Jarvis" (or "Jarvis (dev)"
for a developer checkout) with the setting name as the user name, e.g. AI_API_KEY.
"""
import logging
import os

log = logging.getLogger(__name__)

# Settings that are secrets: they live in the vault, never in settings.json
SECRET_KEYS = ("AI_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "WAQI_TOKEN", "TELEGRAM_BOT_TOKEN")


def service():
    import config

    return "Jarvis (dev)" if getattr(config, "DEV", False) else "Jarvis"


def disabled():
    return os.getenv("JARVIS_NO_VAULT", "").strip().lower() in ("1", "true", "yes")


def get(name):
    if disabled():
        return None
    try:
        import keyring

        return keyring.get_password(service(), name)
    except Exception as exc:  # no backend, locked vault...: Jarvis still starts, the key is just missing
        log.warning("[vault] couldn't read %s: %s", name, exc)
        return None


def set(name, value):  # noqa: A001 (mirrors keyring's own API)
    import keyring

    if value:
        keyring.set_password(service(), name, value)
    else:
        delete(name)
    return True


def delete(name):
    try:
        import keyring

        keyring.delete_password(service(), name)
        return True
    except Exception:
        return False


def all_secrets():
    """{name: value} for every secret that's set."""
    found = {}
    for name in SECRET_KEYS:
        value = get(name)
        if value:
            found[name] = value
    return found


def mask(value):
    """'gsk_...a1b2' for showing that a key is set without showing it."""
    value = value or ""
    return "" if not value else (value[:4] + "..." + value[-4:] if len(value) > 12 else "set")
