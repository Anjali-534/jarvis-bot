"""Laptop control: apps index, windows, keyboard, files, settings, media, routines, the undo log, safety.

Every Windows API is faked (see conftest._no_real_laptop_control and the fixtures below): nothing on the real
desktop, file system outside tmp_path, registry, radios or audio is ever touched.
"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from helpers import make_ctx

import config
from jarvis import actions, desktop, router
from jarvis.desktop import Window
from jarvis.router import handle
from jarvis.skills import (app_index, apps, files, general, keyboard_control, media_session, routines,
                           settings_control, system)


# ---------------------------------------------------------------- fixtures
class Desk:
    """A fake desktop: windows, focus, keys, typing, clipboard, launches."""

    def __init__(self):
        self.windows = []
        self.active = None
        self.calls = []
        self.typed = []
        self.clipboard = None
        self.password = False

    def add(self, title, exe, hwnd=None):
        window = Window(hwnd or (len(self.windows) + 100), title, exe, 1)
        self.windows.append(window)
        if self.active is None:
            self.active = window
        return window


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop, "list_windows", lambda: [w for w in d.windows])
    monkeypatch.setattr(desktop, "active_window", lambda: d.active)
    monkeypatch.setattr(desktop, "focus", lambda h: d.calls.append(("focus", h)) or True)
    monkeypatch.setattr(desktop, "show", lambda h, how: d.calls.append((how, h)) or True)
    monkeypatch.setattr(desktop, "close", lambda h: d.calls.append(("close", h)) or True)
    monkeypatch.setattr(desktop, "is_alive", lambda h: False)
    monkeypatch.setattr(desktop, "monitor_count", lambda: 2)
    monkeypatch.setattr(desktop, "type_text", lambda text: d.typed.append(text))
    monkeypatch.setattr(desktop, "get_clipboard", lambda: d.clipboard)
    monkeypatch.setattr(desktop, "set_clipboard", lambda text: setattr(d, "clipboard", text) or True)
    monkeypatch.setattr(desktop, "focused_is_password", lambda: d.password)
    monkeypatch.setattr(desktop, "start", lambda target: d.calls.append(("start", str(target))))
    monkeypatch.setattr(desktop, "start_app_id", lambda app_id: d.calls.append(("app_id", app_id)))
    monkeypatch.setattr(system, "press", lambda key, presses=1: d.calls.append(("press", key, presses)))
    monkeypatch.setattr(system, "hotkey", lambda *keys: d.calls.append(("hotkey",) + keys))
    monkeypatch.setattr(apps, "launch", lambda target: d.calls.append(("launch", target)))
    monkeypatch.setattr(apps, "start_menu", lambda name: d.calls.append(("start menu", name)))
    monkeypatch.setattr(app_index, "running_process", lambda name: False)
    return d


INSTALLED = [
    {"name": "Visual Studio Code", "kind": "shortcut", "target": r"C:\Start\Visual Studio Code.lnk", "exe": "code.exe"},
    {"name": "Visual Studio 2022", "kind": "shortcut", "target": r"C:\Start\Visual Studio 2022.lnk", "exe": "devenv.exe"},
    {"name": "Spotify", "kind": "store", "target": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "exe": ""},
    {"name": "WhatsApp", "kind": "store", "target": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "exe": ""},
    {"name": "Figma", "kind": "shortcut", "target": r"C:\Start\Figma.lnk", "exe": "figma.exe"},
    {"name": "Notion", "kind": "shortcut", "target": r"C:\Start\Notion.lnk", "exe": "notion.exe"},
    {"name": "OBS Studio", "kind": "shortcut", "target": r"C:\Start\OBS Studio.lnk", "exe": "obs64.exe"},
]


@pytest.fixture
def installed(monkeypatch, desk):
    monkeypatch.setattr(app_index, "scan_shortcuts", lambda: [a for a in INSTALLED if a["kind"] == "shortcut"])
    monkeypatch.setattr(app_index, "scan_store_apps", lambda: [a for a in INSTALLED if a["kind"] == "store"])
    app_index.reset_cache()
    return INSTALLED


# ---------------------------------------------------------------- 1. every installed app
def test_index_merges_start_menu_store_and_apps_map_and_is_cached(installed):
    apps_list = app_index.load()
    names = {a["name"] for a in apps_list}
    assert {"Visual Studio Code", "Spotify", "WhatsApp", "notepad", "chrome"} <= names
    saved = json.loads(config.APP_INDEX_FILE.read_text(encoding="utf-8"))
    assert {a["name"] for a in saved["apps"]} == names
    # a day later it's rebuilt; before that the cache is used
    app_index.reset_cache()
    saved["built"] = (datetime.now() - timedelta(hours=25)).isoformat()
    saved["apps"] = [{"name": "Stale App", "kind": "builtin", "target": "x", "exe": ""}]
    config.APP_INDEX_FILE.write_text(json.dumps(saved), encoding="utf-8")
    assert "Stale App" not in {a["name"] for a in app_index.load()}


@pytest.mark.parametrize("said, expected", [
    ("spotify", "Spotify"), ("spotfy", "Spotify"), ("figma", "Figma"), ("obs", "OBS Studio"),
    ("notion app", "Notion"), ("what's app", "WhatsApp"), ("whats app", "WhatsApp"),
])
def test_fuzzy_app_matching(installed, said, expected):
    entry, close = app_index.candidates(said)
    assert entry is not None and entry["name"] == expected, (said, close)


def test_two_close_matches_ask_which_one(installed, desk):
    ctx = make_ctx(["the second one"])
    assert handle(ctx, "open visual studio") == "open"
    assert "Which one" in ctx.voice.said[0] and "Visual Studio 2022" in ctx.voice.said[0]
    picked = [c for c in desk.calls if c[0] == "start"]
    assert len(picked) == 1 and "Visual Studio" in picked[0][1]


def test_store_apps_launch_through_apps_folder(installed, desk):
    assert handle(make_ctx(), "open whats app") == "open"
    assert ("launch", "whatsapp:") in desk.calls  # the APPS map knows WhatsApp's URI
    desk.calls.clear()
    handle(make_ctx(), "open spotfy")
    assert desk.calls == [("app_id", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")]


def test_start_menu_typing_is_the_last_resort(installed, desk):
    handle(make_ctx(), "open some unknown thing")
    assert desk.calls == [("start menu", "some unknown thing")]
    desk.calls.clear()
    handle(make_ctx(), "open figma")
    assert desk.calls == [("start", r"C:\Start\Figma.lnk")]


def test_is_open_and_list_open_apps(installed, desk):
    desk.add("Inbox - Gmail - Google Chrome", "chrome.exe")
    desk.add("notes.txt - Notepad", "notepad.exe")
    desk.add("Calculator", "applicationframehost.exe")
    assert handle(make_ctx(), "is chrome open") == "is app open"
    assert handle(make_ctx(), "is spotify open") == "is app open"
    ctx = make_ctx()
    handle(ctx, "is chrome open")
    assert ctx.voice.last == "Yes, Chrome is open."
    handle(ctx, "is spotify running")
    assert ctx.voice.last == "No, spotify isn't open."
    handle(ctx, "list open apps")
    assert ctx.voice.last == "You have 3 apps open: Chrome, Notepad and Calculator."


def test_close_asks_when_there_may_be_unsaved_work_and_no_does_nothing(installed, desk):
    notepad = desk.add("*draft.txt - Notepad", "notepad.exe")
    ctx = make_ctx(["no"])
    handle(ctx, "close notepad")
    assert "unsaved work" in ctx.voice.said[0]
    assert ("close", notepad.hwnd) not in desk.calls
    ctx = make_ctx(["yes"])
    handle(ctx, "close notepad")
    assert ("close", notepad.hwnd) in desk.calls  # WM_CLOSE: Notepad still asks to save


def test_closing_a_browser_needs_no_question(installed, desk):
    chrome = desk.add("YouTube - Google Chrome", "chrome.exe")
    ctx = make_ctx()
    handle(ctx, "close chrome")
    assert ctx.voice.said == ["Closed Chrome."] and ("close", chrome.hwnd) in desk.calls


def test_restart_app_closes_then_opens(installed, desk):
    desk.add("Spotify Premium", "spotify.exe")
    ctx = make_ctx()
    assert handle(ctx, "restart spotify") == "restart app"
    assert desk.calls[0][0] == "close" and desk.calls[-1] == ("launch", "spotify:")


def test_close_all_apps_confirms_and_skips_terminals(installed, desk):
    desk.add("Chrome", "chrome.exe")
    terminal = desk.add("PowerShell", "windowsterminal.exe")
    ctx = make_ctx(["no"])
    handle(ctx, "close all apps")
    assert not [c for c in desk.calls if c[0] == "close"]
    handle(make_ctx(["yes"]), "close all apps")
    assert ("close", terminal.hwnd) not in desk.calls and len([c for c in desk.calls if c[0] == "close"]) == 1


# ---------------------------------------------------------------- 2. windows
def test_switch_minimize_snap_and_desktops(installed, desk):
    notepad = desk.add("notes.txt - Notepad", "notepad.exe")
    chrome = desk.add("GitHub - Google Chrome", "chrome.exe")
    assert handle(make_ctx(), "switch to chrome") == "switch window"
    assert desk.calls[-1] == ("focus", chrome.hwnd)
    handle(make_ctx(), "minimize notepad")
    assert desk.calls[-1] == ("minimize", notepad.hwnd)
    handle(make_ctx(), "maximize this window")
    assert desk.calls[-1] == ("maximize", notepad.hwnd)  # the active one
    handle(make_ctx(), "snap chrome left")
    assert desk.calls[-2:] == [("focus", chrome.hwnd), ("hotkey", "win", "left")]
    handle(make_ctx(), "minimize everything")
    assert desk.calls[-1] == ("hotkey", "win", "d")
    handle(make_ctx(), "full screen")
    assert desk.calls[-1] == ("press", "f11", 1)
    handle(make_ctx(), "move this to the other screen")
    assert desk.calls[-1] == ("hotkey", "win", "shift", "right")
    handle(make_ctx(), "new desktop")
    assert desk.calls[-1] == ("hotkey", "win", "ctrl", "d")
    handle(make_ctx(), "next desktop")
    assert desk.calls[-1] == ("hotkey", "win", "ctrl", "right")
    handle(make_ctx(), "previous desktop")
    assert desk.calls[-1] == ("hotkey", "win", "ctrl", "left")


def test_whats_on_my_screen(desk):
    desk.add("Inbox (3) - Gmail - Google Chrome", "chrome.exe")
    ctx = make_ctx()
    handle(ctx, "what's on my screen right now")
    assert ctx.voice.last == "Chrome is in front, showing Inbox (3) - Gmail."


def test_close_this_window_asks_for_editors(desk):
    word = desk.add("Report.docx - Word", "winword.exe")
    ctx = make_ctx(["no"])
    handle(ctx, "close this window")
    assert "unsaved work" in ctx.voice.said[0] and ("close", word.hwnd) not in desk.calls


def test_switch_to_an_app_that_isnt_open_offers_to_open_it(installed, desk):
    ctx = make_ctx(["no"])
    handle(ctx, "switch to spotify")
    assert ctx.voice.said == ["Spotify isn't open. Open it?", "Spotify isn't open."]
    assert not [c for c in desk.calls if c[0] in ("app_id", "launch")]


# ---------------------------------------------------------------- 3. typing, keys, clipboard
def test_type_keeps_capitals_and_dictated_punctuation(desk):
    desk.add("Untitled - Notepad", "notepad.exe")
    handle(make_ctx(), "type Hello Riya comma see you at 5 full stop")
    assert desk.typed == ["Hello Riya, see you at 5."]


def test_never_types_into_a_terminal_unless_told(desk):
    desk.add("PowerShell", "windowsterminal.exe")
    ctx = make_ctx()
    handle(ctx, "type git status")
    assert desk.typed == [] and "terminal" in ctx.voice.last
    handle(make_ctx(), "type in terminal git status")
    assert desk.typed == ["git status"]
    handle(make_ctx(), "press enter")
    assert ("press", "enter", 1) not in desk.calls  # Enter in a terminal could run something
    handle(make_ctx(), "press enter in terminal")
    assert ("press", "enter", 1) in desk.calls


def test_password_box_and_long_text_ask_first(desk):
    desk.add("Sign in - Google Chrome", "chrome.exe")
    desk.password = True
    ctx = make_ctx(["no"])
    handle(ctx, "type hunter2")
    assert "password" in ctx.voice.said[0] and desk.typed == []
    desk.password = False
    long_text = "word " * 50
    ctx = make_ctx(["no"])
    handle(ctx, "type " + long_text)
    assert "characters" in ctx.voice.said[0] and desk.typed == []
    handle(make_ctx(["yes"]), "type " + long_text)
    assert len(desk.typed) == 1


def test_password_typing_is_not_logged(desk):
    desk.add("Sign in", "chrome.exe")
    desk.password = True
    handle(make_ctx(["yes"]), "type supersecret123")
    log_text = actions.log_path().read_text(encoding="utf-8")
    assert "supersecret123" not in log_text and "password box" in log_text


def test_banking_sites_are_off_limits(desk):
    desk.add("HDFC Bank NetBanking - Google Chrome", "chrome.exe")
    ctx = make_ctx()
    handle(ctx, "type 12345")
    assert desk.typed == [] and "banking" in ctx.voice.last
    handle(ctx, "paste")
    assert not [c for c in desk.calls if c[0] == "hotkey"]


@pytest.mark.parametrize("said, keys", [
    ("select all", ("ctrl", "a")), ("copy", ("ctrl", "c")), ("paste", ("ctrl", "v")), ("undo", ("ctrl", "z")),
    ("save", ("ctrl", "s")), ("new tab", ("ctrl", "t")), ("close tab", ("ctrl", "w")),
    ("reopen tab", ("ctrl", "shift", "t")), ("go back", ("alt", "left")), ("refresh", ("f5",)),
    ("zoom in", ("ctrl", "=")), ("zoom out", ("ctrl", "-")),
])
def test_shortcuts(desk, said, keys):
    desk.add("Docs - Google Chrome", "chrome.exe")
    handle(make_ctx(), said)
    assert desk.calls[-1] == ("hotkey",) + keys


def test_press_and_scroll(desk, monkeypatch):
    desk.add("Docs", "chrome.exe")
    scrolled = []
    monkeypatch.setattr(keyboard_control, "scroll_by", lambda d, n: scrolled.append((d, n)))
    handle(make_ctx(), "press escape")
    handle(make_ctx(), "press tab 3 times")
    handle(make_ctx(), "scroll down")
    assert desk.calls[-2:] == [("press", "esc", 1), ("press", "tab", 3)] and scrolled == [("down", 6)]


def test_read_clipboard_two_sentences_and_never_secrets(desk):
    desk.clipboard = "First sentence here. Second one. Third should not be read."
    ctx = make_ctx()
    handle(ctx, "read my clipboard")
    assert ctx.voice.last == "Your clipboard says: First sentence here. Second one."
    for secret in ("Tr0ub4dor&3x!", "sk-abcdefghijklmnopqrstuvwx", "482913"):  # fake values. secret-check: ignore
        desk.clipboard = secret
        handle(ctx, "read my clipboard")
        assert "won't read it out" in ctx.voice.last


def test_copy_that_copies_the_last_answer(desk):
    ctx = make_ctx()
    handle(ctx, "what is 25 times 4")
    handle(ctx, "copy that")
    assert desk.clipboard == "25 times 4 is 100." and ctx.voice.last == "Copied."


# ---------------------------------------------------------------- 4. files
def make_tree(root):
    now = time.time()
    layout = {
        "Documents/Resume_Anjali_2026.pdf": 2, "Documents/old resume draft.docx": 400,
        "Documents/Bogie Pitch Deck.pptx": 3, "Documents/bogie pitch deck notes.txt": 3,
        "Downloads/invoice-nov.pdf": 0.1, "Downloads/setup.exe": 0.05, "Downloads/photo.jpg": 30,
        "Desktop/projects/jarvis/main.py": 1, "Desktop/projects/jarvis/node_modules/left-pad/index.js": 1,
        "Desktop/projects/jarvis/.git/HEAD": 1, "Desktop/projects/jarvis/.venv/pyvenv.cfg": 1,
        "Desktop/projects/env2/pyvenv.cfg": 1, "Desktop/projects/env2/lib.py": 1,
    }
    for rel, days in layout.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("never read", encoding="utf-8")
        stamp = now - days * 86400
        os.utime(path, (stamp, stamp))
    return {name: root / name for name in ("Desktop", "Documents", "Downloads")}


def as_if_on_d_drive(monkeypatch, tmp_path):
    """pytest's tmp_path lives in AppData, which the guard (rightly) refuses: judge tmp files as if on D:."""
    base = os.path.normcase(str(tmp_path))

    def moved(path):
        text = os.path.normcase(os.path.abspath(str(path)))
        return "D:\\testhome" + text[len(base):] if text.startswith(base) else path

    real_protected, real_system = actions.is_protected, actions.is_system
    monkeypatch.setattr(actions, "is_protected", lambda path: real_protected(moved(path)))
    monkeypatch.setattr(actions, "is_system", lambda path: real_system(moved(path)))


