"""Windows settings by voice: Wi-Fi, Bluetooth, dark mode, Do Not Disturb, battery saver, power mode,
per-app volume, the microphone, audio output, the Recycle Bin, temp files and every ms-settings: page.

Safety: the only registry values ever written are the two theme keys (AppsUseLightTheme, SystemUsesLightTheme).
Emptying the Recycle Bin and clearing temp files always ask first; temp means YOUR temp folder only.
Every change is logged to data/logs/actions.log and most can be undone ("undo that").
"""
import asyncio
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

import config
from jarvis import actions, desktop, hinglish as hi
from jarvis.router import SpokenError, skill
from jarvis.skills import system

log = logging.getLogger(__name__)

NOT_WINDOWS = "Sorry, that only works on Windows."
WIFI = r"(?:wi-?\s?fi|wireless|internet)"
ON_WORDS = r"(?:on|enable|start|connect|chalu|chaalu|shuru)"
OFF_WORDS = r"(?:off|disable|stop|disconnect|band|bandh|bnd)"


def _on_off(command):
    """True for on, False for off, None if neither was said."""
    if re.search(rf"\b(?:turn|switch|put)\s+off\b|\b{OFF_WORDS}\b|\bdisable\b|^close\b", command):
        return False
    if re.search(rf"\b(?:turn|switch|put)\s+on\b|\b{ON_WORDS}\b|\benable\b", command):
        return True
    return None


def run_async(coro_factory, timeout=8):
    """Run a WinRT coroutine to completion on its own thread (safe whatever loop the caller has)."""
    box = {}

    def worker():
        try:
            box["value"] = asyncio.run(coro_factory())
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if "error" in box:
        raise box["error"]
    if thread.is_alive():
        raise TimeoutError("Windows took too long to answer")
    return box.get("value")


# ---------------------------------------------------------------- radios (Wi-Fi, Bluetooth) via WinRT
def radio_states():
    """{"wifi": True/False, "bluetooth": True/False} for the radios Windows reports."""
    async def read():
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState

        names = {RadioKind.WI_FI: "wifi", RadioKind.BLUETOOTH: "bluetooth", RadioKind.MOBILE_BROADBAND: "mobile"}
        result = {}
        for radio in await Radio.get_radios_async():
            if radio.kind in names:
                result[names[radio.kind]] = radio.state == RadioState.ON
        return result

    return run_async(read)


def set_radio(kind, on):
    """Switch the Wi-Fi or Bluetooth radio. Returns True if Windows allowed it."""
    async def change():
        from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState

        wanted = {"wifi": RadioKind.WI_FI, "bluetooth": RadioKind.BLUETOOTH}[kind]
        if await Radio.request_access_async() != RadioAccessStatus.ALLOWED:
            return False
        radios = [r for r in await Radio.get_radios_async() if r.kind == wanted]
        if not radios:
            return False
        for radio in radios:
            status = await radio.set_state_async(RadioState.ON if on else RadioState.OFF)
            if status != RadioAccessStatus.ALLOWED:
                return False
        return True

    return run_async(change)


def wifi_details():
    """(connected SSID or None, signal percent or None) from netsh."""
    out = system.run(["netsh", "wlan", "show", "interfaces"])
    text = out.stdout or ""
    ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", text, re.MULTILINE)
    signal = re.search(r"^\s*Signal\s*:\s*(\d+)%", text, re.MULTILINE)
    state = re.search(r"^\s*State\s*:\s*(\w+)", text, re.MULTILINE)
    connected = state and state.group(1).lower() == "connected"
    return (ssid.group(1).strip() if connected and ssid else None), (int(signal.group(1)) if signal else None)


def saved_networks():
    out = system.run(["netsh", "wlan", "show", "profiles"])
    return [m.strip() for m in re.findall(r"All User Profile\s*:\s*(.+)$", out.stdout or "", re.MULTILINE)]


def connect_wifi(name):
    return system.run(["netsh", "wlan", "connect", f"name={name}"]).returncode == 0


def _radio_toggle(ctx, kind, on, label):
    states = radio_states()
    if kind not in states:
        return f"I can't find a {label} radio on this laptop."
    if states[kind] == on:
        return f"{label} is already {'on' if on else 'off'}."
    if kind == "wifi" and not on and config.STT_ENGINE == "google" and not getattr(ctx.voice, "text_mode", False):
        if not ctx.confirm("If Wi-Fi goes off I can't understand speech (it's recognised online). Turn it off anyway?"):
            return "Okay, Wi-Fi stays on."
    if not set_radio(kind, on):
        page = "network-wifi" if kind == "wifi" else "bluetooth"
        open_settings_page(page)
        return f"Windows wouldn't let me switch {label} {'on' if on else 'off'}, so I opened its settings."
    actions.record(f"Turned {label} {'on' if on else 'off'}", undo=lambda: set_radio(kind, not on),
                   undo_text=f"turn {label} {'off' if on else 'on'}")
    return f"{label} is {'on' if on else 'off'}."


