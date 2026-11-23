"""Install the git pre-commit hook that runs scripts/check_secrets.py before every commit.

    python scripts/install_hooks.py
"""
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "# jarvis secret check"


def hooks_dir():
    out = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=ROOT, capture_output=True, text=True)
    path = Path(out.stdout.strip() or ".git/hooks")
    return path if path.is_absolute() else ROOT / path


def hook_script(python=None):
    python = (python or sys.executable).replace("\\", "/")
    return (f"#!/bin/sh\n{MARKER}\n"
            f'"{python}" "$(git rev-parse --show-toplevel)/scripts/check_secrets.py" || exit 1\n')


def install(directory=None):
    directory = directory or hooks_dir()
    directory.mkdir(parents=True, exist_ok=True)
    hook = directory / "pre-commit"
    if hook.exists() and MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
        backup = hook.with_name("pre-commit.before-jarvis")
        hook.replace(backup)
        print(f"Kept your old hook as {backup.name}.")
    hook.write_text(hook_script(), encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook


if __name__ == "__main__":
    path = install()
    print(f"Installed {path}. Every commit is now checked for API keys.")