@pytest.fixture
def home(monkeypatch, tmp_path, desk):
    as_if_on_d_drive(monkeypatch, tmp_path)
    root = tmp_path / "home"
    folders = make_tree(root)
    monkeypatch.setattr(files, "known_folders", lambda: folders)
    monkeypatch.setattr(files, "index_roots", lambda: list(folders.values()))
    opened, trashed, restored = [], [], []
    monkeypatch.setattr(files, "open_path", lambda p: opened.append(Path(p).name))
    monkeypatch.setattr(files, "trash", lambda p: trashed.append(str(p)) or Path(p).rename(Path(str(p) + ".trashed")))
    monkeypatch.setattr(files, "restore_from_bin", lambda p: restored.append(str(p)) or
                        Path(str(p) + ".trashed").rename(p))
    files.reset_index()
    return {"root": root, "opened": opened, "trashed": trashed, "restored": restored, **folders}


def test_index_skips_node_modules_git_and_venvs_and_never_reads_contents(home, monkeypatch):
    def no_reading(*args, **kwargs):
        raise AssertionError("the index must never open files")

    monkeypatch.setattr("builtins.open", no_reading)
    names = {Path(e.path).name for e in files.scan(list(files.index_roots()))}
    assert {"main.py", "Resume_Anjali_2026.pdf"} <= names
    assert not names & {"index.js", "HEAD", "pyvenv.cfg", "lib.py", "node_modules", ".git", ".venv", "env2"}


