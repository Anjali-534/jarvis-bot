"""Every test runs offline, without real apps, and without the user's real data files."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import jarvis.skills  # noqa: E402,F401
from jarvis import geo, http, router  # noqa: E402
from jarvis.skills import vscode, whatsapp  # noqa: E402

PURE_SKILLS = {"calculator"}  # real handlers that may return None to fall through


@pytest.fixture
def recorded_skills(monkeypatch):
    """Replace every handler with one that just reports its name (routing tests)."""
    for sk in router.SKILLS:
        if sk.name not in PURE_SKILLS:
            monkeypatch.setattr(sk, "handler", lambda ctx, cmd, m, name=sk.name: f"<{name}>")
    monkeypatch.setattr(router.FALLBACK, "handler", lambda ctx, cmd, m: "<ai>")
    monkeypatch.setattr(router.FALLBACK, "name", "ai")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected real side effect: {args[:1]}")

    for name in ("get_json", "get_text", "post_json"):
        monkeypatch.setattr(http, name, forbidden)
    for name in ("open_uri", "open_web", "press_enter"):
        monkeypatch.setattr(whatsapp, name, forbidden)
    monkeypatch.setattr(whatsapp, "sleep", lambda seconds: None)
    monkeypatch.setattr(vscode, "shell", forbidden)
    monkeypatch.setattr(vscode, "git", forbidden)

    monkeypatch.setattr(config, "USER_NAME", "Anjali")
    monkeypatch.setattr(config, "WAQI_TOKEN", "")
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(config, "WHATSAPP_SEND_DELAY", 3.0)
    monkeypatch.setattr(config, "RED_ZONE_THRESHOLD", 200)
    monkeypatch.setattr(config, "CONTACTS_FILE", tmp_path / "data" / "contacts.json")
    monkeypatch.setattr(config, "PROJECTS_FILE", tmp_path / "data" / "projects.json")
    monkeypatch.setattr(config, "PROJECT_ROOTS", [])
    monkeypatch.setattr(vscode, "_cache", None)
    geo._CACHE.clear()
