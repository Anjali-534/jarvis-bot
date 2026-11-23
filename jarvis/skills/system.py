"""Windows laptop controls: volume, media, brightness, screenshots, battery, lock, power."""
import logging
import os
import re
import subprocess
import sys
from datetime import datetime

import config
from jarvis.router import skill
from jarvis.timeparse import find_duration, say_duration

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
NOT_WINDOWS = "Sorry, that only works on Windows."
DEVICE = r"(?:\s+(?:the|my))?(?:\s+(?:laptop|pc|computer|system|windows|machine))?"


# Side effects live in these helpers so tests can replace them.
def press(key, presses=1):
    import pyautogui

    pyautogui.press(key, presses=presses, interval=0.03)


def hotkey(*keys):
    import pyautogui

    pyautogui.hotkey(*keys)


def run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def powershell(script, timeout=20):
    return run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)


def windows_only(fn):
    def wrapper(ctx, command, match):
        return fn(ctx, command, match) if IS_WINDOWS else NOT_WINDOWS

    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------- volume and media
def _steps(command, default=5):
    found = re.search(r"\bby\s+(\d{1,3})", command)
    return max(1, min(50, int(found.group(1)) // 2)) if found else default


@skill("volume up", r"\bvolume up\b", r"\b(?:increase|raise|turn up)\s+(?:the\s+)?volume\b", r"\bturn (?:it|the volume) up\b",
       r"\blouder\b", r"\bvolume (?:higher|increase)\b", category='laptop', help=['volume up', 'volume up by 20'])
@windows_only
def volume_up(ctx, command, match):
    press("volumeup", _steps(command))
    return ""


@skill("volume down", r"\bvolume down\b", r"\b(?:decrease|lower|reduce|turn down)\s+(?:the\s+)?volume\b",
       r"\bturn (?:it|the volume) down\b", r"\b(?:quieter|softer)\b", r"\bvolume (?:lower|decrease)\b", category='laptop', help=['turn down the volume'])
@windows_only
def volume_down(ctx, command, match):
    press("volumedown", _steps(command))
    return ""


@skill("mute", r"^(?:please\s+)?(?:un)?mute(?:\s+(?:the\s+)?(?:volume|sound|audio|laptop|speakers?))?$",
       r"\b(?:un)?mute the (?:volume|sound|audio)\b", category='laptop', help=['mute'])
@windows_only
def mute(ctx, command, match):
    press("volumemute")
    return ""


@skill("play pause", r"^(?:please\s+)?(?:pause|resume|unpause)(?:\s+(?:the\s+)?(?:music|song|video|media|playback|it))?$",
       r"\bplay\s*pause\b", r"^(?:stop|pause) the (?:music|song|video)$", category='laptop', help=['pause the music', 'resume'])
@windows_only
def play_pause(ctx, command, match):
    press("playpause")
    return ""


@skill("next track", r"^(?:next|skip)$", r"\b(?:next|skip)(?:\s+(?:the|this))?\s+(?:song|track|video)\b", r"\bplay (?:the )?next\b", category='laptop', help=['next song'])
@windows_only
def next_track(ctx, command, match):
    press("nexttrack")
    return ""


@skill("previous track", r"^previous$", r"\bprevious\s+(?:song|track|video)\b", r"\b(?:last|go back to the)\s+(?:song|track)\b", category='laptop', help=['previous song'])
@windows_only
def previous_track(ctx, command, match):
    press("prevtrack")
    return ""


# ---------------------------------------------------------------- brightness
def get_brightness():
    out = powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
    found = re.search(r"\d+", out.stdout or "")
    return int(found.group()) if out.returncode == 0 and found else None


def set_brightness(level):
    level = max(0, min(100, int(level)))
    out = powershell(
        "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
        f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={level}}} | Out-Null"
    )
    return out.returncode == 0 and not (out.stderr or "").strip()


@skill("brightness", r"\bbrightness\b", r"\b(?:brighter|dimmer)\b", r"\bdim the screen\b", category='laptop', help=['brightness 70', 'brightness up'])
@windows_only
def brightness(ctx, command, match):
    step_match = re.search(r"\bby\s+(\d{1,3})", command)
    exact = None if step_match else re.search(r"(\d{1,3})\s*(?:%|percent)?", command)
    if exact:
        target = int(exact.group(1))
    else:
        step = int(step_match.group(1)) if step_match else 20
        current = get_brightness()
        if current is None:
            return "I couldn't read the brightness. It only works on a laptop's built-in screen."
        if re.search(r"\b(?:up|increase|raise|brighter|more|higher)\b", command):
            target = current + step
        elif re.search(r"\b(?:down|decrease|lower|reduce|dim|dimmer|less)\b", command):
            target = current - step
        else:
            return f"Brightness is at {current} percent."
    target = max(0, min(100, target))
    if not set_brightness(target):
        return "I couldn't change the brightness. It only works on a laptop's built-in screen."
    return f"Brightness set to {target} percent."