def test_index_refuses_system_folders(tmp_path, monkeypatch):
    as_if_on_d_drive(monkeypatch, tmp_path)
    windows = tmp_path / "Windows"
    (windows / "System32").mkdir(parents=True)
    (windows / "System32" / "kernel32.dll").write_text("x")
    (tmp_path / "AppData" / "Roaming").mkdir(parents=True)
    (tmp_path / "AppData" / "Roaming" / "secret.json").write_text("x")
    (tmp_path / "mine.txt").write_text("x")
    monkeypatch.setenv("SystemRoot", "D:\\testhome\\Windows")
    names = {Path(e.path).name for e in files.scan([tmp_path])}
    assert names == {"mine.txt"}  # nothing from Windows or AppData, not even the folder names


def test_ranking_prefers_name_type_and_recency(home):
    ranked = files.rank("the bogie pitch deck")
    assert ranked[0][1].name == "Bogie Pitch Deck.pptx"
    ranked = files.rank("my resume")
    assert ranked[0][1].name == "Resume_Anjali_2026.pdf"  # newer, and a document
    assert files.rank("cv")[0][1].name == "Resume_Anjali_2026.pdf"  # cv = resume
    assert files.rank("invoice")[0][1].name == "invoice-nov.pdf"


def test_open_my_resume_and_hinglish(home):
    ctx = make_ctx(["the first one"])
    assert handle(ctx, "open my resume") == "open file"
    assert home["opened"] == ["Resume_Anjali_2026.pdf"]
    assert handle(make_ctx(["first"]), "meri resume khol") == "open file"
    assert home["opened"][-1] == "Resume_Anjali_2026.pdf"


