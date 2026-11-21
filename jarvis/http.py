"""Shared HTTP session. Skills call these via ``http.get_json`` so tests can monkeypatch them."""
import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "JarvisVoiceAssistant/1.0 (personal desktop assistant; python-requests)",
    "Accept-Language": "en-IN,en;q=0.9",
})

DEFAULT_TIMEOUT = 10


def get_json(url, params=None, timeout=DEFAULT_TIMEOUT, **kwargs):
    response = SESSION.get(url, params=params, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.json()


def get_text(url, params=None, timeout=DEFAULT_TIMEOUT, **kwargs):
    response = SESSION.get(url, params=params, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.text


def post_json(url, payload, headers=None, timeout=30):
    """POST JSON and return the raw response (callers inspect status codes)."""
    return SESSION.post(url, json=payload, headers=headers or {}, timeout=timeout)
