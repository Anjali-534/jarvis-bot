"""The listening loop, shared by the console (main.py) and the tray app (run_jarvis.pyw).

The tray, the hotkey and voice commands all control one Assistant:
wake() / sleep() / set_muted() / stop() are thread-safe flags the loop checks.
"""
import logging
import re
import sys
import threading
import time

import config
from jarvis.router import Context, handle, run_startup

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- wake word
def _wake_words():
    words = set(config.WAKE_WORDS)
    words |= {w.replace("ok ", "okay ") for w in words if w.startswith("ok ")}
    words |= {"जार्विस", "हे जार्विस"}  # how Hindi speech recognition writes "Jarvis"
    return sorted(words, key=len, reverse=True)


# If a trailing "jarvis" follows one of these, it's part of the command (e.g. a project name).
_DANGLING = re.compile(r"\b(?:of|in|for|on|to|at|about|from|with|into|called|named|open|run|start|pull|"
                       r"launch|close|status|work on)$")

# If a leading "jarvis" is followed by one of these, it's the subject ("जार्विस का गिट स्टेटस", "jarvis project ...").
_SUBJECT_NEXT = re.compile(r"^(?:का|की|के|को|में|मे|से|पर|प्रोजेक्ट|project|folder|repo)(?:\s|$)")


def strip_wake_word(text, wake_words=None):
    """Return (woke, command). The wake word is stripped from the start, end or middle."""
    words = wake_words or _wake_words()
    t = re.sub(r"\s+", " ", (text or "").lower()).strip()
    alternatives = "|".join(re.escape(w) for w in words)
    if re.fullmatch(rf"(?:please\s+)?(?:turn off|shut ?down|stop|close|bye|goodbye|good bye|exit|quit)[\s,]+(?:{alternatives})[.!]?", t):
        return True, "exit"
    for word in words:
        w = re.escape(word)
        lead = re.fullmatch(rf"{w}(?!\w)[\s,.!?]*(.*)", t)
        if lead:
            rest = lead.group(1).strip()
            if _SUBJECT_NEXT.match(rest):
                return True, t  # the name is the project, not the wake word
            return True, rest
        trail = re.fullmatch(rf"(.*?)[\s,]*\b{w}[\s.!?]*", t)
        if trail:
            before = trail.group(1).strip()
            if _DANGLING.search(before):
                return True, t  # "git status of jarvis": the name is the project, not the wake word
            return True, before
    for word in words:
        middle = re.search(rf"\b{re.escape(word)}\b[\s,]*", t)
        if middle:
            after = t[middle.end():].strip()
            return True, after or t[:middle.start()].strip(" ,")
    return False, t


def beep():
    """A short 'I'm listening' chime (a beep, not speech, so the mic doesn't hear words)."""
    if sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(880, 120)
        except Exception:
            pass


# ---------------------------------------------------------------- the loop
class Assistant:
    LISTENING, SLEEPING, MUTED = "Listening", "Sleeping", "Muted"

    def __init__(self, voice, wake_enabled=True, on_status=None, on_exit=None):
        self.voice = voice
        self.ctx = Context(voice)
        self.ctx.state["assistant"] = self  # lets skills reach the tray/loop if they need to
        self.wake_enabled = wake_enabled and not getattr(voice, "text_mode", False)
        self.mic_muted = False
        self.on_status = on_status or (lambda status: None)
        self.on_exit = on_exit
        self._forced = threading.Event()   # hotkey/tray "wake up": accept the next phrase without the wake word
        self._last_reply = time.monotonic()
        self._misses = 0
        self._last_status = None

    # ------------------------------------------------------------ status and controls (thread-safe flags)
    @property
    def status(self):
        if self.mic_muted:
            return self.MUTED
        return self.SLEEPING if self.ctx.sleeping else self.LISTENING

    def _notify_status(self):
        status = self.status
        if status != self._last_status:
            self._last_status = status
            log.info("[status] %s", status)
            try:
                self.on_status(status)
            except Exception:
                log.exception("status callback failed")

    def wake(self):
        """Hotkey / tray: listen right now, no wake word needed for the next phrase."""
        self.mic_muted = False
        self.ctx.sleeping = False
        self._forced.set()
        self._last_reply = time.monotonic()
        self._notify_status()
        beep()

    def sleep(self):
        self.ctx.sleeping = True
        self._forced.clear()
        self._notify_status()

    def set_muted(self, muted):
        self.mic_muted = bool(muted)
        self._notify_status()

    def toggle_mute(self):
        self.set_muted(not self.mic_muted)

    def stop(self):
        self.ctx.running = False

    @property
    def running(self):
        return self.ctx.running

    # ------------------------------------------------------------ one utterance
    def step(self, heard):
        """Process one thing heard. Returns the skill that handled it, or None if ignored."""
        if not heard:
            if not self.wake_enabled and not self.ctx.sleeping:
                self._misses += 1
                if self._misses >= 2:
                    self.ctx.say("Sorry, I didn't catch that.")
                    self._misses = 0
            return None
        self._misses = 0
        woke, command = strip_wake_word(heard)
        forced = self._forced.is_set()
        if forced:
            self._forced.clear()
            woke = True
        if self.ctx.sleeping:
            if not woke:
                return None
            self.ctx.sleeping = False
            self._notify_status()
            if not command:
                self.ctx.say("I'm back. Yes?")
                self._last_reply = time.monotonic()
                return None
        elif self.wake_enabled and not woke and time.monotonic() - self._last_reply > config.FOLLOW_UP_SECONDS:
            return None
        handled = None
        if not command:
            self.ctx.say("Yes?")
        else:
            handled = handle(self.ctx, command)
        self._last_reply = time.monotonic()
        self._notify_status()  # "go to sleep" may have changed it
        return handled

    def greet(self):
        from jarvis.skills.general import time_of_day

        self.ctx.say(f"Good {time_of_day()}, {config.USER_NAME}. {config.ASSISTANT_NAME} is online.")
        name = config.ASSISTANT_NAME
        log.info(f"Say '{name}, help' to hear what I can do. Full list: COMMANDS.md"
                 if self.wake_enabled else "Say 'help' to hear what I can do. Full list: COMMANDS.md")
        if config.HOTKEY and not getattr(self.voice, "text_mode", False):
            log.info("Or press %s to talk without the wake word.", config.HOTKEY.upper())
        self._last_reply = time.monotonic()
        self._notify_status()

    def run(self):
        """The loop. Returns when someone says goodbye, stop() is called, or Ctrl+C."""
        self.greet()
        run_startup(self.ctx)  # e.g. calendar reminders, if Google is connected
        try:
            while self.ctx.running:
                if self.mic_muted:
                    time.sleep(0.25)
                    continue
                self.step(self.voice.listen())
        except KeyboardInterrupt:
            self.ctx.say(f"Goodbye, {config.USER_NAME}.")
        finally:
            self.ctx.running = False
            if self.on_exit:
                try:
                    self.on_exit()
                except Exception:
                    log.exception("exit callback failed")
        return self.ctx