def test_several_matches_read_top_3_and_ask(home):
    ctx = make_ctx(["none"])
    handle(ctx, "open my resume")
    assert "1, Resume_Anjali_2026.pdf in Documents" in ctx.voice.said[0]
    assert "2, old resume draft.docx in Documents" in ctx.voice.said[0]
    assert home["opened"] == []


def test_find_offers_to_open(home):
    ctx = make_ctx(["yes"])
    assert handle(ctx, "find the bogie pitch deck") == "find file"
    assert ctx.voice.said[0].startswith("Found Bogie Pitch Deck.pptx in Documents, changed 3 days ago")
    assert home["opened"] == ["Bogie Pitch Deck.pptx"]


def test_latest_download_and_programs_ask_first(home):
    ctx = make_ctx(["no"])
    handle(ctx, "open the latest download")
    assert "program" in ctx.voice.said[0] and home["opened"] == []  # setup.exe is newest
    os.utime(home["Downloads"] / "setup.exe", (time.time() - 99999, time.time() - 99999))
    handle(make_ctx(), "open the latest download")
    assert home["opened"] == ["invoice-nov.pdf"]


def test_downloaded_today_and_open_folder(home):
    ctx = make_ctx()
    handle(ctx, "show files I downloaded today")
    assert ctx.voice.last == "You downloaded 2 files today: setup.exe and invoice-nov.pdf."
    handle(ctx, "open my downloads folder")
    assert home["opened"][-1] == "Downloads"


