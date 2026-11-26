"""Open and close apps and websites."""
import os
import re
import shutil
import subprocess
import time

from jarvis import hinglish as hi
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
    # for "can't do it, shall I open ... instead?" (food, cabs, shopping, tickets)
    "swiggy": "https://www.swiggy.com", "zomato": "https://www.zomato.com", "blinkit": "https://blinkit.com",
    "zepto": "https://www.zeptonow.com", "uber": "https://m.uber.com", "ola": "https://book.olacabs.com",
    "rapido": "https://www.rapido.bike", "bookmyshow": "https://in.bookmyshow.com", "irctc": "https://www.irctc.co.in",
    "makemytrip": "https://www.makemytrip.com", "myntra": "https://www.myntra.com", "netflix": "https://www.netflix.com",
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


BROWSERS = {"chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge",
            "firefox": "firefox", "brave": "brave"}
_IN_BROWSER = re.compile(r"^(.+?)\s+(?:in|on|with|using)\s+(chrome|google chrome|edge|microsoft edge|firefox|brave)$")


def is_known_target(text):
    """Is every part of 'chrome and notepad' / 'github.com' / 'gmail in chrome' an app or site we know?"""
    from jarvis.skills import app_index

    parts = split_targets(text)
    if not parts:
        return False
    for part in parts:
        key = app_index.spoken_query(part)
        browser = _IN_BROWSER.match(key)
        domain = re.sub(r"\s*\bdot\b\s*", ".", key).replace(" ", "")
        if not (key in APPS or key in WEBSITES or (browser and browser.group(1) in WEBSITES) or _DOMAIN.match(domain)
                or app_index.candidates(key)[0] is not None):
            return False
    return True


def open_in_browser(url, browser):
    launch(f'{BROWSERS[browser]} "{url}"')


def open_target(name, ctx=None):
    """Open one app/site and return a short phrase describing what happened ("" = the user picked nothing).

    Order: the APPS map, websites, domains, every installed app (fuzzy, asks when 2-3 are equally close),
    programs on PATH, and only then typing the name into the Start menu."""
    from jarvis import actions
    from jarvis.skills import app_index

    key = re.sub(r"^(?:the|my)\s+", "", name.lower().strip(" ."))
    if app_index.ALIASES.get(key.replace("'", "")) in APPS:  # "what's app" -> whatsapp
        key = app_index.ALIASES[key.replace("'", "")]
    key = re.sub(r"\s+(?:app|application|website|site)$", "", key)
    key = app_index.ALIASES.get(key, key) if app_index.ALIASES.get(key, key) in APPS else key
    browser = _IN_BROWSER.match(key)
    if browser and browser.group(1) in WEBSITES:  # "gmail in chrome"
        open_in_browser(WEBSITES[browser.group(1)], browser.group(2))
        actions.record(f"Opened {browser.group(1)} in {browser.group(2)}")
        return f"{browser.group(1)} in {browser.group(2)}"
    if key in APPS:
        launch(APPS[key])
        actions.record(f"Opened {key}")
        return key
    if key in WEBSITES:
        web.open_url(WEBSITES[key])
        actions.record(f"Opened {WEBSITES[key]}")
        return key
    domain = re.sub(r"\s*\bdot\b\s*", ".", key).replace(" ", "")
    if _DOMAIN.match(domain):
        web.open_url(domain if domain.startswith("http") else f"https://{domain}")
        actions.record(f"Opened {domain}")
        return domain
    if system.IS_WINDOWS:
        opened = app_index.open_app(ctx, key)
        if opened is not None:
            return opened
    exe = shutil.which(key) or shutil.which(key.replace(" ", ""))
    if exe:
        launch(f'"{exe}"')
        actions.record(f"Opened {exe}")
        return key
    if not system.IS_WINDOWS:
        raise RuntimeError(f"Unknown app {key}")
    start_menu(key)  # last resort: type it into Start
    actions.record(f"Searched the Start menu for {key}")
    return f"{key} from the Start menu"


@skill("open", r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:up\s+)?(.+)$",
       rf"{hi.LEAD}{hi.MY}(.+?){hi.OPEN}$",
       category='apps', help=['open Chrome', 'open VS Code and Notepad', 'open GitHub', 'open Gmail in Chrome'],
       params={"target": "app or website, e.g. chrome, notepad, github"}, command="open {target}")
def open_skill(ctx, command, match):
    opened = [o for o in (open_target(name, ctx) for name in split_targets(match.group(1))) if o]
    if not opened:
        return "Okay, I didn't open anything." if split_targets(match.group(1)) else None
    return "Opening " + (opened[0] if len(opened) == 1 else ", ".join(opened[:-1]) + " and " + opened[-1]) + "."


def kill(image):
    """Ask a windowless process to exit (taskkill without /F: no forced kill)."""
    return system.run(["taskkill", "/IM", image])


@skill("close", r"^(?:please\s+)?(?:close|quit|exit|kill|terminate)\s+(?:the\s+|my\s+)?(.+)$",
       rf"{hi.LEAD}(.+?){hi.CLOSE}$",
       category='apps', help=['close Chrome', 'close this tab'], params={"target": "app to close, e.g. chrome"},
       command="close {target}", risky=True)
def close_skill(ctx, command, match):
    from jarvis.skills import app_index

    target = match.group(1).strip(" .")
    if re.fullmatch(r"(?:jarvis|yourself|you)", target):
        return None
    if re.search(r"\b(?:wi-?\s?fi|bluetooth|dnd|do not disturb|dark mode|light mode|night light|mic|microphone|"
                 r"music|gaana|gana|song|video|sound|awaaz|awaz|volume|notifications?|airplane mode|battery saver|laptop|pc|computer|system|machine|light|lights|fan|tv|door)\b",
                 target):
        return None  # "wifi band kar de" is a setting, "gaana band kar" is pause: not apps
    if re.fullmatch(r"(?:this\s+|current\s+|the\s+)?tab", target):
        system.hotkey("ctrl", "w")
        return ""
    if re.fullmatch(r"(?:this\s+|current\s+|the\s+)?window", target):
        from jarvis.skills import windows

        return windows.close_active(ctx)
    if not system.IS_WINDOWS:
        return system.NOT_WINDOWS
    closed, missing, kept = [], [], []
    for name in split_targets(target):
        key = re.sub(r"\s+(?:app|application)$", "", name.lower())
        result = app_index.close_app(ctx, key)
        if result is None:  # no window: maybe it only lives in the tray
            image = PROCESSES.get(key) or (key.replace(" ", "") + ".exe")
            if app_index.running_process(key) and kill(image).returncode == 0:
                from jarvis import actions

                actions.record(f"Closed {key} (asked {image} to exit)")
                closed.append(key)
            else:
                missing.append(key)
        elif result.startswith("Okay"):
            kept.append(result)
        else:
            closed.append(result)
    parts = kept[:]
    if closed:
        parts.append("Closed " + " and ".join(closed) + ".")
    if missing:
        parts.append(" and ".join(missing) + (" doesn't" if len(missing) == 1 else " don't") + " seem to be running.")
    text = " ".join(parts)
    return text[:1].upper() + text[1:] if text else ""