@skill("wifi", rf"^(?:please\s+)?(?:turn|switch|put)\s+(on|off)\s+(?:the\s+|my\s+)?{WIFI}$",
       rf"^(?:please\s+)?(?:turn|switch|put)\s+(?:the\s+|my\s+)?{WIFI}\s+(on|off)$",
       rf"^(?:please\s+)?(?:enable|disable)\s+(?:the\s+)?{WIFI}()$", rf"^{WIFI}\s+(on|off)$",
       rf"^(close|open)\s+(?:the\s+)?(?:wi-?\s?fi)$",  # Hindi mode: "वाईफाई बंद करो" arrives as "close wifi"
       rf"{hi.LEAD}{WIFI}\s+(band|bandh|bnd|off|on|chalu|chaalu|shuru){hi.KAR}$",
       rf"^(?:what(?:'s| is)\s+(?:the\s+|my\s+)?)?{WIFI}\s+(status)\??$", rf"^(?:is|am i on)\s+(?:the\s+)?{WIFI}\s+(on|off|connected|working)\??$",
       rf"^(?:which|what)\s+{WIFI}\s+(?:am i|is it|network am i)\s+(?:on|connected to)\??()$",
       category="settings", help=["turn off wifi", "wifi status", "yaar wifi band kar de"],
       params={"state": ["on", "off", "status"]}, command=lambda a: "wifi status" if a.get("state") == "status"
       else f"turn {a.get('state', 'on')} wifi")
def wifi(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    asking = re.search(r"\b(?:status|is|am i|which|what)\b", command) and not re.search(r"\b(?:turn|switch|put)\b", command)
    on = None if asking else _on_off(command)
    if on is None:
        states = radio_states()
        if not states.get("wifi", False):
            return "Wi-Fi is off."
        ssid, signal = wifi_details()
        if not ssid:
            return "Wi-Fi is on, but not connected to a network."
        return f"You're connected to {ssid}" + (f", signal {signal} percent." if signal is not None else ".")
    return _radio_toggle(ctx, "wifi", on, "Wi-Fi")


@skill("connect wifi", r"^(?:please\s+)?(?:connect|join)\s+(?:me\s+)?to\s+(?:the\s+)?(?:wi-?\s?fi\s+(?:network\s+)?)?(.+?)"
                       r"(?:\s+(?:wi-?\s?fi|network))?$",
       rf"{hi.LEAD}(.+?)\s+(?:wi-?\s?fi\s+)?(?:se|pe|par)\s+connect{hi.KAR}$",
       category="settings", help=["connect to Home WiFi"])
def connect(ctx, command, match):
    name = match.group(1).strip()
    if re.search(r"\b(?:headphones?|speakers?|earphones?|bluetooth|vpn|internet|chrome|vs code)\b", name):
        return None
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    networks = saved_networks()
    from jarvis.skills import app_index

    scored = sorted(((app_index.score(name, n), n) for n in networks), reverse=True)
    if not scored or scored[0][0] < 0.7:
        return None if not re.search(r"\bwi-?\s?fi|network\b", command) else \
            f"{name} isn't one of your saved Wi-Fi networks. I can only join networks you've used before."
    network = scored[0][1]
    if not connect_wifi(network):
        return f"I couldn't connect to {network}. Is it in range?"
    actions.record(f"Connected to the Wi-Fi network {network}")
    return f"Connecting to {network}."


@skill("bluetooth", r"^(?:please\s+)?(?:turn|switch|put)\s+(on|off)\s+(?:the\s+|my\s+)?bluetooth$",
       r"^(?:please\s+)?(?:turn|switch|put)\s+(?:the\s+|my\s+)?bluetooth\s+(on|off)$",
       r"^(?:please\s+)?(?:enable|disable)\s+(?:the\s+)?bluetooth()$", r"^bluetooth\s+(on|off|status)$",
       rf"{hi.LEAD}bluetooth\s+(band|bandh|bnd|off|on|chalu|chaalu|shuru){hi.KAR}$",
       r"^is\s+(?:the\s+)?bluetooth\s+(on|off)\??$",
       category="settings", help=["turn on Bluetooth"], params={"state": ["on", "off"]},
       command="turn {state} bluetooth")
def bluetooth(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    on = None if re.search(r"^is\b|\bstatus\b", command) else _on_off(command)
    if on is None:
        states = radio_states()
        if "bluetooth" not in states:
            return "I can't find Bluetooth on this laptop."
        return f"Bluetooth is {'on' if states['bluetooth'] else 'off'}."
    return _radio_toggle(ctx, "bluetooth", on, "Bluetooth")


@skill("airplane mode", r"\b(?:airplane|aeroplane|flight)\s+mode\b", category="settings", help=["is airplane mode on?"])
def airplane_mode(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    states = radio_states()
    off = not any(states.values()) if states else None
    if re.search(r"\b(?:turn|switch|put)\b|\b(?:on|off|enable|disable)$", command) and not command.startswith("is "):
        open_settings_page("network-airplanemode")
        return "Windows only lets you flip airplane mode yourself, so I opened its settings."
    if off is None:
        return "I couldn't read the radios."
    return "Airplane mode looks on: every radio is off." if off else "Airplane mode is off."


# ---------------------------------------------------------------- dark / light mode (the only registry writes)
THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
THEME_VALUES = ("AppsUseLightTheme", "SystemUsesLightTheme")


def get_theme():
    """{"AppsUseLightTheme": 0/1, "SystemUsesLightTheme": 0/1}"""
    import winreg

    values = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY) as key:
        for name in THEME_VALUES:
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                values[name] = 1
    return values


def set_theme(values):
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for name in THEME_VALUES:  # only ever these two values
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(values[name]))
    # tell open windows (taskbar, Explorer, apps) the theme changed. Broadcasting waits on every window in turn
    # (28 s on a busy desktop), so it runs in the background and the reply is instant.
    def broadcast():
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 200,
                                                 ctypes.byref(result))

    threading.Thread(target=broadcast, name="theme-broadcast", daemon=True).start()
    return True