def test_delete_notes_txt_is_a_file_not_my_notes(home):
    (home["Documents"] / "notes.txt").write_text("x")
    files.reset_index()
    ctx = make_ctx(["no"])
    assert handle(ctx, "delete notes.txt") == "delete file"
    assert "Recycle Bin" in ctx.voice.said[0]


def test_delete_goes_to_recycle_bin_only_after_yes_and_undo_restores(home):
    ctx = make_ctx(["no"])
    handle(ctx, "delete invoice-nov.pdf")
    assert "Recycle Bin" in ctx.voice.said[0] and home["trashed"] == []
    ctx = make_ctx(["yes"])
    handle(ctx, "delete invoice-nov.pdf")
    assert len(home["trashed"]) == 1 and not (home["Downloads"] / "invoice-nov.pdf").exists()
    ctx = make_ctx(["yes"])
    assert handle(ctx, "undo that") == "undo that"
    assert (home["Downloads"] / "invoice-nov.pdf").exists() and len(home["restored"]) == 1


@pytest.mark.parametrize("command", [
    r"delete c:\windows\system32\drivers\etc\hosts", r"delete c:\program files\app\app.exe",
    r"rename c:\users\admin\appdata\local\thing.txt to x.txt", r"move c:\windows\win.ini to documents",
])
def test_system_folders_are_refused(home, command, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "stat", lambda self, **k: os.stat_result((0,) * 10))
    ctx = make_ctx(["yes", "yes", "yes"])
    handle(ctx, command)
    assert "system folders" in ctx.voice.said[0]
    assert home["trashed"] == [] and len(ctx.voice.said) == 1  # never even asked
    assert "Refused" in actions.log_path().read_text(encoding="utf-8")


def test_protected_paths():
    assert actions.is_protected(r"C:\Windows\System32")
    assert actions.is_protected(r"C:\Program Files (x86)\Steam")
    assert actions.is_protected(Path.home() / "AppData" / "Roaming" / "x")
    assert actions.is_protected("C:\\") and actions.is_protected(Path.home())
    assert actions.is_protected(config.BASE_DIR)
    assert not actions.is_protected(Path.home() / "Documents" / "resume.pdf")


def test_move_rename_zip_create_and_undo_each(home):
    handle(make_ctx(["yes"]), "move invoice-nov.pdf to documents")
    assert (home["Documents"] / "invoice-nov.pdf").exists()
    handle(make_ctx(["yes"]), "undo that")
    assert (home["Downloads"] / "invoice-nov.pdf").exists()

    handle(make_ctx(["yes"]), "rename invoice-nov.pdf to november invoice")
    assert (home["Downloads"] / "november invoice.pdf").exists()  # keeps .pdf
    handle(make_ctx(["yes"]), "undo that")
    assert (home["Downloads"] / "invoice-nov.pdf").exists()

    handle(make_ctx(["yes"]), "create folder Receipts on desktop")
    assert (home["Desktop"] / "Receipts").is_dir()
    handle(make_ctx(["yes"]), "undo that")
    assert not (home["Desktop"] / "Receipts").exists()

    handle(make_ctx(["yes"]), "zip the jarvis folder")
    made = home["Desktop"] / "projects" / "jarvis.zip"
    assert made.exists()
    handle(make_ctx(["yes"]), "undo that")
    assert home["trashed"] == [str(made)]


def test_file_changes_with_no_do_nothing(home):
    for command in ("move invoice-nov.pdf to documents", "rename invoice-nov.pdf to x", "zip invoice-nov.pdf",
                    "create folder Junk on desktop"):
        ctx = make_ctx(["no"])
        handle(ctx, command)
        assert ctx.voice.last.startswith("Okay, I didn't"), command
    assert (home["Downloads"] / "invoice-nov.pdf").exists() and not (home["Desktop"] / "Junk").exists()
    assert not list(home["Downloads"].glob("*.zip"))