# ---------------------------------------------------------------- screenshot, battery, status
@skill("screenshot", r"\bscreen\s?shot\b", r"\bcapture (?:the |my )?screen\b", r"\bsnap the screen\b", category='laptop', help=['take a screenshot'])
def screenshot(ctx, command, match):
    import pyautogui

    folder = config.SCREENSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Screenshot {datetime.now():%Y-%m-%d %H-%M-%S}.png"
    pyautogui.screenshot(str(path))
    log.info(f"[screenshot] saved to {path}")
    return f"Screenshot saved in your {folder.name} folder."


@skill("battery", r"\bbattery\b", r"\bcharg(?:e|ing)\s+(?:level|status|left|percentage)\b", r"\bhow much charge\b", category='laptop', help=['battery status'])
def battery(ctx, command, match):
    import psutil

    info = psutil.sensors_battery()
    if info is None:
        return "I can't find a battery on this computer."
    percent = round(info.percent)
    if info.power_plugged:
        return f"Battery is at {percent} percent and charging."
    reply = f"Battery is at {percent} percent"
    if info.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) and info.secsleft > 0:
        reply += f", about {say_duration(info.secsleft // 60 * 60)} left"
    reply += "."
    if percent < 20:
        reply += " That's low, please plug in the charger."
    return reply


@skill("system status", r"\bsystem (?:status|info|health|usage|stats)\b", r"\bcpu\b", r"\b(?:ram|memory) usage\b",
       r"\bdisk (?:space|usage)\b", r"\bhow is my (?:laptop|pc|computer|system)(?: doing)?\b", category='laptop', help=['system status'])
def system_status(ctx, command, match):
    import psutil

    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    drive = (os.environ.get("SystemDrive", "C:") + "\\") if IS_WINDOWS else "/"
    disk = psutil.disk_usage(drive)
    return (f"CPU is at {cpu:.0f} percent. Memory is {ram.percent:.0f} percent used. "
            f"Your main drive has {disk.free / 1024**3:.0f} gigabytes free out of {disk.total / 1024**3:.0f}.")


# ---------------------------------------------------------------- lock, power, sleep
@skill("lock", r"\block\s+(?:the\s+|my\s+)?(?:screen|laptop|pc|computer|system|windows)\b", r"^lock(?: it)?$", category='laptop', help=['lock the laptop'])
@windows_only
def lock(ctx, command, match):
    ctx.say("Locking your laptop.")
    run(["rundll32.exe", "user32.dll,LockWorkStation"])
    return ""


@skill("cancel shutdown", r"\b(?:cancel|abort|stop)\b.*\b(?:shut\s?down|restart|reboot)\b", category='laptop', help=['cancel shutdown'])
@windows_only
def cancel_shutdown(ctx, command, match):
    result = run(["shutdown", "/a"])
    if result.returncode == 0:
        return "Okay, I've cancelled the scheduled shutdown."
    return "There was no shutdown or restart scheduled."


@skill("power", rf"^(?:please\s+)?(?:shut\s?down|restart|reboot){DEVICE}(?:\s+(?:in|after)\s+.+)?$",
       r"\b(?:turn|power|switch)\s+off\b(?:\s+(?:the|my))?\s+(?:laptop|pc|computer|system|machine)\b",
       r"\bshut\s?down\b(?:\s+(?:the|my))?\s+(?:laptop|pc|computer|system|machine)\b",
       r"\b(?:restart|reboot)\b(?:\s+(?:the|my))?\s+(?:laptop|pc|computer|system|machine)\b", category='laptop', help=['shut down the laptop', 'restart in 10 minutes'])
@windows_only
def power(ctx, command, match):
    restart = bool(re.search(r"\b(?:restart|reboot)\b", command))
    action, flag = ("restart", "/r") if restart else ("shut down", "/s")
    found = find_duration(command)
    delay = min(found[0], 315360000) if found else 15
    if not ctx.confirm(f"Are you sure you want to {action} the laptop in {say_duration(delay)}?"):
        return f"Okay, I won't {action} the laptop."
    result = run(["shutdown", flag, "/t", str(delay)])
    if result.returncode != 0:
        return f"Windows wouldn't let me {action}. Maybe one is already scheduled; say cancel shutdown first."
    return f"Okay. The laptop will {action} in {say_duration(delay)}. Say cancel shutdown if you change your mind."