@skill("dark mode", r"^(?:please\s+)?(?:turn|switch|put)\s+(on|off)\s+(?:the\s+)?(dark|light)\s+(?:mode|theme)$",
       r"^(?:please\s+)?(?:turn|switch|put|go|change|set)\s+(?:it\s+|windows\s+|the theme\s+)?(?:to|into|on)?\s*(?:the\s+)?(dark|light)"
       r"\s+(?:mode|theme)(?:\s+(on|off))?$",
       r"^(?:please\s+)?(?:enable|disable|use|activate)\s+(?:the\s+)?(dark|light)\s+(?:mode|theme)()$",
       r"^(dark|light)\s+(?:mode|theme)(?:\s+(on|off|please|kar\w*|laga\w*|chalu))?$", r"^(?:toggle)\s+(dark|light)?\s*(?:mode|theme)()$",
       rf"{hi.LEAD}(dark|light)\s+(?:mode|theme)(?:\s+(?:laga|lagao|laga do|laga de|kar|karo|kar do|kar de|chalu|on)"
       rf"(?:\s+(?:na|please|yaar))*)?()$",
       category="settings", help=["dark mode on", "switch to light mode"],
       tool="theme", params={"mode": ["dark", "light"]}, command="switch to {mode} mode")
def dark_mode(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    groups = [g for g in match.groups() if g]
    mode = next((g for g in groups if g in ("dark", "light")), None)
    on = _on_off(" ".join(g for g in groups if g in ("on", "off"))) if any(g in ("on", "off") for g in groups) else True
    before = get_theme()
    if mode is None:  # "toggle theme"
        light = not before["AppsUseLightTheme"]
    else:
        light = (mode == "light") == on
    if before["AppsUseLightTheme"] == int(light) and before["SystemUsesLightTheme"] == int(light):
        return f"It's already in {'light' if light else 'dark'} mode."
    set_theme({name: int(light) for name in THEME_VALUES})
    actions.record(f"Switched Windows to {'light' if light else 'dark'} mode", undo=lambda: set_theme(before),
                   undo_text="switch the theme back")
    return f"{'Light' if light else 'Dark'} mode is on."


@skill("night light", r"\bnight\s?light\b", r"\b(?:blue light|eye comfort)\s+(?:filter|mode)\b",
       category="settings", help=["night light"])
def night_light(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    open_settings_page("nightlight")
    actions.record("Opened the Night light settings")
    return "Windows doesn't let apps flip Night light, so I opened its settings. It's the first switch."


# ---------------------------------------------------------------- Do Not Disturb
def set_dnd(on):
    """Flip the Do not disturb switch on the Settings > Notifications page through UI Automation.
    Returns True/False, or None if the switch couldn't be found (the page is left open then)."""
    from pywinauto import Desktop

    open_settings_page("notifications")
    deadline = time.monotonic() + 6
    toggle = None
    while time.monotonic() < deadline and toggle is None:
        time.sleep(0.4)
        try:
            window = Desktop(backend="uia").window(title_re="^Settings$")
            toggle = window.child_window(title_re="(?i)^do not disturb$", control_type="Button").wrapper_object()
        except Exception:
            toggle = None
    if toggle is None:
        return None
    try:
        state = toggle.get_toggle_state()
    except Exception:
        return None
    if bool(state) != on:
        toggle.toggle()
    try:
        window.close()
    except Exception:
        pass
    return True


@skill("do not disturb", r"^(?:please\s+)?(?:turn|switch|put)\s+(on|off)\s+(?:the\s+)?(?:do not disturb|dnd|focus(?: mode| assist)?)$",
       r"^(?:please\s+)?(?:turn|switch|put)\s+(?:the\s+)?(?:do not disturb|dnd|focus(?: mode| assist)?)\s+(on|off)$",
       r"^(?:please\s+)?(?:enable|disable|start|stop|end)\s+(?:the\s+)?(?:do not disturb|dnd|focus(?: mode| assist| session)?)()$",
       r"^(?:do not disturb|dnd|focus mode|focus assist)(?:\s+(on|off|mode))?$",
       r"^(?:please\s+)?(?:don't|do not) disturb me()$",
       rf"{hi.LEAD}(?:do not disturb|dnd|notifications?)\s+(band|bandh|off|on|chalu){hi.KAR}$",
       category="settings", help=["do not disturb on", "DND off"], tool="do_not_disturb",
       params={"state": ["on", "off"]}, command="do not disturb {state}")
def do_not_disturb(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    text = command.replace("do not disturb", "dnd").replace("don't disturb", "dnd")
    on = _on_off(text)
    if re.search(r"\b(?:stop|end|disable)\b", text):
        on = False
    elif re.search(r"\bnotifications?\s+(?:band|bandh)\b", text):
        on = True  # "notifications band kar do" = do not disturb ON
    if on is None:
        on = True
    result = set_dnd(on)
    if result is None:
        return "I opened the notification settings. Flip Do not disturb there; I couldn't reach the switch."
    actions.record(f"Turned Do not disturb {'on' if on else 'off'}", undo=lambda: set_dnd(not on),
                   undo_text=f"turn Do not disturb {'off' if on else 'on'}")
    return f"Do not disturb is {'on' if on else 'off'}."


# ---------------------------------------------------------------- battery saver, power mode
POWER_MODES = {"best performance": "ded574b5-45a0-4f42-8737-46345c09c238",
               "balanced": "00000000-0000-0000-0000-000000000000",
               "best power efficiency": "961cc777-2547-4f9d-8174-7d86181b8a7a"}


def battery_saver_threshold():
    out = system.run(["powercfg", "/q", "SCHEME_CURRENT", "SUB_ENERGYSAVER", "ESBATTTHRESHOLD"])
    found = re.search(r"Current DC Power Setting Index:\s*0x([0-9a-f]+)", out.stdout or "", re.IGNORECASE)
    return int(found.group(1), 16) if found else None


def set_battery_saver_threshold(percent):
    first = system.run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", "SUB_ENERGYSAVER", "ESBATTTHRESHOLD",
                        str(int(percent))])
    system.run(["powercfg", "/setactive", "SCHEME_CURRENT"])
    return first.returncode == 0


@skill("battery saver", r"\b(?:battery|energy)\s+saver\b", r"\bsave (?:the )?battery\b",
       category="settings", help=["battery saver on"], params={"state": ["on", "off"]}, command="battery saver {state}")
def battery_saver(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    on = _on_off(command)
    if on is None:
        on = True if re.search(r"\bsave (?:the )?battery\b", command) else None
    current = battery_saver_threshold()
    if on is None:
        if current is None:
            return "I couldn't read the battery saver setting."
        return "Battery saver comes on " + ("whenever you're on battery." if current >= 100 else f"at {current} percent.")
    if current is None:
        open_settings_page("batterysaver")
        return "I couldn't change it, so I opened the battery saver settings."
    target = 100 if on else (ctx.state.get("battery_saver_before") or 20)
    if on:
        ctx.state["battery_saver_before"] = current if current < 100 else 20
    if not set_battery_saver_threshold(target):
        open_settings_page("batterysaver")
        return "Windows wouldn't let me, so I opened the battery saver settings."
    actions.record(f"Battery saver {'on' if on else 'off'} (comes on at {target}%)",
                   undo=lambda: set_battery_saver_threshold(current), undo_text="put battery saver back how it was")
    return "Battery saver is on whenever you're unplugged." if on else f"Battery saver is off until {target} percent."


@skill("power mode", r"\b(?:power|performance)\s+mode\b", r"\bbest\s+(?:performance|power efficiency|battery life)\b",
       r"\b(?:balanced)\s+(?:mode|power)\b", category="settings", help=["best performance mode", "balanced power mode"],
       params={"mode": ["best performance", "balanced", "best power efficiency"]}, command="power mode {mode}")
def power_mode(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    if re.search(r"\bperformance\b", command) and not re.search(r"\bpower mode\b.*\bbalanced\b", command):
        mode = "best performance"
    elif re.search(r"\b(?:efficiency|battery life|saver)\b", command):
        mode = "best power efficiency"
    elif re.search(r"\bbalanced\b", command):
        mode = "balanced"
    else:
        current = system.run(["powercfg", "/getactiveoverlayscheme"]).stdout or ""
        name = next((n for n, g in POWER_MODES.items() if g in current.lower()), "balanced")
        return f"Power mode is {name}."
    before = system.run(["powercfg", "/getactiveoverlayscheme"]).stdout or ""
    previous = next((g for g in POWER_MODES.values() if g in before.lower()), POWER_MODES["balanced"])
    if system.run(["powercfg", "/overlaysetactive", POWER_MODES[mode]]).returncode != 0:
        open_settings_page("powersleep")
        return "I couldn't change it here, so I opened the power settings."
    actions.record(f"Set the power mode to {mode}", undo=lambda: system.run(["powercfg", "/overlaysetactive", previous]),
                   undo_text="put the power mode back")
    return f"Power mode set to {mode}."


# ---------------------------------------------------------------- volume: per app, master, mic, output device
def audio_sessions():
    """[(process name lower-case, session)] for apps currently using audio."""
    from pycaw.pycaw import AudioUtilities

    return [(s.Process.name().lower(), s) for s in AudioUtilities.GetAllSessions() if s.Process]


def sessions_for(app):
    from jarvis.skills import app_index

    exes = app_index._exes_for(app)
    return [s for name, s in audio_sessions() if name in exes]


def master_volume():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def get_master():
    return round(master_volume().GetMasterVolumeLevelScalar() * 100)


def set_master(percent):
    master_volume().SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None)


def set_app_volume(session, percent):
    session.SimpleAudioVolume.SetMasterVolume(max(0, min(100, percent)) / 100, None)


def get_app_volume(session):
    return round(session.SimpleAudioVolume.GetMasterVolume() * 100)


def set_app_mute(session, muted):
    session.SimpleAudioVolume.SetMute(1 if muted else 0, None)


def microphone():
    from pycaw.pycaw import AudioUtilities

    mic = AudioUtilities.GetMicrophone()
    if mic is None:
        return None
    from comtypes import CLSCTX_ALL
    from ctypes import POINTER, cast
    from pycaw.pycaw import IAudioEndpointVolume

    return cast(mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))


def set_mic_mute(muted):
    mic = microphone()
    if mic is None:
        return False
    mic.SetMute(1 if muted else 0, None)
    return True


def output_devices():
    """[(name, id, is_default)] for active speakers/headphones."""
    import warnings

    from pycaw.pycaw import AudioUtilities

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pycaw warns about properties some drivers don't have
        default_id = getattr(AudioUtilities.GetSpeakers(), "id", None)
        found = AudioUtilities.GetAllDevices(data_flow=0, device_state=1)  # render devices that are active
    devices = [(d.FriendlyName, d.id, d.id == default_id) for d in found if d.FriendlyName]
    # stereo "Headphones (Buds)" before the phone-call "Headset (Buds Hands-Free)" profile
    return sorted(devices, key=lambda d: "hands-free" in d[0].lower())


def set_output(device_id):
    from pycaw.pycaw import AudioUtilities

    AudioUtilities.SetDefaultDevice(device_id)
    return True


@skill("set volume", r"^(?:please\s+)?(?:set\s+)?(?:the\s+)?(?:system\s+|master\s+)?volume\s+(?:to\s+|at\s+)?(\d{1,3})\s*(?:%|percent)?$",
       r"^(?:please\s+)?(?:set|put|turn)\s+(?:the\s+)?volume\s+(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?$",
       rf"{hi.LEAD}(?:volume|awaaz|awaz)\s+(\d{{1,3}})\s*(?:%|percent)?\s*(?:pe|par)?{hi.KAR}$",
       category="settings", help=["volume 30", "set volume to 70 percent"])  # the AI reaches it via the volume tool
def set_volume(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    level = max(0, min(100, int(match.group(1))))
    try:
        before = get_master()
        set_master(level)
    except Exception:
        log.info("pycaw unavailable, using the volume keys", exc_info=True)
        from jarvis.skills import briefing

        briefing.set_volume_percent(level)
        before = None
    actions.record(f"Set the volume to {level}%", undo=(lambda: set_master(before)) if before is not None else None,
                   undo_text=f"put the volume back to {before}%")
    return ""


APP_WORD = r"([a-z][\w .+-]*?)"


@skill("app volume", rf"^(?:please\s+)?(?:set\s+)?(?:the\s+)?{APP_WORD}(?:'s)?\s+volume\s+(?:to\s+|at\s+)?(\d{{1,3}})\s*(?:%|percent)?$",
       rf"^(?:please\s+)?(?:set|put|turn)\s+(?:the\s+)?volume\s+(?:of|for|on)\s+{APP_WORD}\s+(?:to|at)\s+(\d{{1,3}})\s*(?:%|percent)?$",
       rf"^(?:please\s+)?(mute|unmute)\s+(?:the\s+)?{APP_WORD}$",
       rf"{hi.LEAD}{APP_WORD}\s+(?:ka|ki)\s+(?:volume|awaaz|awaz)\s+(\d{{1,3}})\s*(?:%|percent)?\s*(?:pe|par)?{hi.KAR}$",
       rf"{hi.LEAD}{APP_WORD}\s+(?:ko\s+)?(mute|unmute){hi.KAR}$",
       category="settings", help=["spotify volume 30", "mute chrome"])  # the AI reaches it via the volume tool
def app_volume(ctx, command, match):
    first, second = match.group(1), match.group(2)
    if first in ("mute", "unmute") or second in ("mute", "unmute"):
        verb, app = (first, second) if first in ("mute", "unmute") else (second, first)
        level = None
    else:
        app, level, verb = first, int(second), None
    app = app.strip()
    if re.fullmatch(r"(?:the|system|master|main|it|everything|all|sound|audio|speakers?|laptop|volume|my|me|"
                    r"set|turn|put|change)", app) or re.search(r"\b(?:mic|microphone)\b", app):
        return None  # plain "mute" / "volume 30" / "mute my mic": other skills
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    sessions = sessions_for(app)
    if not sessions:
        if re.search(r"\bspotify\b", app):
            return None  # Spotify isn't playing here: the Spotify skill sets its own volume through its API
        return f"{app.capitalize()} isn't playing any sound right now."
    if verb:
        muted = verb == "mute"
        for s in sessions:
            set_app_mute(s, muted)
        actions.record(f"{'Muted' if muted else 'Unmuted'} {app}",
                       undo=lambda: [set_app_mute(s, not muted) for s in sessions], undo_text=f"{'unmute' if muted else 'mute'} {app}")
        return f"{app.capitalize()} {'muted' if muted else 'unmuted'}."
    level = max(0, min(100, level))
    before = [(s, get_app_volume(s)) for s in sessions]
    for s in sessions:
        set_app_volume(s, level)
    actions.record(f"Set {app}'s volume to {level}%", undo=lambda: [set_app_volume(s, v) for s, v in before],
                   undo_text=f"put {app}'s volume back")
    return f"{app.capitalize()} volume set to {level} percent."


@skill("mic mute", r"^(?:please\s+)?(mute|unmute)\s+(?:my\s+|the\s+)?(?:mic|microphone)$",
       r"^(?:please\s+)?(?:turn|switch)\s+(on|off)\s+(?:my\s+|the\s+)?(?:mic|microphone)$",
       r"^(?:please\s+)?(?:turn|switch)\s+(?:my\s+|the\s+)?(?:mic|microphone)\s+(on|off)$",
       rf"{hi.LEAD}(?:mic|microphone)\s+(band|bandh|off|on|chalu|mute|unmute){hi.KAR}$",
       category="settings", help=["mute my mic", "unmute mic"])
def mic_mute(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    word = match.group(1)
    mute = word in ("mute", "off", "band", "bandh")
    if mute and not getattr(ctx.voice, "text_mode", False):
        if not ctx.confirm("If I mute the microphone I can't hear you either, until you unmute it from the taskbar. Mute it?"):
            return "Okay, the mic stays on."
    if not set_mic_mute(mute):
        return "I can't find a microphone."
    actions.record(f"{'Muted' if mute else 'Unmuted'} the microphone", undo=lambda: set_mic_mute(not mute),
                   undo_text=f"{'unmute' if mute else 'mute'} the microphone")
    return f"Microphone {'muted' if mute else 'on'}."


OUTPUT_WORDS = {"headphones": ("headphone", "headset", "earphone", "earbud", "buds", "airpods", "hands-free", "wh-", "wf-"),
                "speakers": ("speaker", "realtek", "laptop", "internal")}


@skill("audio output", r"^(?:please\s+)?(?:switch|change|set|move)\s+(?:the\s+)?(?:audio|sound|output|playback)(?:\s+output)?\s+"
                       r"(?:to|over to)\s+(?:my\s+|the\s+)?(.+?)$",
       r"^(?:please\s+)?(?:switch|change|go)\s+(?:over\s+)?to\s+(?:my\s+|the\s+)?(headphones?|headset|earphones?|earbuds?|speakers?)$",
       r"^(?:please\s+)?(?:play|use)\s+(?:the\s+)?(?:audio|sound)\s+(?:on|through|from)\s+(?:my\s+|the\s+)?(.+)$",
       rf"{hi.LEAD}(headphones?|headset|earphones?|speakers?)\s+(?:pe|par|mein)\s+(?:switch|laga|lagao|chala|chalao){hi.KAR}$",
       category="settings", help=["switch to headphones", "switch to speakers"])
def audio_output(ctx, command, match):
    wanted = match.group(1).strip()
    generic = re.fullmatch(r"(headphones?|headset|earphones?|earbuds?|speakers?)", wanted)
    if not generic and not re.search(r"\b(?:audio|sound|output|playback)\b", command):
        return None  # "switch to chrome" is a window
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    devices = output_devices()
    if not devices:
        return "I can't find any speakers or headphones."
    key = "headphones" if generic and not wanted.startswith("speaker") else "speakers" if generic else None
    if key:
        hits = [d for d in devices if any(w in d[0].lower() for w in OUTPUT_WORDS[key])]
    else:
        from jarvis.skills import app_index

        hits = [d for d in devices if app_index.score(wanted, d[0]) >= 0.6 or wanted.lower() in d[0].lower()]
    if not hits:
        names = ", ".join(d[0] for d in devices)
        return f"I can't find {wanted} connected. I can see: {names}."
    name, device_id, is_default = hits[0]
    if is_default:
        return f"Sound is already coming from {name}."
    previous = next((d for d in devices if d[2]), None)
    set_output(device_id)
    actions.record(f"Switched the sound output to {name}",
                   undo=(lambda: set_output(previous[1])) if previous else None,
                   undo_text=f"switch back to {previous[0]}" if previous else "")
    return f"Sound now plays through {name}."


# ---------------------------------------------------------------- recycle bin, temp files
def empty_bin():
    import ctypes

    flags = 0x1 | 0x2 | 0x4  # no confirmation dialog, no progress UI, no sound
    result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    return result in (0, -2147418113)  # S_OK, or E_UNEXPECTED when it was already empty


@skill("empty recycle bin", r"\b(?:empty|clear|clean)\s+(?:out\s+)?(?:the\s+|my\s+)?(?:recycle\s?bin|trash|bin)\b",
       rf"{hi.LEAD}recycle\s?bin\s+(?:khaali|khali|saaf|clear|empty){hi.KAR}$",
       category="settings", help=["empty the recycle bin"])
def empty_recycle_bin(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    if not ctx.confirm("Empty the Recycle Bin? Those files will be gone for good, and I can't undo it."):
        actions.note("Didn't empty the Recycle Bin (you said no)")
        return "Okay, I left the Recycle Bin alone."
    if not empty_bin():
        return "Windows wouldn't empty the Recycle Bin."
    actions.record("Emptied the Recycle Bin")
    return "Recycle Bin emptied."


def user_temp():
    """Your own temp folder (AppData\\Local\\Temp), never C:\\Windows\\Temp."""
    path = Path(os.environ.get("TEMP") or os.environ.get("TMP") or Path.home() / "AppData" / "Local" / "Temp")
    expected = Path.home() / "AppData" / "Local" / "Temp"
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(expected.resolve())):
        raise SpokenError("Your temp folder isn't where I expect it, so I won't clear it.")
    return path


def clear_temp(older_than_hours=24):
    """Delete files in the user temp folder older than a day (newer ones may be in use). Returns (count, bytes)."""
    root = user_temp()
    cutoff = time.time() - older_than_hours * 3600
    count = size = 0
    for item in list(root.iterdir()):
        try:
            if item.is_symlink():
                continue
            stamp = item.stat().st_mtime
            if stamp > cutoff:
                continue
            if item.is_dir():
                bytes_in = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item)
            else:
                bytes_in = item.stat().st_size
                item.unlink()
            count += 1
            size += bytes_in
        except OSError:
            continue  # in use: leave it
    return count, size


@skill("clear temp files", r"\b(?:clear|clean|delete|remove|empty)\s+(?:out\s+|up\s+)?(?:the\s+|my\s+|all\s+)?(?:temp|temporary|junk)\s+files?\b",
       r"\b(?:clean|clear)\s+(?:up\s+)?(?:the\s+)?temp(?: folder)?\b", rf"{hi.LEAD}temp\s+files?\s+(?:saaf|clear|delete){hi.KAR}$",
       category="settings", help=["clear temp files"])
def clear_temp_files(ctx, command, match):
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    root = user_temp()
    if not ctx.confirm(f"Delete the temp files in {root} that are more than a day old? This can't be undone."):
        actions.note("Didn't clear temp files (you said no)")
        return "Okay, I left them."
    count, size = clear_temp()
    actions.record(f"Cleared {count} temp items ({size / 1024**2:.0f} MB) from {root}")
    if not count:
        return "There was nothing old enough to clear."
    return f"Cleared {count} items, about {size / 1024**2:.0f} megabytes."


# ---------------------------------------------------------------- settings pages
SETTINGS_PAGES = {
    "display": "display", "screen": "display", "brightness": "display", "resolution": "display",
    "night light": "nightlight", "sound": "sound", "audio": "sound", "volume": "apps-volume", "app volume": "apps-volume",
    "wifi": "network-wifi", "wi-fi": "network-wifi", "wi fi": "network-wifi", "network": "network",
    "internet": "network", "ethernet": "network-ethernet", "vpn": "network-vpn", "hotspot": "network-mobilehotspot",
    "mobile hotspot": "network-mobilehotspot", "airplane mode": "network-airplanemode", "proxy": "network-proxy",
    "bluetooth": "bluetooth", "devices": "bluetooth", "printers": "printers", "printer": "printers", "mouse": "mousetouchpad",
    "touchpad": "devices-touchpad", "keyboard": "keyboard", "typing": "typing", "pen": "pen",
    "battery": "batterysaver", "battery saver": "batterysaver", "power": "powersleep", "sleep": "powersleep",
    "power and battery": "powersleep", "storage": "storagesense", "apps": "appsfeatures",
    "installed apps": "appsfeatures", "default apps": "defaultapps", "startup": "startupapps", "startup apps": "startupapps",
    "update": "windowsupdate", "updates": "windowsupdate", "windows update": "windowsupdate", "privacy": "privacy",
    "camera": "privacy-webcam", "microphone": "privacy-microphone", "mic": "privacy-microphone",
    "location": "privacy-location", "notifications": "notifications", "focus": "quiethours", "do not disturb": "notifications",
    "personalization": "personalization", "background": "personalization-background", "wallpaper": "personalization-background",
    "colors": "colors", "colours": "colors", "theme": "themes", "themes": "themes", "lock screen": "lockscreen",
    "taskbar": "taskbar", "start": "personalization-start", "fonts": "fonts", "accounts": "yourinfo",
    "sign in": "signinoptions", "sign-in": "signinoptions", "password": "signinoptions", "time": "dateandtime",
    "date": "dateandtime", "date and time": "dateandtime", "language": "regionlanguage", "region": "regionformatting",
    "accessibility": "easeofaccess", "narrator": "easeofaccess-narrator", "magnifier": "easeofaccess-magnifier",
    "gaming": "gaming-gamebar", "game mode": "gaming-gamemode", "multitasking": "multitasking", "clipboard": "clipboard",
    "about": "about", "system": "", "security": "windowsdefender", "windows security": "windowsdefender",
    "backup": "backup", "recovery": "recovery", "activation": "activation", "troubleshoot": "troubleshoot",
    "multiple displays": "display", "projection": "project", "nearby sharing": "crossdevice", "phone": "mobile-devices",
}


def open_settings_page(page):
    desktop.start(f"ms-settings:{page}")


@skill("open settings page", r"^(?:please\s+)?(?:open|show|go to|take me to)\s+(?:the\s+|my\s+)?(.+?)\s+settings?(?:\s+page)?$",
       r"^(?:please\s+)?(?:open\s+)?settings?\s+(?:for|of)\s+(?:the\s+|my\s+)?(.+)$",
       rf"{hi.LEAD}(.+?)\s+(?:ki|ke|ka)\s+settings?{hi.OPEN}$",
       category="settings", help=["open display settings", "open bluetooth settings", "open update settings"],
       tool="settings_page", params={"page": "e.g. display, sound, wifi, bluetooth, battery, apps, updates, privacy"},
       command="open {page} settings")
def open_settings(ctx, command, match):
    from jarvis.skills import app_index

    asked = re.sub(r"\s+(?:and|&)\s+", " and ", match.group(1).strip().lower())
    page = SETTINGS_PAGES.get(asked)
    if page is None:
        scored = sorted(((app_index.score(asked, name), name) for name in SETTINGS_PAGES), reverse=True)
        if scored and scored[0][0] >= 0.8:
            asked, page = scored[0][1], SETTINGS_PAGES[scored[0][1]]
    if page is None:
        if re.search(r"\b(?:chrome|vs ?code|spotify|whatsapp|zoom|teams|discord|game)\b", asked):
            return None  # an app's own settings
        page = ""
        asked = "the main"
    if not system.IS_WINDOWS:
        return NOT_WINDOWS
    open_settings_page(page)
    actions.record(f"Opened {asked} settings (ms-settings:{page})")
    return f"Opening {asked} settings."