# ---------------------------------------------------------------- 5. settings
def test_dark_mode_and_back(desk, monkeypatch):
    theme = {"AppsUseLightTheme": 1, "SystemUsesLightTheme": 1}
    monkeypatch.setattr(settings_control, "get_theme", lambda: dict(theme))
    monkeypatch.setattr(settings_control, "set_theme", lambda values: theme.update(values) or True)
    ctx = make_ctx()
    assert handle(ctx, "dark mode on") == "dark mode"
    assert theme == {"AppsUseLightTheme": 0, "SystemUsesLightTheme": 0} and ctx.voice.last == "Dark mode is on."
    handle(ctx, "dark mode on")
    assert ctx.voice.last == "It's already in dark mode."
    handle(make_ctx(["yes"]), "undo that")
    assert theme == {"AppsUseLightTheme": 1, "SystemUsesLightTheme": 1}
    handle(ctx, "turn off light mode")
    assert theme["AppsUseLightTheme"] == 0


def test_wifi_off_warns_that_speech_needs_internet(desk, monkeypatch):
    radios = {"wifi": True, "bluetooth": False}
    monkeypatch.setattr(settings_control, "radio_states", lambda: dict(radios))
    monkeypatch.setattr(settings_control, "set_radio", lambda kind, on: radios.update({kind: on}) or True)
    monkeypatch.setattr(config, "STT_ENGINE", "google")
    ctx = make_ctx(["no"])
    assert handle(ctx, "yaar wifi band kar de") == "wifi"
    assert "can't understand speech" in ctx.voice.said[0] and radios["wifi"] is True
    handle(make_ctx(), "turn on bluetooth")
    assert radios["bluetooth"] is True
    ctx = make_ctx()
    handle(ctx, "is bluetooth on")
    assert ctx.voice.last == "Bluetooth is on."


def test_app_volume_and_mute(desk, monkeypatch):
    class Session:
        def __init__(self):
            self.level, self.muted = 100, False

    chrome = Session()
    monkeypatch.setattr(settings_control, "audio_sessions", lambda: [("chrome.exe", chrome)])
    monkeypatch.setattr(settings_control, "set_app_volume", lambda s, v: setattr(s, "level", v))
    monkeypatch.setattr(settings_control, "get_app_volume", lambda s: s.level)
    monkeypatch.setattr(settings_control, "set_app_mute", lambda s, m: setattr(s, "muted", m))
    handle(make_ctx(), "chrome volume 30")
    assert chrome.level == 30
    handle(make_ctx(), "mute chrome")
    assert chrome.muted is True
    ctx = make_ctx()
    handle(ctx, "mute spotify")  # not playing here: the Spotify skill gets a go
    assert "spotify" not in ctx.voice.last.lower() or "isn't" in ctx.voice.last


def test_master_volume(desk, monkeypatch):
    level = {"now": 50}
    monkeypatch.setattr(settings_control, "get_master", lambda: level["now"])
    monkeypatch.setattr(settings_control, "set_master", lambda v: level.update(now=v))
    assert handle(make_ctx(), "volume 30") == "set volume"
    assert level["now"] == 30
    handle(make_ctx(["yes"]), "undo that")
    assert level["now"] == 50


def test_settings_pages(desk, monkeypatch):
    pages = []
    monkeypatch.setattr(settings_control, "open_settings_page", pages.append)
    for said in ("open display settings", "open bluetooth settings", "open windows update settings",
                 "open privacy settings", "open battery settings"):
        handle(make_ctx(), said)
    assert pages == ["display", "bluetooth", "windowsupdate", "privacy", "batterysaver"]


def test_recycle_bin_and_temp_files_need_a_yes(desk, monkeypatch):
    emptied, cleared = [], []
    monkeypatch.setattr(settings_control, "empty_bin", lambda: emptied.append(1) or True)
    monkeypatch.setattr(settings_control, "clear_temp", lambda: cleared.append(1) or (3, 1024 ** 2))
    monkeypatch.setattr(settings_control, "user_temp", lambda: Path("C:/Users/x/AppData/Local/Temp"))
    handle(make_ctx(["no"]), "empty the recycle bin")
    handle(make_ctx(["no"]), "clear temp files")
    assert emptied == [] and cleared == []
    handle(make_ctx(["yes"]), "empty the recycle bin")
    assert emptied == [1]


def test_mic_mute_warns_first(desk, monkeypatch):
    muted = []
    monkeypatch.setattr(settings_control, "set_mic_mute", lambda m: muted.append(m) or True)
    ctx = make_ctx(["no"])
    handle(ctx, "mute my mic")
    assert "can't hear you" in ctx.voice.said[0] and muted == []


# ---------------------------------------------------------------- 6. media
@pytest.fixture
def media(monkeypatch, desk):
    playing = {"app": "Chrome", "title": "Lofi beats to study to", "artist": "Lofi Girl", "playing": True,
               "session": "chrome", "current": True}
    paused = {"app": "Spotify", "title": "Kesariya", "artist": "Arijit Singh", "playing": False,
              "session": "spotify", "current": False}
    found = [playing, paused]
    calls = []
    monkeypatch.setattr(media_session, "sessions", lambda: list(found))
    monkeypatch.setattr(media_session, "_call", lambda session, action: calls.append((session, action)) or True)
    return {"found": found, "calls": calls, "desk": desk}


