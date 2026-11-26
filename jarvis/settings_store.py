"""settings.json in the data folder: everything you set in the wizard or Settings window except secrets.

Secrets go to the Windows Credential Manager (jarvis/vault.py). Values here are strings, numbers or booleans,
keyed by the same names as the environment variables (USER_NAME, DEFAULT_LOCATION, TTS_VOICE...).
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def path(data_dir=None):
    import config

    return Path(data_dir or config.DATA_DIR) / "settings.json"


def load(data_dir=None):
    try:
        data = json.loads(path(data_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(values, data_dir=None):
    """Write settings.json atomically. Secret keys are never written here (they go to the vault)."""
    from jarvis import vault

    clean = {k: v for k, v in values.items() if k not in vault.SECRET_KEYS}
    target = path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def update(changes, data_dir=None):
    """Merge changes in: secrets to the vault, the rest to settings.json. Returns the saved settings."""
    from jarvis import vault

    current = load(data_dir)
    for key, value in changes.items():
        if key in vault.SECRET_KEYS:
            vault.set(key, value)
        elif value is None:
            current.pop(key, None)
        else:
            current[key] = value
    save(current, data_dir)
    return current


def as_env(value):
    """A settings.json value as the string an environment variable would hold."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value)
    return str(value)


def done_setup(data_dir=None):
    return bool(load(data_dir).get("SETUP_DONE"))


def mark_setup_done(data_dir=None):
    return update({"SETUP_DONE": datetime.now().isoformat(timespec="seconds")}, data_dir)
