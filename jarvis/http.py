"""Shared HTTP session. Skills call these via ``http.get_json`` so tests can monkeypatch them.

`requests` is imported on the first network call, not at startup (it takes ~1 s to import on a slow laptop).
"""
import json
import sys

from jarvis import cache

_SESSION = None


def session():
    global _SESSION
    if _SESSION is None:
        import requests

        _SESSION = requests.Session()
        _SESSION.headers.update({
            "User-Agent": "JarvisVoiceAssistant/1.0 (personal desktop assistant; python-requests)",
            "Accept-Language": "en-IN,en;q=0.9",
        })
    return _SESSION


def is_request_error(exc):
    """True for requests' network errors (without importing requests if it was never used)."""
    requests = sys.modules.get("requests")
    return requests is not None and isinstance(exc, requests.RequestException)

DEFAULT_TIMEOUT = 6   # normal APIs; the AI uses config.AI_TIMEOUT (15 s)


def _cache_key(url, params):
    """URL + params, leaving out secrets (tokens/keys) so they're never written to data/cache.json."""
    safe = {k: v for k, v in (params or {}).items() if not any(s in k.lower() for s in ("token", "key"))}
    return url + "?" + json.dumps(safe, sort_keys=True, default=str)


def get_json(url, params=None, timeout=DEFAULT_TIMEOUT, cache_ttl=None, **kwargs):
    """GET JSON. cache_ttl (seconds) serves a saved copy from data/cache.json while it's fresh."""
    key = _cache_key(url, params) if cache_ttl else None
    if key:
        cached = cache.get(key, cache_ttl)
        if cached is not None:
            return cached
    response = session().get(url, params=params, timeout=timeout, **kwargs)
    response.raise_for_status()
    data = response.json()
    if key:
        cache.put(key, data)
    return data


def get_text(url, params=None, timeout=DEFAULT_TIMEOUT, **kwargs):
    response = session().get(url, params=params, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.text


def post_stream(url, payload, headers=None, timeout=DEFAULT_TIMEOUT):
    """POST JSON and return the response without reading the body (for server-sent-event streams)."""
    return session().post(url, json=payload, headers=headers or {}, timeout=timeout, stream=True)


def post_json(url, payload, headers=None, timeout=DEFAULT_TIMEOUT):
    """POST JSON and return the raw response (callers inspect status codes)."""
    return session().post(url, json=payload, headers=headers or {}, timeout=timeout)
