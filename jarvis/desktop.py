"""Low-level Windows desktop calls: top-level windows, focus, closing, typing, clipboard, monitors.

Every real side effect of the laptop-control skills goes through this module (or system.press / system.hotkey),
so the tests replace these functions and nothing on the real desktop is ever touched by a test.
"""
import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Processes that are terminals: Jarvis never types or presses Enter in them unless told "in terminal"
TERMINALS = {"windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
             "openconsole.exe", "bash.exe", "wsl.exe", "mintty.exe", "alacritty.exe", "wezterm-gui.exe"}
# Window titles that mean a bank or payment site is in front: no typing, pasting or key presses there
BANKING = ("netbanking", "net banking", "internet banking", "onlinesbi", "sbi online", "hdfc bank", "icici bank",
           "axis bank", "kotak", "yes bank", "bank of baroda", "punjab national bank", "pnb one", "canara bank",
           "idfc first", "indusind", "paypal", "paytm", "phonepe", "google pay", "bhim", "razorpay", "net-banking",
           " bank ", "banking")


@dataclass
class Window:
    hwnd: int
    title: str
    exe: str = ""     # process image name, lower-case: "chrome.exe"
    pid: int = 0
    minimized: bool = False

    @property
    def is_terminal(self):
        return self.exe in TERMINALS

    @property
    def is_banking(self):
        title = f" {self.title.lower()} "
        return any(word in title for word in BANKING)


GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW, DWMWA_CLOAKED = 4, -20, 0x00000080, 14
WM_CLOSE = 0x0010
SW = {"minimize": 6, "maximize": 3, "restore": 9, "show": 5}
SKIP_TITLES = {"Program Manager", "Windows Input Experience", "Microsoft Text Input Application", "Settings ",
               "Windows Shell Experience Host", "NVIDIA GeForce Overlay", "PopupHost"}


def _cloaked(hwnd):
    value = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value),
                                                   ctypes.sizeof(value))
    except OSError:
        return False
    return bool(value.value)


def _process_name(pid):
    import psutil

    try:
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def _window(hwnd):
    import win32gui
    import win32process

    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return Window(hwnd, win32gui.GetWindowText(hwnd), _process_name(pid), pid, bool(win32gui.IsIconic(hwnd)))


def list_windows():
    """Visible top-level app windows, front-most first (the order Alt+Tab shows them)."""
    import win32gui

    found = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return True
        if win32gui.GetWindow(hwnd, GW_OWNER) or win32gui.GetWindowLong(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if _cloaked(hwnd):  # suspended Store apps and windows on other virtual desktops
            return True
        found.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    windows = [_window(h) for h in found]
    return [w for w in windows if w.title not in SKIP_TITLES and w.pid != os.getpid()]


def active_window():
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    return _window(hwnd) if hwnd else None


def focus(hwnd):
    """Bring a window to the front (restoring it if minimized)."""
    import win32con
    import win32gui

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # Windows only lets the foreground process steal focus; a tap of Alt makes us "the last input"
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            return False
    return True


def show(hwnd, how):
    """how: minimize, maximize, restore."""
    import win32gui

    win32gui.ShowWindow(hwnd, SW[how])
    return True


def close(hwnd):
    """Ask a window to close, like clicking its X: the app can still ask to save unsaved work."""
    import win32gui

    win32gui.PostMessage(hwnd, WM_CLOSE, 0, 0)
    return True


def is_alive(hwnd):
    import win32gui

    return bool(win32gui.IsWindow(hwnd))


def monitor_count():
    import win32api

    return len(win32api.EnumDisplayMonitors())


def focused_is_password():
    """True when the keyboard focus is in a password box (UI Automation's IsPassword)."""
    try:
        from pywinauto.uia_defines import IUIA

        element = IUIA().iuia.GetFocusedElement()
        return bool(element and element.CurrentIsPassword)
    except Exception:
        log.debug("Couldn't read the focused element", exc_info=True)
        return False


# ---------------------------------------------------------------- typing (Unicode, so Hindi works too)
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


KEYEVENTF_UNICODE, KEYEVENTF_KEYUP, INPUT_KEYBOARD, VK_RETURN = 0x0004, 0x0002, 1, 0x0D


def type_text(text, delay=0.004):
    """Type text into the focused window, character by character. Newlines become Enter."""
    send = ctypes.windll.user32.SendInput
    for char in text:
        if char == "\n":
            events = [(VK_RETURN, 0, 0), (VK_RETURN, 0, KEYEVENTF_KEYUP)]
        else:
            raw = char.encode("utf-16-le")  # emoji are two UTF-16 units
            units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
            events = [e for u in units for e in ((0, u, KEYEVENTF_UNICODE), (0, u, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))]
        for vk, scan, flags in events:
            inp = _INPUT(type=INPUT_KEYBOARD)
            inp.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
            send(1, ctypes.byref(inp), ctypes.sizeof(inp))
        if delay:
            time.sleep(delay)


# ---------------------------------------------------------------- clipboard (text only)
def get_clipboard():
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return None
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard(text):
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()
    return True


# ---------------------------------------------------------------- starting things
def start(target):
    """os.startfile: a file, a folder, a shortcut or a URI."""
    os.startfile(target)


def start_app_id(app_id):
    """Launch a Start-menu app (Store or desktop) by its AppUserModelID."""
    import subprocess

    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
