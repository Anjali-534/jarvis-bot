"""Pre-commit secret check. Fake keys are built at runtime so this file itself never contains one."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load("check_secrets")
hooks = load("install_hooks")

RANDOM = "Xq7Lm2Vb9Tz4Kp8Wn3Rf6Hd1Js5Gc0Ya"  # fake, 34 chars, mixed case + digits. secret-check: ignore


def fake(prefix, body=RANDOM):
    return prefix + body


@pytest.mark.parametrize("line, kind", [
    (f"AI_API_KEY={fake('AI' + 'za')}", "Google API key"),
    (f"key = '{fake('AQ' + '.Ab')}'", "Google auth key"),
    (f"AI_API_KEY={fake('gs' + 'k_')}", "Groq API key"),
    (f"OPENAI_API_KEY={fake('s' + 'k-')}", "OpenAI-style secret key"),
    (f"token: {fake('s' + 'k-proj-')}", "OpenAI-style secret key"),
    (f"GITHUB={fake('gh' + 'p_')}", "GitHub token"),
    ("-----BEGIN RSA " + "PRIVATE KEY-----", "Private key"),
    (f"WAQI_TOKEN={RANDOM}", "long random token"),
])
def test_detects_secrets(line, kind):
    hits = list(check.scan_text(f"first line\n{line}\n"))
    assert [(n, k) for n, k, _ in hits] == [(2, kind)]
    assert RANDOM not in hits[0][2]  # masked: never printed in full


@pytest.mark.parametrize("line", [
    "AI_API_KEY=",
    "commit 4798795f3f85e11775ec6acfb31d19fc00698927",          # git SHA: hex only
    "def test_reminder_fires_into_later_test_without_crashing():",
    "SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback",
    'KEY = "AQ.test-key"', "sk-test", 'monkeypatch.setattr(config, "AI_API_KEY", "gsk-test")',
    "LONG_CONSTANT_NAME_FOR_SOMETHING_IN_CONFIG = 1",
    f"KEY={RANDOM}  # secret-check: ignore",
])
def test_ignores_normal_code(line):
    assert list(check.scan_text(line)) == []


def test_repository_has_no_secrets():
    assert check.check(check.git("ls-files").stdout.splitlines(),
                       lambda p: (ROOT / p).read_text(encoding="utf-8", errors="replace")) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_hook_blocks_a_staged_key(tmp_path, monkeypatch):
    def run(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True)

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check_secrets.py", tmp_path / "scripts" / "check_secrets.py")
    hooks.install(tmp_path / ".git" / "hooks")

    (tmp_path / "ok.py").write_text("print('hello')\n", encoding="utf-8")
    run("add", "-A")
    assert run("commit", "-q", "-m", "clean").returncode == 0

    (tmp_path / ".env.example").write_text(f"AI_API_KEY={fake('gs' + 'k_')}\n", encoding="utf-8")
    run("add", ".env.example")
    blocked = run("commit", "-q", "-m", "leak")
    assert blocked.returncode != 0
    assert "Groq API key" in blocked.stderr and RANDOM not in blocked.stderr
    assert run("log", "--oneline").stdout.count("\n") == 1  # the leaking commit didn't happen


def test_staged_version_is_checked_not_working_copy(monkeypatch):
    monkeypatch.setattr(check, "staged_files", lambda: ["a.txt"])
    monkeypatch.setattr(check, "staged_content", lambda path: f"KEY={fake('gs' + 'k_')}")
    assert check.main([]) == 1
    monkeypatch.setattr(check, "staged_content", lambda path: "KEY=")
    assert check.main([]) == 0
