"""Start Jarvis when you log in: a shortcut in the Windows Startup folder (shell:startup)."""
import logging
import os
import subprocess
import sys
from pathlib import Path

import config

log = logging.getLogger(__name__)

SHORTCUT_NAME = f"{config.ASSISTANT_NAME}.lnk"
LAUNCHER = "run_jarvis.pyw"


def startup_folder():
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path():
    return startup_folder() / SHORTCUT_NAME


def pythonw_path(repo=None, executable=None):
    """Prefer the repo's venv pythonw.exe, else the pythonw.exe next to the running Python."""
    repo = Path(repo or config.BASE_DIR)
    for venv in (".venv", "venv", "env"):
        candidate = repo / venv / "Scripts" / "pythonw.exe"
        if candidate.exists():
            return candidate
    current = Path(executable or sys.executable)
    pythonw = current.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else current


def shortcut_spec(repo=None, executable=None):
    if getattr(config, "FROZEN", False) and repo is None:  # the installed Jarvis.exe starts itself in the tray
        exe = Path(executable or sys.executable)
        return {"path": shortcut_path(), "target": exe, "arguments": "", "working_dir": exe.parent,
                "description": f"Start {config.ASSISTANT_NAME} voice assistant in the tray"}
    repo = Path(repo or config.REPO_DIR)
    return {
        "path": shortcut_path(),
        "target": pythonw_path(repo, executable),
        "arguments": f'"{repo / LAUNCHER}"',
        "working_dir": repo,
        "description": f"Start {config.ASSISTANT_NAME} voice assistant in the tray",
    }


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell_script(spec):
    return (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(spec["path"]) + "); "
        "$s.TargetPath = " + _ps_quote(spec["target"]) + "; "
        "$s.Arguments = " + _ps_quote(spec["arguments"]) + "; "
        "$s.WorkingDirectory = " + _ps_quote(spec["working_dir"]) + "; "
        "$s.Description = " + _ps_quote(spec["description"]) + "; "
        "$s.WindowStyle = 7; $s.Save()"
    )


def run_powershell(script):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=30,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def is_installed():
    return shortcut_path().exists()


def install():
    if sys.platform != "win32":
        raise RuntimeError("Starting with Windows only works on Windows.")
    spec = shortcut_spec()
    spec["path"].parent.mkdir(parents=True, exist_ok=True)
    result = run_powershell(powershell_script(spec))
    if result.returncode != 0 or not spec["path"].exists():
        raise RuntimeError(f"Couldn't create the startup shortcut: {(result.stderr or '').strip()[:200]}")
    log.info("[startup] created %s -> %s %s", spec["path"], spec["target"], spec["arguments"])
    return spec["path"]


def uninstall():
    path = shortcut_path()
    if path.exists():
        path.unlink()
        log.info("[startup] removed %s", path)
        return True
    return False
