"""Background mode: tray, hotkey, toasts, startup shortcut, single instance and logging.

pystray, pynput and win11toast are replaced with fakes; nothing real is shown or installed.
"""
import logging
import sys
import threading
import time
import types
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from helpers import FakeVoice, make_ctx

import config
from jarvis import hotkey, logs, notify, startup
from jarvis.assistant import Assistant
from jarvis.instance import SingleInstance
from jarvis.router import handle
from jarvis.skills import system


# ---------------------------------------------------------------- startup shortcut
def test_startup_folder_and_shortcut_path(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert startup.startup_folder() == tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert startup.shortcut_path().name == f"{config.ASSISTANT_NAME}.lnk"


def test_pythonw_prefers_repo_venv(tmp_path):
    venv_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    venv_pythonw.parent.mkdir(parents=True)
    venv_pythonw.touch()
    assert startup.pythonw_path(tmp_path, executable=tmp_path / "elsewhere" / "python.exe") == venv_pythonw


def test_pythonw_falls_back_to_sibling_of_python(tmp_path):
    py = tmp_path / "Python313" / "python.exe"
    py.parent.mkdir()
    py.touch()
    (py.parent / "pythonw.exe").touch()
    assert startup.pythonw_path(tmp_path / "repo", executable=py) == py.parent / "pythonw.exe"
    (py.parent / "pythonw.exe").unlink()
    assert startup.pythonw_path(tmp_path / "repo", executable=py) == py   # no pythonw at all


def test_shortcut_spec_points_at_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    repo = tmp_path / "my repo"
    spec = startup.shortcut_spec(repo, executable=tmp_path / "python.exe")
    assert spec["arguments"] == f'"{repo / "run_jarvis.pyw"}"'
    assert spec["working_dir"] == repo
    assert spec["path"] == startup.shortcut_path()


def test_powershell_script_quotes_paths():
    spec = {"path": Path(r"C:\Users\O'Neil\Startup\Jarvis.lnk"), "target": Path(r"C:\py\pythonw.exe"),
            "arguments": r'"C:\code\run_jarvis.pyw"', "working_dir": Path(r"C:\code"), "description": "Jarvis"}
    script = startup.powershell_script(spec)
    assert r"CreateShortcut('C:\Users\O''Neil\Startup\Jarvis.lnk')" in script
    assert r"""$s.Arguments = '"C:\code\run_jarvis.pyw"'""" in script
    assert script.endswith("$s.Save()")


def test_install_and_uninstall(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(sys, "platform", "win32")
    scripts = []

    def fake_powershell(script):
        scripts.append(script)
        startup.shortcut_path().write_text("lnk")   # what WScript.Shell would do
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(startup, "run_powershell", fake_powershell)
    assert not startup.is_installed()
    assert startup.install() == startup.shortcut_path()
    assert startup.is_installed() and "run_jarvis.pyw" in scripts[0]
    assert startup.uninstall() is True and not startup.is_installed()
    assert startup.uninstall() is False


def test_voice_autostart_confirms(monkeypatch):
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    installed = []
    monkeypatch.setattr(startup, "is_installed", lambda: bool(installed))
    monkeypatch.setattr(startup, "install", lambda: installed.append(True))
    monkeypatch.setattr(startup, "uninstall", lambda: installed.clear())

    ctx = make_ctx(["no"])
    assert handle(ctx, "start with windows") == "autostart"
    assert installed == [] and ctx.voice.last == "Okay, I won't."
    ctx = make_ctx(["yes"])
    handle(ctx, "start jarvis automatically when i log in")
    assert installed == [True] and "will start in the tray" in ctx.voice.last

    ctx = make_ctx(["yes"])
    assert handle(ctx, "don't start with windows") == "stop autostart"
    assert installed == [] and "won't start with Windows" in ctx.voice.last


# ---------------------------------------------------------------- single instance
@pytest.mark.skipif(sys.platform != "win32", reason="named mutex is Windows-only")
def test_named_mutex_single_instance():
    name = f"JarvisTest-{uuid.uuid4().hex}"
    first, second = SingleInstance(name), SingleInstance(name)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_lock_file_single_instance(tmp_path):
    first = SingleInstance("JarvisTest", lock_dir=tmp_path, use_mutex=False)
    second = SingleInstance("JarvisTest", lock_dir=tmp_path, use_mutex=False)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


@pytest.mark.skipif(sys.platform != "win32", reason="named event is Windows-only")
def test_second_launch_signals_the_first():
    name = f"JarvisTest-{uuid.uuid4().hex}"
    first = SingleInstance(name)
    assert first.acquire()
    shown = threading.Event()
    first.listen(shown.set)
    second = SingleInstance(name)
    assert second.acquire() is False
    assert second.signal_existing() is True
    assert shown.wait(3)
    first.release()
    assert SingleInstance(f"JarvisTest-{uuid.uuid4().hex}").signal_existing() is False  # nobody listening


# ---------------------------------------------------------------- logging
@pytest.fixture
def clean_root_logger():
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def test_log_rotation_config(clean_root_logger):
    handler = logs.setup_logging(console=False)
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 1_000_000 and handler.backupCount == 5
    assert Path(handler.baseFilename) == config.LOG_DIR / "jarvis.log"
    root = logging.getLogger()
    assert not any(type(h) is logging.StreamHandler and getattr(h, "_jarvis", False) for h in root.handlers)
    logging.getLogger("jarvis.test").info("hello log")
    handler.flush()
    assert "hello log" in (config.LOG_DIR / "jarvis.log").read_text(encoding="utf-8")
    handler.doRollover()
    assert (config.LOG_DIR / "jarvis.log.1").exists()


def test_console_handler_and_no_duplicates(clean_root_logger):
    logs.setup_logging(console=True)
    logs.setup_logging(console=True)   # calling twice must not double every line
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_jarvis", False)]
    assert len(ours) == 2


def test_no_console_under_pythonw(clean_root_logger, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    logs.setup_logging()
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_jarvis", False)]
    assert len(ours) == 1 and isinstance(ours[0], RotatingFileHandler)


# ---------------------------------------------------------------- hotkey
@pytest.mark.parametrize("spec, combo", [
    ("ctrl+alt+j", "<ctrl>+<alt>+j"), ("Ctrl + Alt + J", "<ctrl>+<alt>+j"), ("win+shift+space", "<cmd>+<shift>+<space>"),
    ("control+f9", "<ctrl>+<f9>"),
])
def test_hotkey_format(spec, combo):
    assert hotkey.to_pynput(spec) == combo


def test_hotkey_start_uses_pynput(monkeypatch):
    created = {}

    class FakeHotKeys:
        def __init__(self, mapping):
            created["mapping"] = mapping
            self.daemon = False

        def start(self):
            created["started"] = True

    fake = types.ModuleType("pynput")
    fake.keyboard = types.SimpleNamespace(GlobalHotKeys=FakeHotKeys)
    monkeypatch.setitem(sys.modules, "pynput", fake)
    pressed = []
    listener = hotkey.start(lambda: pressed.append(1), spec="ctrl+alt+j")
    assert created["started"] and listener.daemon
    created["mapping"]["<ctrl>+<alt>+j"]()
    assert pressed == [1]


def test_hotkey_disabled_or_invalid():
    assert hotkey.start(lambda: None, spec="") is None
    assert hotkey.start(lambda: None, spec="ctrl+banana") is None


# ---------------------------------------------------------------- toasts
def test_toast_respects_notify_setting(monkeypatch):
    shown = []
    monkeypatch.setattr(notify, "_show", lambda t, m: shown.append((t, m)) or "fake")
    assert notify.toast("Reminder", "stretch", background=False) is False   # NOTIFY is off in tests
    monkeypatch.setattr(config, "NOTIFY", True)
    assert notify.toast("Reminder", "stretch", background=False) is True
    assert shown == [("Reminder", "stretch")]


def test_toast_uses_win11toast(monkeypatch):
    calls = []
    fake = types.ModuleType("win11toast")
    fake.notify = lambda title, message, **kw: calls.append((title, message))
    monkeypatch.setitem(sys.modules, "win11toast", fake)
    assert notify._show("Reminder", "drink water") == "win11toast"
    assert calls == [(f"{config.ASSISTANT_NAME}: Reminder", "drink water")]


def test_toast_falls_back_to_tray(monkeypatch):
    monkeypatch.setitem(sys.modules, "win11toast", None)   # import fails
    shown = []
    notify.set_tray_notifier(lambda t, m: shown.append((t, m)))
    try:
        assert notify._show("Timer done", "5 minutes") == "tray"
    finally:
        notify.set_tray_notifier(None)
    assert shown == [("Timer done", "5 minutes")]


def test_reminder_shows_toast(monkeypatch):
    monkeypatch.setattr(config, "NOTIFY", True)
    toasts = []
    monkeypatch.setattr(notify, "_show", lambda t, m: toasts.append((t, m)))
    ctx = make_ctx()
    handle(ctx, "remind me in 1 second to stretch")
    deadline = time.time() + 4
    while time.time() < deadline and not toasts:
        time.sleep(0.05)
    assert toasts == [("Reminder", "stretch")]


# ---------------------------------------------------------------- assistant controls
def test_hotkey_wake_skips_wake_word_once(monkeypatch):
    monkeypatch.setattr("jarvis.assistant.beep", lambda: None)
    monkeypatch.setattr(config, "FOLLOW_UP_SECONDS", 0)
    statuses = []
    assistant = Assistant(FakeVoice(), wake_enabled=True, on_status=statuses.append)
    assistant.sleep()
    assert assistant.status == "Sleeping"
    assert assistant.step("what is 2 plus 2") is None          # asleep, no wake word: ignored
    assistant.wake()
    assert assistant.status == "Listening"
    assert assistant.step("what is 2 plus 2") == "calculator"  # hotkey: no wake word needed
    time.sleep(0.01)
    assert assistant.step("what is 3 plus 3") is None          # only the next phrase
    assert statuses == ["Sleeping", "Listening"]


def test_mute_status_and_loop_skips_listening(monkeypatch):
    voice = FakeVoice(["exit"], stop_when_empty=True)
    statuses = []
    assistant = Assistant(voice, wake_enabled=False, on_status=statuses.append)
    assistant.set_muted(True)
    assert assistant.status == "Muted"
    listened = []
    real_listen = voice.listen
    voice.listen = lambda *a, **k: listened.append(1) or real_listen()
    threading.Timer(0.4, assistant.set_muted, args=(False,)).start()
    assistant.run()
    assert listened and statuses[0] == "Muted" and "Listening" in statuses
    assert not assistant.running


# ---------------------------------------------------------------- tray
class FakeIcon:
    def __init__(self, name, image, title, menu):
        self.name, self.icon, self.title, self.menu = name, image, title, menu
        self.stopped = False
        self.notified = []

    def update_menu(self):
        pass

    def notify(self, message, title=None):
        self.notified.append((title, message))

    def stop(self):
        self.stopped = True

    def run(self):
        pass


class FakeMenuItem:
    def __init__(self, text, action, default=False):
        self.text, self.action, self.default = text, action, default

    def label(self):
        return self.text(self) if callable(self.text) else self.text


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


@pytest.fixture
def fake_pystray(monkeypatch):
    module = types.ModuleType("pystray")
    module.Icon, module.Menu, module.MenuItem = FakeIcon, FakeMenu, FakeMenuItem
    monkeypatch.setitem(sys.modules, "pystray", module)
    yield module
    notify.set_tray_notifier(None)


def test_icon_colours():
    from jarvis import tray

    x, y = tray.status_dot_position(64)
    assert tray.make_icon("Listening").getpixel((x, y)) == tray.STATUS_COLOURS["Listening"]
    assert tray.make_icon("Sleeping").getpixel((x, y)) == tray.STATUS_COLOURS["Sleeping"]
    assert tray.make_icon("Muted").getpixel((x, y)) == tray.STATUS_COLOURS["Muted"]
    assert tray.make_icon("Listening").getpixel((10, 32)) == tray.ORANGE   # orange circle body
    assert tray.make_icon("Listening").getpixel((0, 0))[3] == 0            # transparent corner


def test_tray_menu_and_status(fake_pystray, monkeypatch):
    from jarvis import tray

    opened = []
    monkeypatch.setattr(tray, "open_path", opened.append)
    app = tray.TrayApp()
    labels = [i.label() for i in app.menu.items if isinstance(i, FakeMenuItem)]
    assert labels == ["Wake up", "Sleep", "Mute mic", "Settings", "Open COMMANDS.md", "Open notes", "Open logs",
                      f"Restart {config.ASSISTANT_NAME}", "Quit"]
    assert app.icon.title == f"{config.ASSISTANT_NAME}: Starting"

    monkeypatch.setattr("jarvis.assistant.beep", lambda: None)
    app.assistant = Assistant(FakeVoice(), on_status=app.set_status)
    app.on_mute()
    assert app.icon.title == f"{config.ASSISTANT_NAME}: Muted"
    assert [i.label() for i in app.menu.items if isinstance(i, FakeMenuItem)][2] == "Unmute mic"
    app.on_wake()
    assert app.icon.title == f"{config.ASSISTANT_NAME}: Listening" and not app.assistant.mic_muted
    app.on_sleep()
    assert app.icon.title == f"{config.ASSISTANT_NAME}: Sleeping"

    app.on_open_commands()
    app.on_open_notes()
    app.on_open_logs()
    assert opened == [config.BASE_DIR / "COMMANDS.md", config.NOTES_FILE, config.LOG_DIR]
    assert config.NOTES_FILE.exists()

    app.on_second_launch.__func__  # exists
    app.on_quit()
    assert app.icon.stopped and not app.assistant.running


def test_tray_voice_loop_goodbye_stops_icon(fake_pystray, monkeypatch):
    from jarvis import tray

    monkeypatch.setattr(tray.hotkey, "start", lambda cb: None)
    app = tray.TrayApp(voice_factory=lambda: FakeVoice(["goodbye"], stop_when_empty=True))
    app._voice_loop()          # runs to completion: goodbye ends the loop
    assert app.icon.stopped    # ...and closes the tray
    assert app.assistant.ctx.voice.said[-1].startswith("Goodbye")


def test_tray_without_microphone(fake_pystray, monkeypatch):
    from jarvis import tray

    voice = FakeVoice()
    voice.text_mode = True
    app = tray.TrayApp(voice_factory=lambda: voice)
    app._voice_loop()
    assert app.icon.title == f"{config.ASSISTANT_NAME}: Muted" and app.assistant is None


def test_tray_second_launch_wakes(fake_pystray, monkeypatch):
    from jarvis import tray

    monkeypatch.setattr("jarvis.assistant.beep", lambda: None)
    app = tray.TrayApp()
    app.assistant = Assistant(FakeVoice())
    app.assistant.sleep()
    app.on_second_launch()
    assert app.assistant.status == "Listening"


def test_restart_releases_lock_before_relaunch(fake_pystray, monkeypatch):
    from jarvis import tray

    order = []
    instance = types.SimpleNamespace(release=lambda: order.append("release"), listen=lambda cb: None)
    monkeypatch.setattr(tray, "relaunch", lambda: order.append("relaunch"))
    app = tray.TrayApp(instance=instance)
    app.on_restart()
    app.shutdown()
    assert order == ["release", "relaunch"]


def test_console_logging_survives_emoji(tmp_path):
    """An emoji in an email subject must not break the console (cp1252 on Windows)."""
    import subprocess
    import sys

    code = ("import logging, config; from pathlib import Path; config.LOG_DIR = Path(r'%s'); "
            "from jarvis.logs import setup_logging; setup_logging(console=True); "
            "logging.getLogger('t').info('Party time \U0001f973 done')" % tmp_path)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=str(config.BASE_DIR),
                            env={**__import__("os").environ, "PYTHONIOENCODING": "cp1252"}, timeout=60)
    assert b"Logging error" not in result.stderr and b"UnicodeEncodeError" not in result.stderr
    assert b"Party time ? done" in result.stdout
