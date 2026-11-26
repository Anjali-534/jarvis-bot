"""Every installed app: Start-menu shortcuts (yours and all users'), Store apps (Get-StartApps) and the APPS map.

The index is cached in data/app_index.json and rebuilt once a day in the background, or on "refresh apps".
"open X" fuzzy-matches it (speech errors like "what's app" or "vs code" included) and asks when 2-3 apps are
equally close. This module also finds an app's windows, so it answers "is Chrome open?", "list open apps",
"restart Spotify" and does the closing for "close X" (gracefully, asking first if there may be unsaved work).
"""
import difflib
import functools
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import config
from jarvis import actions, desktop, hinglish as hi
from jarvis.router import join_and, on_startup, skill, speak_list

log = logging.getLogger(__name__)

REFRESH_HOURS = 24
MATCH_CUTOFF = 0.72       # below this an app isn't considered at all
CLEAR_WIN = 0.95          # a score this high opens without asking
TIE_GAP = 0.06            # apps within this of the best are "equally close": ask which one
# Shortcuts that aren't apps
JUNK = re.compile(r"\b(?:uninstall\w*|readme|read me|help|documentation|manual|license|release notes|website|"
                  r"what's new|changelog|support|faq|on the web|repair|setup|eula)\b", re.IGNORECASE)
# What speech recognition writes -> what the app is called
ALIASES = {
    "what's app": "whatsapp", "whats app": "whatsapp", "what app": "whatsapp", "watts app": "whatsapp",
    "what sapp": "whatsapp", "vs code": "visual studio code", "v s code": "visual studio code",
    "vscode": "visual studio code", "ms word": "word", "ms excel": "excel", "power point": "powerpoint",
    "you tube": "youtube", "spot if i": "spotify", "spotty fy": "spotify", "note pad": "notepad",
    "calculator app": "calculator", "file manager": "file explorer", "my computer": "file explorer",
    "this pc": "file explorer", "photo": "photos", "clock": "clock", "store": "microsoft store",
    "play store": "microsoft store", "vlc player": "vlc media player", "vlc": "vlc media player",
}
# Friendly names for common process images (window lists, "is X open")
FRIENDLY = {
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "brave.exe": "Brave", "code.exe": "VS Code",
    "spotify.exe": "Spotify", "whatsapp.exe": "WhatsApp", "notepad.exe": "Notepad", "winword.exe": "Word",
    "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "outlook.exe": "Outlook", "ms-teams.exe": "Teams",
    "zoom.exe": "Zoom", "telegram.exe": "Telegram", "discord.exe": "Discord", "explorer.exe": "File Explorer",
    "taskmgr.exe": "Task Manager", "windowsterminal.exe": "Terminal", "cmd.exe": "Command Prompt",
    "powershell.exe": "PowerShell", "pycharm64.exe": "PyCharm", "mspaint.exe": "Paint", "vlc.exe": "VLC",
    "calculatorapp.exe": "Calculator", "systemsettings.exe": "Settings", "acrord32.exe": "Adobe Acrobat",
    "acrobat.exe": "Adobe Acrobat", "obs64.exe": "OBS", "slack.exe": "Slack", "notion.exe": "Notion",
    "figma.exe": "Figma", "postman.exe": "Postman", "devenv.exe": "Visual Studio", "onenote.exe": "OneNote",
}
# Closing these can't lose work (or they restore it themselves): no "are you sure?"
SAFE_TO_CLOSE = {"chrome", "edge", "firefox", "brave", "spotify", "whatsapp", "calculator", "teams", "zoom",
                 "telegram", "discord", "file explorer", "task manager", "settings", "camera", "photos", "clock",
                 "vlc", "media player", "netflix", "microsoft store", "slack", "maps", "weather", "news"}
DIRTY = re.compile(r"^\*|\*$|\s\*\s|●|\bunsaved\b|\bmodified\b|\(not saved\)", re.IGNORECASE)

_cache = {"apps": None, "built": None}
_cache_lock = threading.Lock()
_build_lock = threading.Lock()


# ---------------------------------------------------------------- names
@functools.lru_cache(maxsize=4096)
def norm(name):
    name = (name or "").lower().replace("'", "").replace("’", "")
    name = re.sub(r"[®™©]", "", name)
    name = re.sub(r"\(.*?\)", " ", name)
    return re.sub(r"[^a-z0-9+#]+", " ", name).strip()