def test_whats_playing_in_any_app(media):
    ctx = make_ctx()
    assert handle(ctx, "what's playing") == "now playing"
    assert ctx.voice.last == "Playing Lofi beats to study to by Lofi Girl on Chrome."
    media["found"][0]["playing"] = False
    handle(ctx, "what's playing")
    assert ctx.voice.last == "Nothing's playing. Lofi beats to study to by Lofi Girl is paused on Chrome."


def test_media_controls_use_the_session_then_keys(media):
    handle(make_ctx(), "pause")
    handle(make_ctx(), "next song")
    assert media["calls"] == [("chrome", "pause"), ("chrome", "next")]
    media["found"].clear()
    handle(make_ctx(), "previous song")
    assert media["desk"].calls[-1] == ("press", "prevtrack", 1)


# ---------------------------------------------------------------- 7. routines
@pytest.fixture
def recorder(monkeypatch):
    """Every skill except the routine ones just records the command it got."""
    ran = []
    keep = {"run routine", "create routine", "list routines", "delete routine", "schedule routine", "cancel schedule",
            "undo that", "what did you do", "open file", "find file"}  # these two fall through when it's not a file
    for sk in router.SKILLS:
        if sk.name not in keep:
            monkeypatch.setattr(sk, "handler", lambda ctx, cmd, m, name=sk.name: ran.append((name, cmd)) or "")
    return ran


def test_example_file_is_copied_and_has_the_built_ins():
    data = routines.load()
    assert {"work mode", "break time", "movie mode", "shutdown routine"} <= set(data["routines"])
    assert config.ROUTINES_FILE.exists()


def test_routine_runs_its_steps_in_order(recorder, monkeypatch):
    waited = []
    monkeypatch.setattr(routines, "sleep", waited.append)
    ctx = make_ctx()
    assert handle(ctx, "start movie mode") == "run routine"
    assert recorder == [("dark mode", "dark mode on"), ("brightness", "brightness 40"), ("set volume", "volume 70"),
                        ("open", "open netflix")]
    assert ctx.voice.said[0] == "Starting movie mode." and ctx.voice.last == "Movie mode done."
    recorder.clear()
    handle(make_ctx(), "work mode")
    assert [c for _, c in recorder] == routines.steps_of(routines.load()["routines"]["work mode"])
    recorder.clear()
    handle(make_ctx(), "start the shutdown routine")
    assert waited == [10.0] and [n for n, _ in recorder] == ["close all apps", "power"]


def test_aliases_and_not_a_routine(recorder):
    assert handle(make_ctx(), "start break") == "run routine"
    assert handle(make_ctx(), "start chrome") == "open"  # not a routine: falls through to apps


def test_create_routine_step_by_step(recorder):
    ctx = make_ctx(["open notepad", "blah blah unknown", "open calculator", "wait 3 seconds", "done", "yes"])
    handle(ctx, "create a routine called test time")
    assert "I don't know that command" in ctx.voice.said[2]
    assert routines.load()["routines"]["test time"]["steps"] == ["open notepad", "open calculator", {"wait": 3}]
    handle(make_ctx(), "start test time")
    assert [c for _, c in recorder] == ["open notepad", "open calculator"]


def test_delete_routine_confirms(recorder):
    handle(make_ctx(["no"]), "delete routine movie mode")
    assert "movie mode" in routines.load()["routines"]
    handle(make_ctx(["yes"]), "delete the movie mode routine")
    assert "movie mode" not in routines.load()["routines"]
    handle(make_ctx(["yes"]), "undo that")
    assert "movie mode" in routines.load()["routines"]


def test_schedule_and_unattended_runs_never_say_yes(recorder, monkeypatch):
    ctx = make_ctx(["yes"])
    handle(ctx, "every weekday at 9:30 start work mode")
    schedule = routines.load()["schedules"][0]
    assert schedule == {"routine": "work mode", "days": ["mon", "tue", "wed", "thu", "fri"], "time": "09:30"}
    monday = datetime(2026, 11, 23, 9, 30, 20)
    assert routines.due(schedule, monday, set())
    assert not routines.due(schedule, datetime(2026, 11, 28, 9, 30), set())  # Saturday
    assert not routines.due(schedule, monday + timedelta(minutes=5), set())
    started = routines.tick(ctx, monday)
    assert started == ["work mode"] and routines.tick(ctx, monday + timedelta(seconds=30)) == []  # once a day

    # a scheduled shutdown routine: nobody is there to say yes, so nothing risky runs
    asked = []

    def asks(ctx, cmd, m):
        asked.append(ctx.confirm("Shut down?"))
        return "Okay, I won't shut down."

    power = next(s for s in router.SKILLS if s.name == "power")
    monkeypatch.setattr(power, "handler", asks)
    reply = routines.run(make_ctx(), "shutdown routine", unattended=True)
    assert asked == [False] and "skipped the steps that needed your yes" in reply


def test_parse_time_and_days():
    assert routines.parse_time("9:30") == "09:30" and routines.parse_time("6") == "18:00"
    assert routines.parse_time("7 am") == "07:00" and routines.parse_time("10 pm") == "22:00"
    assert routines.parse_days("weekends") == ["sat", "sun"]
    assert routines.parse_days("monday and friday") == ["mon", "fri"]


