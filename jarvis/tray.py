"""System tray app: the tray icon owns the main thread, the voice loop runs on a background thread.

    pythonw run_jarvis.pyw      (no console; this is what the startup shortcut runs)

Icon: an orange circle with "J" and a status dot: green = listening, grey = sleeping, red = mic muted.
"""
import logging
import os
import subprocess
import sys
import threading

import config
from jarvis import hotkey, notify

log = logging.getLogger(__name__)

ORANGE = (243, 146, 24, 255)
STATUS_COLOURS = {
    "Listening": (46, 204, 113, 255),   # green
    "Sleeping": (149, 165, 166, 255),   # grey
    "Muted": (231, 76, 60, 255),        # red
    "Starting": (241, 196, 15, 255),    # yellow
}


def make_icon(status="Listening", size=64):
    """Orange circle with a white "J" and a coloured status dot in the corner."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((1, 1, size - 2, size - 2), fill=ORANGE)
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.62))
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), "J", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1] - size * 0.02), "J", font=font, fill="white")
    r = int(size * 0.2)
    cx, cy = size - r - 1, size - r - 1
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=STATUS_COLOURS.get(status, ORANGE),
                 outline="white", width=max(2, size // 32))
    return img


def status_dot_position(size=64):
    r = int(size * 0.2)
    return size - r - 1, size - r - 1


def open_path(path):
    os.startfile(str(path))


class TrayApp:
    def __init__(self, instance=None, voice_factory=None):
        import pystray

        self._pystray = pystray
        self.instance = instance
        self.voice_factory = voice_factory or self._default_voice
        self.assistant = None
        self.voice = None
        self._hotkey = None
        self._restart = False
        self.status = "Starting"
        item, menu = pystray.MenuItem, pystray.Menu
        self.menu = menu(
            item("Wake up", self.on_wake, default=True),
            item("Sleep", self.on_sleep),
            item(lambda _: "Unmute mic" if self.assistant and self.assistant.mic_muted else "Mute mic", self.on_mute),
            menu.SEPARATOR,
            item("Settings", self.on_settings),
            item("Open COMMANDS.md", self.on_open_commands),
            item("Open notes", self.on_open_notes),
            item("Open logs", self.on_open_logs),
            menu.SEPARATOR,
            item(f"Restart {config.ASSISTANT_NAME}", self.on_restart),
            item("Quit", self.on_quit),
        )
        self.icon = pystray.Icon("jarvis", make_icon("Starting"), self.tooltip("Starting"), self.menu)
        notify.set_tray_notifier(lambda title, message: self.icon.notify(message, title))

    @staticmethod
    def _default_voice():
        from jarvis.voice import Voice

        return Voice(text_mode=False, mute=False)

    @staticmethod
    def tooltip(status):
        return f"{config.ASSISTANT_NAME}: {status}"

    # ------------------------------------------------------------ status
    def set_status(self, status):
        self.status = status
        self.icon.icon = make_icon(status)
        self.icon.title = self.tooltip(status)
        try:
            self.icon.update_menu()
        except Exception:
            pass

    # ------------------------------------------------------------ voice loop (background thread)
    def _voice_loop(self):
        from jarvis.assistant import Assistant

        try:
            self.voice = self.voice_factory()
            if getattr(self.voice, "text_mode", False):
                log.warning("[tray] No microphone found; voice is off until one is connected.")
                self.set_status("Muted")
                notify.toast(config.ASSISTANT_NAME, "No microphone found. Connect one and choose Restart.")
                return
            self.assistant = Assistant(self.voice, wake_enabled=config.WAKE_WORD_ENABLED,
                                       on_status=self.set_status, on_exit=self.icon.stop)
            self._hotkey = hotkey.start(self.assistant.wake)
            self.assistant.run()
        except Exception:
            log.exception("[tray] voice loop crashed")
            notify.toast(config.ASSISTANT_NAME, "Something went wrong. See Open logs in the tray menu.")
            self.set_status("Muted")

    def on_second_launch(self):
        """Someone started Jarvis again: wake the running one instead."""
        if self.assistant:
            self.assistant.wake()
        notify.toast(config.ASSISTANT_NAME, f"{config.ASSISTANT_NAME} is already running. I'm listening.")

    # ------------------------------------------------------------ menu actions
    def on_wake(self, *_):
        if self.assistant:
            self.assistant.wake()

    def on_sleep(self, *_):
        if self.assistant:
            self.assistant.sleep()

    def on_mute(self, *_):
        if self.assistant:
            self.assistant.toggle_mute()

    def on_open_commands(self, *_):
        open_path(config.BASE_DIR / "COMMANDS.md")

    def on_open_notes(self, *_):
        path = config.NOTES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        open_path(path)

    def on_open_logs(self, *_):
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        open_path(config.LOG_DIR)

    def on_settings(self, *_):
        """Open the Settings window (its own process: Tk and the tray each want a main thread). Restart on save."""
        def run():
            from jarvis.gui.setup_window import SAVED

            try:
                code = subprocess.call(app_command("--settings"), cwd=str(config.BASE_DIR))
            except OSError:
                log.exception("[tray] couldn't open Settings")
                return
            if code == SAVED:
                log.info("[tray] settings saved; restarting to apply them")
                self.on_restart()

        threading.Thread(target=run, name="settings-window", daemon=True).start()

    def on_restart(self, *_):
        self._restart = True
        self.on_quit()

    def on_quit(self, *_):
        if self.assistant:
            self.assistant.stop()
        self.icon.stop()

    # ------------------------------------------------------------ lifecycle
    def run(self):
        """Block on the tray (main thread) until Quit or goodbye."""
        if self.instance:
            self.instance.listen(self.on_second_launch)
        threading.Thread(target=self._voice_loop, name="voice-loop", daemon=True).start()
        self.icon.run()
        self.shutdown()

    def shutdown(self):
        log.info("[tray] shutting down")
        if self.assistant:
            self.assistant.stop()
        if self._hotkey:
            self._hotkey.stop()
        if self.instance:
            self.instance.release()  # before restarting, so the new copy can take the lock
        if self._restart:
            relaunch()
        if self.voice and hasattr(self.voice, "close"):
            self.voice.close()


def app_command(*args):
    """How to start this program again with these arguments (the built Jarvis.exe, or Python + the launcher)."""
    if config.FROZEN:
        return [sys.executable, *args]
    launcher = config.REPO_DIR / ("run_jarvis.pyw" if not args else "main.py")
    return [sys.executable, str(launcher), *args]


def relaunch():
    """Start a fresh copy of this program (used by Restart)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    log.info("[tray] restarting: %s %s", sys.executable, sys.argv)
    subprocess.Popen([sys.executable, *sys.argv], cwd=str(config.BASE_DIR), creationflags=flags, close_fds=True)


def first_run_setup(instance=None):
    """No settings yet: show the setup wizard. True if it saved (and a fresh copy was started to use them)."""
    from jarvis import setup_logic

    if not setup_logic.needs_setup():
        return False
    try:
        from jarvis.gui.setup_window import SAVED, run_wizard

        code = run_wizard()
    except Exception:
        log.exception("[tray] the setup wizard failed; starting with default settings")
        return False
    if code != SAVED:
        return False
    if instance:
        instance.release()
    relaunch()
    return True


def main():
    """Entry point for run_jarvis.pyw."""
    from jarvis.instance import SingleInstance
    from jarvis.logs import setup_logging

    setup_logging(console=None)  # file always; console only if there is one
    import jarvis.skills  # noqa: F401  (register skills)

    instance = SingleInstance()
    if not instance.acquire():
        reached = instance.signal_existing()
        log.info("[tray] already running (signalled: %s); exiting", reached)
        return 0
    if first_run_setup(instance):
        return 0  # the wizard saved settings and started a fresh copy that reads them
    log.info("[tray] %s starting in the tray", config.ASSISTANT_NAME)
    try:
        TrayApp(instance).run()
    except Exception:
        log.exception("[tray] crashed")
        instance.release()
        return 1
    return 0