@skill("sleep pc", r"\b(?:sleep|hibernate)\b.*\b(?:laptop|pc|computer|system|machine)\b",
       r"\b(?:laptop|pc|computer|system|machine)\b.*\b(?:to\s+)?sleep\b", category='laptop', help=['put the laptop to sleep'])
@windows_only
def sleep_pc(ctx, command, match):
    if not ctx.confirm("Put the laptop to sleep now?"):
        return "Okay, staying awake."
    ctx.say("Good night. Putting the laptop to sleep.")
    powershell("Add-Type -AssemblyName System.Windows.Forms; "
               "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false) | Out-Null")
    return ""


# ---------------------------------------------------------------- start with Windows
_BOOT = r"(?:windows|startup|start up|login|log in|boot|my laptop|the laptop|my computer)"


@skill("stop autostart", rf"\b(?:don't|dont|do not|never|stop)\s+(?:auto\s?)?(?:start|launch|run|open)(?:ing)?\b.*\b{_BOOT}\b",
       r"\b(?:disable|remove|turn off|switch off)\s+(?:the\s+)?(?:auto\s?start|autostart|startup|start with windows)\b",
       category="laptop", help=["don't start with Windows"])
@windows_only
def stop_autostart(ctx, command, match):
    from jarvis import startup

    if not startup.is_installed():
        return f"{config.ASSISTANT_NAME} isn't set to start with Windows."
    if not ctx.confirm(f"Stop {config.ASSISTANT_NAME} from starting when you log in?"):
        return "Okay, I'll keep starting with Windows."
    startup.uninstall()
    return f"Done. {config.ASSISTANT_NAME} won't start with Windows any more."


@skill("autostart", rf"\b(?:auto\s?)?(?:start|launch|run|open)\s+(?:(?:jarvis|yourself|automatically)\s+){{0,2}}(?:with|on|at|when)\s+(?:i\s+)?{_BOOT}\b",
       r"\b(?:enable|turn on|switch on)\s+(?:the\s+)?(?:auto\s?start|autostart|start with windows)\b",
       category="laptop", help=["start with Windows"])
@windows_only
def autostart(ctx, command, match):
    from jarvis import startup

    if startup.is_installed():
        return f"{config.ASSISTANT_NAME} already starts with Windows."
    if not ctx.confirm(f"Start {config.ASSISTANT_NAME} in the tray every time you log in?"):
        return "Okay, I won't."
    startup.install()
    return f"Done. {config.ASSISTANT_NAME} will start in the tray when you log in."


# ---------------------------------------------------------------- microphone
@skill("recalibrate mic", r"\b(?:re-?calibrate|calibrate)\s+(?:the\s+|my\s+)?(?:mic|microphone)\b",
       r"\b(?:mic|microphone)\s+(?:re-?calibrat\w*|calibrat\w*)\b", category="laptop", help=["recalibrate mic"])
def recalibrate_mic(ctx, command, match):
    calibrate = getattr(ctx.voice, "calibrate", None)
    if getattr(ctx.voice, "text_mode", False) or calibrate is None:
        return "The microphone isn't in use in text mode."
    ctx.say("Stay quiet for a moment.")
    threshold = calibrate()
    if threshold is None:
        return "I couldn't reach the microphone."
    return "Done. I've re-measured the background noise."


SAMPLE_PHRASES = [
    "what's the weather",
    "open notepad",
    "set a timer for five minutes",
    "what's the AQI in Rohini",
    "check my email",
]


def save_wav(path, pcm, rate=16000):
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)


@skill("record samples", r"\brecord\s+(?:voice\s+|speech\s+|audio\s+)?samples?\b", category="laptop",
       help=["record samples"])
def record_samples(ctx, command, match):
    record = getattr(ctx.voice, "record_sample", None)
    if getattr(ctx.voice, "text_mode", False) or record is None:
        return "Recording samples needs the microphone. Start me without --text."
    folder = config.SAMPLES_DIR
    saved = 0
    for number, phrase in enumerate(SAMPLE_PHRASES, 1):
        ctx.say(f"Sample {number}. After the beep, say: {phrase}.")
        from jarvis.voice import beep

        beep()
        pcm = record()
        if not pcm:
            ctx.say("I didn't hear anything, skipping that one.")
            continue
        save_wav(folder / f"sample{number}.wav", pcm)
        (folder / f"sample{number}.txt").write_text(phrase, encoding="utf-8")
        saved += 1
    if not saved:
        return "I didn't record any samples."
    return (f"Saved {saved} samples in data, samples. Run python -m jarvis.benchmark to compare the "
            "speech engines.")