# ---------------------------------------------------------------- 8. the action log and undo
def test_actions_are_logged_and_what_did_you_do(desk):
    chrome = desk.add("GitHub - Google Chrome", "chrome.exe")
    handle(make_ctx(), "switch to chrome")
    handle(make_ctx(), "minimize chrome")
    text = actions.log_path().read_text(encoding="utf-8")
    assert "Switched to Chrome" in text and "Minimized Chrome" in text
    ctx = make_ctx()
    handle(ctx, "what did you just do")
    assert ctx.voice.last == "Last, I minimized Chrome. Before that: switched to Chrome."
    handle(make_ctx(["yes"]), "undo that")
    assert desk.calls[-1] == ("restore", chrome.hwnd)


def test_undo_with_no_does_nothing_and_nothing_to_undo(desk):
    ctx = make_ctx()
    handle(ctx, "undo that")
    assert ctx.voice.last == "There's nothing I can undo."
    chrome = desk.add("Chrome", "chrome.exe")
    handle(make_ctx(), "minimize chrome")
    ctx = make_ctx(["no"])
    handle(ctx, "undo that")
    assert ctx.voice.last == "Okay, I left it." and desk.calls[-1] == ("minimize", chrome.hwnd)


# ---------------------------------------------------------------- 9. natural language and help
@pytest.mark.parametrize("phrase, expected", [
    ("yaar wifi band kar de", "wifi"), ("wifi on kar do", "wifi"), ("chrome pe switch kar", "switch window"),
    ("bluetooth chalu karo", "bluetooth"), ("dark mode laga do", "dark mode"), ("chrome band kar do", "close"),
    ("laptop band kar do yaar", "power"), ("notepad khol do", "open"), ("gaana roko", "play pause"),
    ("agla gaana", "next track"), ("downloads folder khol", "open folder"),
    ("switch to headphones", "audio output"), ("switch to chrome", "switch window"), ("switch to dark mode", "dark mode"),
    ("switch to hindi", "language"), ("open display settings", "open settings page"), ("refresh apps", "refresh apps"),
    ("what apps are open", "list open apps"), ("show desktop", "show desktop"), ("new tab", "shortcut"),
    ("copy that", "copy answer"), ("undo", "shortcut"), ("undo that", "undo that"), ("type hello", "type text"),
    ("press enter", "press key"), ("delete the old invoice.pdf file", "delete file"),
    ("move resume to documents", "move file"), ("move this to the other screen", "other screen"),
    ("list my routines", "list routines"), ("every weekday at 9:30 start work mode", "schedule routine"),
    ("empty the recycle bin", "empty recycle bin"), ("battery saver on", "battery saver"),
    ("best performance mode", "power mode"), ("do not disturb on", "do not disturb"), ("mute chrome", "app volume"),
    ("what's on my screen right now", "whats on screen"), ("is chrome open", "is app open"),
    ("read my clipboard", "read clipboard"), ("restart spotify", "restart app"), ("full screen", "full screen"),
    # the old ones still go where they did
    ("restart in 5 minutes", "power"), ("mute", "mute"), ("volume up by 20", "volume up"), ("open chrome", "open"),
    ("close this tab", "shortcut"), ("go to sleep", "sleep"), ("what's the weather", "weather"),
])
def test_routing(recorded_skills, phrase, expected):
    assert handle(make_ctx(), phrase) == expected


@pytest.mark.parametrize("hindi, english, skill_name", [
    ("वाईफाई बंद करो", "close wifi", "wifi"), ("ब्लूटूथ चालू करो", "turn on bluetooth", "bluetooth"),
    ("डार्क मोड चालू करो", "turn on dark mode", "dark mode"),
])
def test_hindi_mode_settings(recorded_skills, hindi, english, skill_name):
    from jarvis import translit

    assert translit.normalize_command(hindi) == english
    assert handle(make_ctx(), hindi) == skill_name


def test_close_wifi_means_off(desk, monkeypatch):
    radios = {"wifi": True}
    monkeypatch.setattr(settings_control, "radio_states", lambda: dict(radios))
    monkeypatch.setattr(settings_control, "set_radio", lambda kind, on: radios.update({kind: on}) or True)
    monkeypatch.setattr(config, "STT_ENGINE", "whisper")  # offline speech: no warning needed
    handle(make_ctx(), "close wifi")
    assert radios["wifi"] is False


def test_new_skills_are_in_the_prefilter():
    from jarvis import intent

    names = {n for n, *_ in intent.tools()}
    assert {"switch_window", "window", "wifi", "bluetooth", "theme", "do_not_disturb", "settings_page", "open_file",
            "find_file", "routine", "now_playing", "keyboard_shortcut", "type_text"} <= names
    assert "switch_window" in intent.shortlist(make_ctx(), "chrome window pe le chal")
    assert "wifi" in intent.shortlist(make_ctx(), "net band karna hai wifi wala")


@pytest.mark.parametrize("category", ["windows", "keyboard", "files", "settings", "routines"])
def test_help_categories(category):
    assert general.examples_for(category)
    assert general.find_category(category) == category