def compact(name):
    return norm(name).replace(" ", "")


def spoken_query(text):
    """'the vs code app' -> 'visual studio code'; "what's app" -> 'whatsapp'."""
    key = re.sub(r"^(?:the|my|an?)\s+", "", (text or "").lower().strip(" .?!"))
    for candidate in (key, key.replace("'", "")):  # "whats app" before "app" is stripped off it
        if candidate in ALIASES:
            return ALIASES[candidate]
    key = re.sub(r"\s+(?:app|application|program|software)$", "", key)
    return ALIASES.get(key, ALIASES.get(key.replace("'", ""), key))


# ---------------------------------------------------------------- building the index (side effects)
def start_menu_dirs():
    dirs = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.is_dir()]


def _shortcut_exe(path):
    """The .exe a .lnk points at (for matching running processes), or ''."""
    try:
        import win32com.client

        target = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(path)).TargetPath or ""
    except Exception:
        return ""
    return os.path.basename(target).lower() if target.lower().endswith(".exe") else ""


def scan_shortcuts():
    apps = []
    for root in start_menu_dirs():
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".lnk", ".url") or JUNK.search(path.stem):
                continue
            apps.append({"name": path.stem, "kind": "shortcut", "target": str(path), "exe": _shortcut_exe(path)})
    return apps


def scan_store_apps():
    """Every Start-menu app, Store/UWP ones included: [{"Name": ..., "AppID": ...}] from PowerShell."""
    from jarvis.skills import system

    out = system.powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress", timeout=30)
    try:
        rows = json.loads(out.stdout or "[]")
    except ValueError:
        return []
    rows = [rows] if isinstance(rows, dict) else rows
    return [{"name": r["Name"], "kind": "store", "target": r["AppID"], "exe": ""}
            for r in rows if r.get("Name") and r.get("AppID") and not JUNK.search(r["Name"])]


def builtin_apps():
    from jarvis.skills import apps

    return [{"name": name, "kind": "builtin", "target": target, "exe": apps.PROCESSES.get(name, "").lower()}
            for name, target in apps.APPS.items()]


def merge(*groups):
    """One entry per app name; a shortcut (knows its .exe) wins, then a Store AppID, then the APPS map."""
    rank = {"shortcut": 0, "store": 1, "builtin": 2}
    merged = {}
    for entry in (e for group in groups for e in group):
        key = compact(entry["name"])
        if not key:
            continue
        old = merged.get(key)
        if old is None or rank[entry["kind"]] < rank[old["kind"]]:
            if old and not entry.get("exe"):
                entry = {**entry, "exe": old.get("exe", "")}
            merged[key] = entry
        elif entry.get("exe") and not old.get("exe"):
            old["exe"] = entry["exe"]
    return sorted(merged.values(), key=lambda e: e["name"].lower())


def build():
    apps = merge(scan_shortcuts(), scan_store_apps(), builtin_apps())
    data = {"built": datetime.now().isoformat(timespec="seconds"), "apps": apps}
    try:
        config.APP_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.APP_INDEX_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        log.warning("Couldn't save the app index", exc_info=True)
    log.info("[apps] indexed %d installed apps", len(apps))
    return data


def _stale(built):
    try:
        return datetime.now() - datetime.fromisoformat(built) > timedelta(hours=REFRESH_HOURS)
    except (TypeError, ValueError):
        return True


