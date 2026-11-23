"""Open and close apps and websites."""
import os
import re
import shutil
import subprocess
import time

from jarvis.router import skill
from jarvis.skills import system, web

# Spoken name -> command or URI. Add your own apps here.
APPS = {
    "notepad": "notepad", "calculator": "calc", "calc": "calc", "paint": "mspaint", "ms paint": "mspaint",
    "command prompt": "cmd", "cmd": "cmd", "terminal": "wt", "windows terminal": "wt", "powershell": "powershell",
    "file explorer": "explorer", "explorer": "explorer", "files": "explorer", "my files": "explorer",
    "task manager": "taskmgr", "control panel": "control", "settings": "ms-settings:",
    "wifi settings": "ms-settings:network-wifi", "wi-fi settings": "ms-settings:network-wifi",
    "bluetooth settings": "ms-settings:bluetooth", "bluetooth": "ms-settings:bluetooth",
    "camera": "microsoft.windows.camera:",
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge", "firefox": "firefox",
    "vs code": "code", "vscode": "code", "visual studio code": "code", "code": "code",
    "pycharm": "pycharm64",
    "whatsapp": "whatsapp:", "spotify": "spotify:",
    "word": "winword", "microsoft word": "winword", "excel": "excel", "microsoft excel": "excel",
    "powerpoint": "powerpnt", "power point": "powerpnt", "outlook": "outlook",
    "teams": "msteams:", "microsoft teams": "msteams:", "zoom": "zoommtg:",
    "telegram": "tg:", "discord": "discord:",
}

WEBSITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "mail": "https://mail.google.com",
    "github": "https://github.com", "linkedin": "https://www.linkedin.com", "instagram": "https://www.instagram.com",
    "chatgpt": "https://chatgpt.com", "chat gpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com", "google": "https://www.google.com",
    "stack overflow": "https://stackoverflow.com", "stackoverflow": "https://stackoverflow.com",
    "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com",
    "whatsapp web": "https://web.whatsapp.com", "railway": "https://railway.app", "vercel": "https://vercel.com",
}

# Spoken name -> process image name for taskkill
PROCESSES = {
    "vs code": "Code.exe", "vscode": "Code.exe", "visual studio code": "Code.exe", "code": "Code.exe",
    "chrome": "chrome.exe", "google chrome": "chrome.exe", "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe", "whatsapp": "WhatsApp.exe", "spotify": "Spotify.exe", "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe", "calc": "CalculatorApp.exe", "paint": "mspaint.exe",
    "word": "WINWORD.EXE", "microsoft word": "WINWORD.EXE", "excel": "EXCEL.EXE", "powerpoint": "POWERPNT.EXE",
    "outlook": "OUTLOOK.EXE", "teams": "ms-teams.exe", "microsoft teams": "ms-teams.exe", "zoom": "Zoom.exe",
    "telegram": "Telegram.exe", "discord": "Discord.exe", "pycharm": "pycharm64.exe",
    "file explorer": "explorer.exe", "task manager": "Taskmgr.exe", "terminal": "WindowsTerminal.exe",
    "command prompt": "cmd.exe", "cmd": "cmd.exe", "powershell": "powershell.exe",
}

_URI = re.compile(r"^[a-z][\w.+-]*:(?!\\)", re.IGNORECASE)
_DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}$", re.IGNORECASE)


def split_targets(text):
    return [t.strip() for t in re.split(r"\s*(?:,|\band\b|&)\s*", text) if t.strip()]


def launch(target):
    """Start an app: URIs via os.startfile, everything else via `start "" target`."""
    if _URI.match(target) and not target.lower().startswith(("http:", "https:")):
        os.startfile(target)
    else:
        subprocess.Popen(f'start "" {target}', shell=True)


def start_menu(name):
    import pyautogui

    pyautogui.press("win")
    time.sleep(0.6)
    pyautogui.write(name, interval=0.03)
    time.sleep(0.8)
    pyautogui.press("enter")


def open_target(name):
    """Open one app/site and return a short phrase describing what happened."""
    key = re.sub(r"^(?:the|my)\s+", "", name.lower().strip(" ."))
    key = re.sub(r"\s+(?:app|application|website|site)$", "", key)
    if key in APPS:
        launch(APPS[key])
        return key
    if key in WEBSITES:
        web.open_url(WEBSITES[key])
        return key
    domain = re.sub(r"\s*\bdot\b\s*", ".", key).replace(" ", "")
    if _DOMAIN.match(domain):
        web.open_url(domain if domain.startswith("http") else f"https://{domain}")
        return domain
    exe = shutil.which(key) or shutil.which(key.replace(" ", ""))
    if exe:
        launch(f'"{exe}"')
        return key
    if not system.IS_WINDOWS:
        raise RuntimeError(f"Unknown app {key}")
    start_menu(key)
    return f"{key} from the Start menu"


@skill("open", r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:up\s+)?(.+)$", category='apps', help=['open Chrome', 'open VS Code and Notepad', 'open GitHub'], params={"target": "app or website, e.g. chrome, notepad, github"}, command="open {target}")
def open_skill(ctx, command, match):
    opened = [open_target(name) for name in split_targets(match.group(1))]
    if not opened:
        return None
    return "Opening " + (opened[0] if len(opened) == 1 else ", ".join(opened[:-1]) + " and " + opened[-1]) + "."


def kill(image):
    return system.run(["taskkill", "/IM", image, "/F"])


@skill("close", r"^(?:please\s+)?(?:close|quit|exit|kill|terminate)\s+(?:the\s+|my\s+)?(.+)$", category='apps', help=['close Chrome', 'close this tab'], params={"target": "app to close, e.g. chrome"}, command="close {target}", risky=True)
def close_skill(ctx, command, match):
    target = match.group(1).strip(" .")
    if re.fullmatch(r"(?:jarvis|yourself|you)", target):
        return None
    if re.fullmatch(r"(?:this\s+|current\s+|the\s+)?window", target):
        system.hotkey("alt", "f4")
        return ""
    if re.fullmatch(r"(?:this\s+|current\s+|the\s+)?tab", target):
        system.hotkey("ctrl", "w")
        return ""
    if not system.IS_WINDOWS:
        return system.NOT_WINDOWS
    closed, missing = [], []
    for name in split_targets(target):
        key = re.sub(r"\s+(?:app|application)$", "", name.lower())
        image = PROCESSES.get(key) or (key.replace(" ", "") + ".exe")
        result = kill(image)
        (closed if result.returncode == 0 else missing).append(key)
    parts = []
    if closed:
        parts.append("Closed " + " and ".join(closed) + ".")
    if missing:
        parts.append(" and ".join(missing) + (" doesn't" if len(missing) == 1 else " don't") + " seem to be running.")
    return " ".join(parts).capitalize() if not closed else " ".join(parts)
