"""A small JSON cache on disk (data/cache.json) so weather, AQI and places survive restarts.

    cache.get(key, max_age_seconds) -> value or None
    cache.put(key, value)
"""
import json
import logging
import threading
import time

import config

log = logging.getLogger(__name__)

MAX_ENTRIES = 500
_lock = threading.Lock()
_data = None
_loaded_from = None


def _load():
    global _data, _loaded_from
    path = config.CACHE_FILE
    if _data is not None and _loaded_from == path:
        return _data
    _data, _loaded_from = {}, path
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            _data = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("[cache] ignoring unreadable %s: %s", path, exc)
    return _data


def get(key, max_age):
    with _lock:
        entry = _load().get(key)
    if not entry or time.time() - entry.get("t", 0) > max_age:
        return None
    return entry.get("v")


def put(key, value):
    with _lock:
        data = _load()
        data[key] = {"t": time.time(), "v": value}
        if len(data) > MAX_ENTRIES:  # drop the oldest
            for old in sorted(data, key=lambda k: data[k].get("t", 0))[:len(data) - MAX_ENTRIES]:
                del data[old]
        try:
            config.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = config.CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(config.CACHE_FILE)
        except OSError as exc:
            log.warning("[cache] couldn't save: %s", exc)


def clear():
    global _data
    with _lock:
        _data = {}
        if config.CACHE_FILE.exists():
            config.CACHE_FILE.unlink()