def load(refresh=False):
    """The app list: memory, then data/app_index.json, then a fresh scan (when asked, or a day old)."""
    with _cache_lock:
        if _cache["apps"] is not None and not refresh and not _stale(_cache["built"]):
            return _cache["apps"]
    with _build_lock:
        data = None
        if not refresh:
            try:
                data = json.loads(config.APP_INDEX_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if data and _stale(data.get("built")):
                data = None
        if data is None:
            data = build() if desktop.IS_WINDOWS else {"built": datetime.now().isoformat(), "apps": builtin_apps()}
        with _cache_lock:
            _cache.update(apps=data.get("apps") or [], built=data.get("built"))
        return _cache["apps"]


def reset_cache():
    with _cache_lock:
        _cache.update(apps=None, built=None)


# ---------------------------------------------------------------- fuzzy matching
def score(query, name):
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0.0
    if q == n or compact(q) == compact(n):
        return 1.0
    qw, nw = q.split(), n.split()
    cq, cn = compact(q), compact(n)
    best = 0.0
    if set(qw) <= set(nw):  # "visual studio" is in "visual studio code" and "visual studio 2022"
        best = 0.78 + 0.17 * len(qw) / len(nw)
    elif len(cq) >= 4 and cn.startswith(cq):
        best = 0.8
    elif len(qw) == 1 and len(q) >= 4 and any(w[0] == q[0] and difflib.SequenceMatcher(None, q, w).ratio() >= 0.88
                                              for w in nw):
        best = 0.76  # "spotfy" -> "Spotify Premium"
    # the whole-name comparison is slow (190 apps per command): only when its cheap upper bounds could matter
    matcher = difflib.SequenceMatcher(None, cq, cn)
    floor = max(best, 0.5)
    if matcher.real_quick_ratio() > floor and matcher.quick_ratio() > floor:
        best = max(best, matcher.ratio(), difflib.SequenceMatcher(None, q, n).ratio())
    return best


def match(query, apps=None):
    """[(score, entry)] best first, only those above MATCH_CUTOFF."""
    query = spoken_query(query)
    apps = load() if apps is None else apps
    ranked = sorted(((score(query, a["name"]), a) for a in apps), key=lambda pair: -pair[0])
    return [(s, a) for s, a in ranked if s >= MATCH_CUTOFF]


def candidates(query, apps=None):
    """The app to open, or several equally-close ones to ask about: (entry or None, [entries])."""
    ranked = match(query, apps)
    if not ranked:
        return None, []
    top = ranked[0][0]
    close = [a for s, a in ranked[:3] if top - s <= TIE_GAP]
    if top >= CLEAR_WIN and (len(close) == 1 or ranked[1][0] < CLEAR_WIN):
        return ranked[0][1], []
    if len(close) == 1:
        return ranked[0][1], []
    return None, close


ORDINAL = {"first": 0, "1": 0, "one": 0, "pehla": 0, "pehli": 0, "second": 1, "2": 1, "two": 1, "doosra": 1,
           "dusra": 1, "doosri": 1, "third": 2, "3": 2, "three": 2, "teesra": 2, "teesri": 2, "last": -1}


def pick(answer, options, label=lambda o: o["name"]):
    """Which of the options did the user mean? By position ("the second one") or by name."""
    answer = (answer or "").lower().strip(" .?!")
    if not answer:
        return None
    for word, index in ORDINAL.items():
        if re.search(rf"\b{word}\b", answer) and abs(index) <= len(options):
            return options[index]
    scored = sorted(((score(answer, label(o)), o) for o in options), key=lambda p: -p[0])
    if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def ask_which(ctx, options, label=lambda o: o["name"], what="one"):
    names = [label(o) for o in options]
    answer = ctx.ask(f"Which {what}: {join_and(names).replace(' and ', ' or ')}?")
    return pick(answer, options, label)


def launch(entry):
    """Start an indexed app."""
    from jarvis.skills import apps

    kind, target = entry["kind"], entry["target"]
    if kind == "store":
        desktop.start_app_id(target)
    elif kind == "shortcut":
        desktop.start(target)
    else:
        apps.launch(target)
    actions.record(f"Opened {entry['name']}", undo=lambda: close_windows(windows_for(entry["name"])),
                   undo_text=f"close {entry['name']}")


def open_app(ctx, query):
    """Open the best-matching installed app. Returns its name, or None if nothing in the index fits.
    Asks which one when 2-3 apps are equally close ("visual studio": VS Code or Visual Studio 2022?)."""
    entry, close = candidates(query)
    if entry is None and close:
        if ctx is None:
            entry = close[0]
        else:
            entry = ask_which(ctx, close)
            if entry is None:
                return ""  # didn't pick one: open nothing
    if entry is None:
        return None
    launch(entry)
    return entry["name"]


# ---------------------------------------------------------------- running apps
def friendly(window):
    """'Chrome', 'VS Code', or for Store apps the window title ('Calculator')."""
    if window.exe in FRIENDLY:
        return FRIENDLY[window.exe]
    if window.exe in ("applicationframehost.exe", ""):
        return window.title.split(" - ")[-1].strip() or window.title
    for entry in _cache["apps"] or ():
        if entry.get("exe") == window.exe:
            return entry["name"]
    return window.exe[:-4].capitalize() if window.exe.endswith(".exe") else window.title


def _exes_for(query):
    from jarvis.skills import apps

    key = spoken_query(query)
    exes = {compact(key) + ".exe"}
    for name in {key, query.lower().strip()}:
        if name in apps.PROCESSES:
            exes.add(apps.PROCESSES[name].lower())
    exes |= {exe for exe, label in FRIENDLY.items() if score(key, label) >= 0.9}
    for s, entry in match(key, _cache["apps"] or builtin_apps()):
        if s >= 0.85 and entry.get("exe"):
            exes.add(entry["exe"].lower())
    return exes


def _title_matches(query, title):
    q = compact(spoken_query(query))
    segments = [s for s in re.split(r"\s+[-—|]\s+", title) if s.strip()]
    return any(compact(s) == q or (len(q) >= 4 and difflib.SequenceMatcher(None, compact(s), q).ratio() >= 0.88)
               for s in segments[-2:])


def windows_for(query, windows=None):
    """The open windows that belong to the app `query`, front-most first."""
    windows = desktop.list_windows() if windows is None else windows
    exes = _exes_for(query)
    hits = [w for w in windows if w.exe in exes]
    return hits or [w for w in windows if _title_matches(query, w.title)]


def open_apps(windows=None):
    """{'Chrome': [windows], 'Notepad': [...]}, front-most app first."""
    grouped = {}
    for window in desktop.list_windows() if windows is None else windows:
        grouped.setdefault(friendly(window), []).append(window)
    return grouped


def needs_confirm(name, windows):
    """Might closing lose unsaved work? Always yes unless it's a known-safe app with no 'unsaved' marker."""
    label = norm(name)
    if any(DIRTY.search(w.title) for w in windows):
        return True
    return not any(label == norm(safe) or norm(friendly(w)) == norm(safe) for w in windows for safe in SAFE_TO_CLOSE)


def close_windows(windows):
    for window in windows:
        desktop.close(window.hwnd)
    return len(windows)


def wait_closed(windows, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(desktop.is_alive(w.hwnd) for w in windows):
            return True
        sleep(0.3)
    return False


def sleep(seconds):
    time.sleep(seconds)


def close_app(ctx, name):
    """Close every window of an app, asking first if it might have unsaved work. Returns what to say."""
    windows = windows_for(name)
    label = friendly(windows[0]) if windows else spoken_query(name)
    if not windows:
        return None
    if needs_confirm(label, windows):
        if not ctx.confirm(f"{label} might have unsaved work. Close it anyway?"):
            actions.note(f"Didn't close {label} (you said no)")
            return f"Okay, I'll leave {label} open."
    close_windows(windows)
    actions.record(f"Closed {label}" + (f" ({len(windows)} windows)" if len(windows) > 1 else ""),
                   undo=lambda: _reopen(label), undo_text=f"open {label} again")
    return label


def _reopen(label):
    from jarvis.skills import apps

    apps.open_target(label)


# ---------------------------------------------------------------- skills
@skill("refresh apps", r"^(?:please\s+)?(?:refresh|rescan|re-?index|update|rebuild)\s+(?:the\s+|my\s+)?(?:installed\s+)?"
                       r"(?:apps?|applications?|app list|app index)$",
       category="apps", help=["refresh apps"])
def refresh_apps(ctx, command, match):
    apps = load(refresh=True)
    return f"Done. I know {len(apps)} installed apps now."


@skill("is app open", r"^(?:is|are)\s+(?:the\s+|my\s+)?(.+?)\s+(?:open|running|on)(?:\s+right now)?\??$",
       rf"{hi.LEAD}(?:kya\s+)?(.+?)\s+(?:khula|khuli|chal)\s+(?:hai|raha hai|rahi hai)(?:\s+kya)?\??$",
       category="apps", help=["is Chrome open?"])
def is_open(ctx, command, match):
    name = match.group(1)
    if re.fullmatch(r"(?:it|this|that|wi-?fi|bluetooth|the wifi|airplane mode|dnd|do not disturb|dark mode)", name):
        return None
    if not desktop.IS_WINDOWS:
        return "Sorry, that only works on Windows."
    windows = windows_for(name)
    if not windows:
        return f"No, {spoken_query(name)} isn't open."
    label = friendly(windows[0])
    many = f", {len(windows)} windows" if len(windows) > 1 else ""
    return f"Yes, {label} is open{many}."


@skill("list open apps", r"\b(?:list|show|tell me|which|what)\b.*\b(?:open|running)\s+(?:apps|applications|programs|windows)\b",
       r"\bwhat(?:'s| is| all is)?\s+(?:open|running)(?:\s+(?:right now|on my laptop))?\??$",
       r"\b(?:apps|windows)\s+(?:are\s+)?open\??$", r"\bkaun\s*kaun\s*(?:se|si)?\s*(?:apps?|windows?)\s+khul",
       category="apps", help=["list open apps"])
def list_open(ctx, command, match):
    if not desktop.IS_WINDOWS:
        return "Sorry, that only works on Windows."
    grouped = open_apps()
    if not grouped:
        return "Nothing's open apart from me."
    names = [f"{name} ({len(ws)} windows)" if len(ws) > 1 else name for name, ws in grouped.items()]
    return speak_list(ctx, f"You have {len(names)} apps open:", names)


@skill("restart app", r"^(?:please\s+)?(?:restart|relaunch|reopen|reload)\s+(?:the\s+|my\s+)?(.+?)(?:\s+app)?$",
       rf"{hi.LEAD}(.+?)\s+(?:restart|dobara\s+(?:khol|chalu)){hi.KAR}$",
       category="apps", help=["restart Spotify"], params={"app": "app to restart, e.g. spotify"},
       command="restart {app}", risky=True, confirms=True)
def restart_app(ctx, command, match):
    from jarvis.skills import apps

    name = match.group(1).strip()
    if re.search(r"\b(?:laptop|pc|computer|system|machine|windows|jarvis|yourself|you|tab|it)\b", name):
        return None
    if not desktop.IS_WINDOWS:
        return "Sorry, that only works on Windows."
    windows = windows_for(name)
    if not windows:
        opened = apps.open_target(name, ctx)
        return f"{spoken_query(name).capitalize()} wasn't open, so I opened {opened}." if opened else ""
    label = friendly(windows[0])
    if needs_confirm(label, windows) and not ctx.confirm(f"{label} might have unsaved work. Restart it anyway?"):
        return f"Okay, I won't restart {label}."
    close_windows(windows)
    wait_closed(windows)
    apps.open_target(label, ctx)
    actions.record(f"Restarted {label}")
    return f"Restarted {label}."


@skill("close all apps", r"^(?:please\s+)?(?:close|quit|exit)\s+(?:all|every|everything|all (?:the\s+|my\s+)?(?:apps|"
                         r"applications|programs|windows))(?:\s+(?:apps|windows))?$",
       rf"{hi.LEAD}(?:saare|sare|sab)\s+(?:apps?|windows?|kuch)?{hi.CLOSE}$",
       category="apps", help=["close all apps"])
def close_all(ctx, command, match):
    if not desktop.IS_WINDOWS:
        return "Sorry, that only works on Windows."
    grouped = {name: ws for name, ws in open_apps().items() if not any(w.is_terminal for w in ws)}
    if not grouped:
        return "There's nothing open to close."
    names = list(grouped)
    if not ctx.confirm(f"Close {len(names)} apps: {join_and(names)}? Apps with unsaved work will ask you to save."):
        actions.note("Didn't close all apps (you said no)")
        return "Okay, I won't close anything."
    count = close_windows([w for ws in grouped.values() for w in ws])
    actions.record(f"Closed all apps ({join_and(names)}; {count} windows)")
    return f"Closing {join_and(names)}."


@on_startup
def _refresh_in_background(ctx):
    """Load (or rebuild) the app index off the main thread, then keep it fresh once a day."""
    if not desktop.IS_WINDOWS:
        return

    def loop():
        while True:
            try:
                load()
            except Exception:
                log.exception("[apps] couldn't build the app index")
            time.sleep(3600)

    threading.Thread(target=loop, name="app-index", daemon=True).start()


def list_installed():
    return [a["name"] for a in load()]


def running_process(name):
    """True if a process for the app is running even without a window (Spotify in the tray, say)."""
    import psutil

    exes = _exes_for(name)
    try:
        return any((p.info["name"] or "").lower() in exes for p in psutil.process_iter(["name"]))
    except Exception:
        return False
