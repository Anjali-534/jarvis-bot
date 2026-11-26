"""Telegram phone remote: control Jarvis on the laptop from your phone, by text or voice note.

Turned on by TELEGRAM_BOT_TOKEN (from @BotFather). It uses long polling (python-telegram-bot), so no server or
webhook is needed, and runs on a background thread inside the tray app and main.py.

Security, most important first:
* Only ONE chat can control Jarvis. The first "/start" asks for a one-time 6-digit pairing code, which Jarvis
  shows in the laptop terminal and as a toast (valid 5 minutes, 3 tries). After pairing, the chat id is saved in
  data/telegram.json; every other chat is ignored (no reply at all) and logged. "/unpair" removes it.
* Messages that arrived while the laptop was off are dropped, never run late.
* Nothing runs except the normal Jarvis router and skills: no shell commands, no code.
* Every question a skill asks becomes [Yes] [No] buttons that expire after 60 s (no answer = no). Risky actions
  (shutdown/restart, delete/move files, close apps, WhatsApp/email drafts, calendar changes, git pull, run
  project...) always ask from the phone, even the ones that don't ask on the laptop.
* Typing into windows, key presses and the clipboard are off from the phone unless TELEGRAM_ALLOW_KEYBOARD=true.
* Screenshots ask first: the screen may show private things.
* Every phone command, refusal and stranger is written to data/logs/actions.log.

Replies come back as text; Jarvis only speaks on the laptop if you add "laptop pe bol" / "say it out loud".
"""
import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from jarvis import actions, notify, persona
from jarvis.router import Context, on_startup

log = logging.getLogger(__name__)

PAIRING_MINUTES = 5
PAIRING_TRIES = 3
NEW_CODE_EVERY = 30          # seconds between new pairing codes (no flooding)
MAX_MESSAGE = 3900           # Telegram allows 4096 characters
KEYBOARD_OFF = ("Typing, keys and the clipboard are off from the phone. Set TELEGRAM_ALLOW_KEYBOARD=true in "
                ".env if you want them.")
# Skills that don't ask on the laptop but must from the phone (on top of every skill marked risky)
# ("lock" isn't here: locking is safe, so /lock and "lock the laptop" act at once)
CONFIRM_FROM_PHONE = {"close window", "close all apps", "sleep pc", "power", "restart app", "empty recycle bin",
                      "clear temp files", "mic mute"}
BATTERY_LOW, BATTERY_REARM = 15, 20     # alert below 15% on battery; again only after charging or back above 20%
RETRY_SECONDS = (5, 10, 30, 60)          # reconnect back-off when Telegram or the network is down
SEND_FILE = re.compile(r"^(?:please\s+)?(?:send|share|forward|bhej(?:o|do|de)?)\s+(?:me\s+|mujhe\s+)?(?:my\s+|the\s+|meri\s+|mera\s+)?"
                       r"(.+?)(?:\s+(?:file|document|doc|here|to me|to my phone|on telegram|yahan|idhar))*$"
                       r"|^(?:meri|mera|mere|my)?\s*(.+?)\s+(?:bhej|bhejo|bhej do|bhej de|send kar\w*)(?:\s+(?:do|de|na))?$",
                       re.IGNORECASE)
NOT_A_FILE = re.compile(r"\b(?:whatsapp|email|e-mail|mail|message|msg|sms|text to|reminder|screenshot|screen|location|"
                        r"photo of (?:the )?screen)\b", re.IGNORECASE)
# Never sent, wherever they are: keys, tokens, password vaults, .env files
SECRET_FILES = re.compile(r"(?:^\.env|\.env$|\.pem$|\.key$|\.pfx$|\.p12$|\.kdbx$|\.ppk$|^id_(?:rsa|ed25519|ecdsa)|"
                          r"token|credential|secret|password|wallet)", re.IGNORECASE)
ALOUD = re.compile(r"\s*,?\s*\b(?:laptop (?:pe|par|pr) (?:bol(?:o|na)?|bata(?:o)?|suna(?:o)?)|bol ke bata(?:o)?|"
                   r"say it out loud|out loud|aloud|speak it|read it out|zor se bol(?:o)?)\b\s*", re.IGNORECASE)
SCREENSHOT = re.compile(r"^/screenshot(?:@\w+)?$|\bscreenshot\s+(?:bhej\w*|send|de do|dikha\w*)\b|"
                        r"\b(?:send|share|show)\s+(?:me\s+)?(?:a\s+|the\s+)?(?:screenshot|screen)\b|"
                        r"\bscreen\s+(?:dikha\w*|bhej\w*)\b", re.IGNORECASE)
HELP = (
    "I'm Jarvis on your laptop. Text me anything you'd say out loud: 'open chrome', 'volume 30', 'what's playing', "
    "'Dhruv ka birthday kab hai?', 'petrol price today', or just chat. Voice notes work too.\n\n"
    "/status: battery, charging, CPU, RAM, disk\n"
    "/screenshot: a screenshot (asks first)\n"
    "/unpair: stop this chat controlling the laptop\n\n"
    "I reply here and stay quiet on the laptop; add 'laptop pe bol' or 'say it out loud' to hear it there. "
    "Risky things (shutdown, deleting or moving files, closing apps, messages, calendar changes) ask with "
    "Yes/No buttons that expire in a minute."
)


# ---------------------------------------------------------------- the paired chat
def load_state():
    try:
        return json.loads(config.TELEGRAM_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    config.TELEGRAM_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TELEGRAM_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def paired_chat():
    chat = load_state().get("chat_id")
    return int(chat) if chat is not None else None


# ---------------------------------------------------------------- the phone as Jarvis's "voice"
@dataclass
class Question:
    text: str
    token: str
    expires: float
    answer: str | None = None
    event: threading.Event = field(default_factory=threading.Event)
    message_id: object = None


class PhoneVoice:
    """What a skill says is collected and sent as one Telegram message; what it asks becomes [Yes] [No] buttons."""

    text_mode = True     # no microphone here: skills skip mic-only things
    is_remote = True     # e.g. the Wi-Fi skill warns that turning Wi-Fi off cuts this chat off too

    def __init__(self, owner):
        self.owner = owner
        self.buffer = []
        self.active = False
        self.aloud = False

    def speak(self, text, wait=True):
        if self.aloud and self.owner.laptop_voice is not None:
            try:
                self.owner.laptop_voice.speak(text, wait=False)
            except Exception:
                log.debug("laptop voice failed", exc_info=True)
        from jarvis import router

        if self.active and (router._is_one_sec(text) or persona.is_line("checking_online", text)):
            self.owner.send(text)  # "Ruk, dekhta hoon": on its own straight away, like a typing hint
        elif self.active:
            self.buffer.append(text)
        elif not self.owner._just_forwarded(text):  # a reminder or timer firing later: send it straight away
            self.owner.send(text)                     # (unless its notification just went to the phone)

    def flush(self):
        if self.buffer:
            text, self.buffer = " ".join(self.buffer), []
            self.owner.send(text)

    def listen(self, *args, **kwargs):
        question = self.buffer.pop() if self.buffer else "Yes or no?"
        self.flush()
        return self.owner.ask(question)

    def wait_idle(self):
        return None


# ---------------------------------------------------------------- the remote itself (transport-agnostic)
class Remote:
    """All the logic, with the Telegram calls injected (tests pass a fake transport).

    transport.send(chat_id, text), transport.ask(chat_id, text, token) -> message id,
    transport.expire(chat_id, message_id, text), transport.photo(chat_id, path, caption)
    """

    def __init__(self, transport, laptop_ctx=None):
        self.transport = transport
        self.laptop_ctx = laptop_ctx
        self.laptop_voice = getattr(laptop_ctx, "voice", None)
        self.voice = PhoneVoice(self)
        self.ctx = Context(self.voice)
        self.ctx.state.update(confirm_risky=True, confirm_skills=CONFIRM_FROM_PHONE, remote=True)
        if not config.TELEGRAM_ALLOW_KEYBOARD:
            self.ctx.state["blocked_modules"] = {"jarvis.skills.keyboard_control": KEYBOARD_OFF}
        self.lock = threading.Lock()
        self.question = None
        self.pairing = None
        self.last_code_at = 0.0
        self.worker = None
        self.recent, self.forwarded = [], []
        self.battery_warned = False
        self.announced = set()

    # ---------------------------------------------------------------- sending
    @property
    def chat(self):
        return paired_chat()

    def send(self, text, chat=None):
        """Send text (split into Telegram-sized pieces). Never raises: Telegram being down mustn't break Jarvis."""
        chat = chat or self.chat
        if chat is None or not text:
            return False
        try:
            for start in range(0, len(text), MAX_MESSAGE):
                self.transport.send(chat, text[start:start + MAX_MESSAGE])
        except Exception as exc:
            log.warning("[telegram] couldn't send a message (%s); is the network down?", exc)
            return False
        self.recent.append((time.monotonic(), text))
        del self.recent[:-20]
        return True

    def _typing_while(self, done):
        """Show "typing..." in the chat until `done` is set (Telegram drops it after 5 s, so it's renewed)."""
        chat = self.chat
        while chat is not None and not done.is_set():
            try:
                self.transport.typing(chat)
            except Exception:
                log.debug("couldn't send typing", exc_info=True)
            done.wait(4)

    # ---------------------------------------------------------------- notifications from the laptop
    def forward(self, title, message):
        """A reminder, timer, calendar alert or memory date from the laptop, sent to the phone (TELEGRAM_NOTIFY)."""
        if not config.TELEGRAM_NOTIFY or self.chat is None:
            return False
        self.forwarded.append((time.monotonic(), message))
        del self.forwarded[:-20]
        return self.send(f"{title}: {message}")

    def _just_forwarded(self, text):
        now = time.monotonic()
        return any(now - at < 15 and message and message in text for at, message in self.forwarded)

    def battery_alert(self, percent, plugged):
        """'Battery at 14%...' once per drop below BATTERY_LOW (re-armed when charging or back above 20%)."""
        if plugged or percent >= BATTERY_REARM:
            self.battery_warned = False
            return None
        if percent < BATTERY_LOW and not self.battery_warned:
            self.battery_warned = True
            return f"Battery at {round(percent)}% and not charging. Plug in the laptop."
        return None

    def check_battery(self):
        import psutil

        info = psutil.sensors_battery()
        if info is None:
            return None
        message = self.battery_alert(info.percent, info.power_plugged)
        if message and config.TELEGRAM_NOTIFY:
            actions.note(f"Telegram: low battery alert ({round(info.percent)}%)")
            self.send(message)
        return message

    def announce(self, started):
        """'Jarvis started / stopped on the laptop' (TELEGRAM_NOTIFY), once each."""
        key = "started" if started else "stopped"
        if not config.TELEGRAM_NOTIFY or key in self.announced or self.chat is None:
            return False
        self.announced.add(key)
        return self.send(f"{config.ASSISTANT_NAME} {'started' if started else 'stopped'} on the laptop "
                         f"({datetime.now():%I:%M %p}).")

    def ask(self, text):
        """Send [Yes] [No] buttons and wait (60 s). Returns 'yes', 'no', a typed answer, or '' when it expires."""
        token = secrets.token_hex(4)
        question = Question(text, token, time.monotonic() + config.TELEGRAM_CONFIRM_SECONDS)
        self.question = question
        try:
            question.message_id = self.transport.ask(self.chat, text, token)
        except Exception:
            log.exception("[telegram] couldn't send a question")
            self.question = None
            return ""
        answered = question.event.wait(config.TELEGRAM_CONFIRM_SECONDS)
        self.question = None
        if not answered:
            actions.note(f"Telegram: no answer in {config.TELEGRAM_CONFIRM_SECONDS} s to '{text}' (treated as no)")
            try:
                self.transport.expire(self.chat, question.message_id, f"{text}\n(expired: I didn't do it)")
            except Exception:
                log.debug("couldn't mark the question expired", exc_info=True)
            return ""
        return question.answer or ""

    # ---------------------------------------------------------------- who is talking
    def _stranger(self, chat_id, who, what):
        log.warning("[telegram] ignored %s from chat %s (%s): only the paired chat can control Jarvis", what,
                    chat_id, who or "unknown")
        actions.note(f"Telegram: ignored {what} from an unpaired chat {chat_id} ({who or 'unknown'})")

    def _pairing(self, chat_id, text, who):
        """Not paired yet: /start gives a one-time code on the LAPTOP; the phone must send it back."""
        now = time.monotonic()
        text = (text or "").strip()
        if text.split("@")[0] == "/start":
            if self.pairing and self.pairing["chat"] == chat_id and now < self.pairing["expires"]:
                self.send("Send me the 6-digit code shown on your laptop.", chat_id)
                return
            if now - self.last_code_at < NEW_CODE_EVERY:
                self._stranger(chat_id, who, "a /start during a pairing")
                return
            code = f"{secrets.randbelow(10 ** 6):06d}"
            self.pairing = {"chat": chat_id, "code": code, "expires": now + PAIRING_MINUTES * 60, "tries": 0}
            self.last_code_at = now
            log.warning("\n[telegram] Pairing request from %s (chat %s). If that's you, send this code from your "
                        "phone: %s  (valid %d minutes)\n", who or "unknown", chat_id, code, PAIRING_MINUTES)
            notify.toast("Telegram pairing code", f"{code}  (from {who or 'a Telegram user'}; valid {PAIRING_MINUTES} min)")
            actions.note(f"Telegram: pairing code shown for chat {chat_id} ({who or 'unknown'})")
            self.send("Hi! To pair this chat with your laptop, send me the 6-digit code shown on the laptop "
                      "(terminal and notification).", chat_id)
            return
        pending = self.pairing
        if not pending or pending["chat"] != chat_id or now > pending["expires"]:
            self._stranger(chat_id, who, "a message before pairing")
            return
        if secrets.compare_digest(re.sub(r"\D", "", text), pending["code"]):
            save_state({"chat_id": chat_id, "paired_at": datetime.now().isoformat(timespec="seconds"),
                        "who": who or ""})
            self.pairing = None
            actions.record(f"Paired Telegram chat {chat_id} ({who or 'unknown'})")
            notify.toast("Telegram", f"Paired with {who or 'your phone'}.")
            self.send("Paired! This chat now controls Jarvis on your laptop.\n\n" + HELP, chat_id)
            return
        pending["tries"] += 1
        actions.note(f"Telegram: wrong pairing code from chat {chat_id}")
        if pending["tries"] >= PAIRING_TRIES:
            self.pairing = None
            self.send("Too many wrong codes. Send /start to get a new one.", chat_id)
        else:
            self.send(f"That's not the code. {PAIRING_TRIES - pending['tries']} tries left.", chat_id)

    def authorized(self, chat_id, text=None, who=None, what="a message"):
        """True for the paired chat. Strangers are ignored and logged; before pairing, /start pairs."""
        paired = self.chat
        if paired is None:
            self._pairing(chat_id, text, who)
            return False
        if chat_id != paired:
            self._stranger(chat_id, who, what)
            return False
        return True

    # ---------------------------------------------------------------- incoming
    def on_message(self, chat_id, text, who=None):
        """A text from Telegram. Returns quickly; the command runs on a worker thread."""
        if not self.authorized(chat_id, text, who):
            return
        text = (text or "").strip()
        if not text:
            return
        if self.question is not None:  # a typed answer to the pending question ("the second one", "no")
            self.question.answer = text.lower()
            self.question.event.set()
            return
        command = text.split("@")[0].lower() if text.startswith("/") else None
        if command in ("/start", "/help"):
            self.send("Already paired with this chat.\n\n" + HELP if command == "/start" else HELP)
            return
        if command == "/status":
            self._spawn(lambda: self.send(status_text(self.laptop_ctx)))
            return
        if command == "/unpair":
            self._spawn(self._unpair)
            return
        if SCREENSHOT.search(text):
            self._spawn(self._screenshot)
            return
        if command == "/lock":
            self._spawn(lambda: self.run_command("lock the laptop"))  # safe: no confirmation
            return
        wanted = SEND_FILE.search(text)
        if wanted and not command and not NOT_A_FILE.search(text):
            query = (wanted.group(1) or wanted.group(2) or "").strip()
            self._spawn(lambda: self._send_file(query, text))
            return
        if text.startswith("/"):
            self.send("I don't know that one. Try /help, or just type what you want.")
            return
        self._spawn(lambda: self.run_command(text))

    def on_button(self, chat_id, data, who=None):
        """A [Yes] / [No] press. Returns the text to show on the button message, or None."""
        if not self.authorized(chat_id, who=who, what="a button press"):
            return None
        token, _, choice = (data or "").partition(":")
        question = self.question
        if question is None or not secrets.compare_digest(token, question.token) or time.monotonic() > question.expires:
            return "This question has expired."
        question.answer = "yes" if choice == "yes" else "no"
        question.event.set()
        return f"{question.text}\n({'Yes' if choice == 'yes' else 'No'})"

    def on_voice(self, chat_id, download, who=None):
        """A voice note. download(path) saves the .ogg there."""
        if not self.authorized(chat_id, who=who, what="a voice note"):
            return
        if self.question is not None:
            self._spawn(lambda: self._voice_answer(download))
            return
        self._spawn(lambda: self._voice_command(download))

    def _spawn(self, job):
        if not self.lock.acquire(blocking=False):
            self.send("Still on the last one, ek sec...")
            return

        def run():
            done = threading.Event()
            threading.Thread(target=self._typing_while, args=(done,), name="telegram-typing", daemon=True).start()
            try:
                job()
            except Exception:
                log.exception("[telegram] command failed")
                self.send(persona.line("error"))
            finally:
                done.set()
                self.lock.release()

        self.worker = threading.Thread(target=run, name="telegram-command", daemon=True)
        self.worker.start()

    def join(self, timeout=10):
        if self.worker is not None:
            self.worker.join(timeout)

    # ---------------------------------------------------------------- running things
    def run_command(self, text):
        from jarvis import router

        aloud = bool(ALOUD.search(text))
        command = ALOUD.sub(" ", text).strip(" ,.") if aloud else text
        actions.note(f"Telegram command: {command}" + (" (out loud)" if aloud else ""))
        self.voice.active, self.voice.aloud, self.voice.buffer = True, aloud, []
        try:
            handled = router.handle(self.ctx, command)
        finally:
            self.voice.active = False
            self.voice.aloud = False
        if self.voice.buffer:
            self.voice.flush()
        elif not handled:
            self.send(persona.line("dont_know"))
        return handled

    def _unpair(self):
        if not self.voice_confirm("Unpair this chat? You'll need a new code from the laptop to control it again."):
            self.send("Okay, still paired.")
            return
        chat = self.chat
        try:
            config.TELEGRAM_FILE.unlink()
        except FileNotFoundError:
            pass
        actions.record(f"Unpaired Telegram chat {chat}")
        notify.toast("Telegram", "Phone unpaired.")
        self.transport.send(chat, "Unpaired. Send /start to pair again (you'll need the code from the laptop).")

    def voice_confirm(self, question):
        from jarvis.router import is_yes

        return is_yes(self.ask(question))

    def _send_file(self, query, text):
        """'send me my resume': find it (the file index), check it's allowed, ask with buttons, send the document."""
        from jarvis.router import SpokenError
        from jarvis.skills import files

        ranked = files.rank(query) if query else []
        if not ranked:
            if files.is_fileish(query):
                self.send(f"I couldn't find a file called {query} on the laptop.")
                return
            self.run_command(text)  # not a file after all ("send good morning to the group"...): the router
            return
        self.voice.active, self.voice.buffer = True, []
        try:
            entry = files.choose(self.ctx, query, ranked, verb="send")
        finally:
            self.voice.active = False
        if entry is None:
            self.send("Okay, I didn't send anything.")
            return
        path = Path(entry.path)
        try:
            actions.guard(path, "send")
        except SpokenError as exc:
            self.send(str(exc))
            return
        if entry.is_dir:
            self.send(f"{path.name} is a folder. Say 'zip {path.name}' first, then send me the zip.")
            return
        if SECRET_FILES.search(path.name):
            actions.note(f"Telegram: refused to send {path} (looks like keys, tokens or passwords)")
            self.send("That file looks like it holds keys, tokens or passwords, so I won't send it over Telegram.")
            return
        try:
            size = path.stat().st_size
        except OSError:
            self.send(f"I can't read {path.name} right now.")
            return
        limit = config.TELEGRAM_MAX_FILE_MB * 1024 * 1024
        if size > limit:
            self.send(f"{path.name} is {size / 1024 ** 2:.0f} MB; Telegram only lets me send up to "
                      f"{config.TELEGRAM_MAX_FILE_MB} MB.")
            return
        where = files.where(entry)
        if not self.voice_confirm(f"Send {path.name} ({_size(size)}) {where} to this chat?"):
            actions.note(f"Telegram: didn't send {path} (you said no)")
            self.send("Okay, not sent.")
            return
        try:
            self.transport.document(self.chat, str(path), path.name)
        except Exception as exc:
            log.warning("[telegram] couldn't send %s: %s", path, exc)
            self.send(f"I couldn't send {path.name}: Telegram didn't take it.")
            return
        actions.record(f"Sent {path} to the paired Telegram chat")

    def _screenshot(self):
        if not self.voice_confirm("The screen may show private things (chats, tabs, emails). Send a screenshot?"):
            self.send("Okay, no screenshot.")
            actions.note("Telegram: screenshot declined")
            return
        path = take_screenshot()
        try:
            self.transport.photo(self.chat, path, f"Screen at {datetime.now():%I:%M %p}")
            actions.record("Sent a screenshot to the paired Telegram chat")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _voice_text(self, download):
        folder = Path(tempfile.mkdtemp(prefix="jarvis-voice-"))
        ogg = folder / "note.ogg"
        try:
            download(str(ogg))
            pcm = ogg_to_pcm(ogg)
            if not pcm:
                return None
            return transcribe(pcm, getattr(self.laptop_voice, "language", None) or config.SPEECH_LANGUAGE)
        finally:
            for item in folder.iterdir():
                item.unlink(missing_ok=True)
            folder.rmdir()

    def _voice_command(self, download):
        heard = self._voice_text(download)
        if not heard:
            self.send("I couldn't make out that voice note. Try again, or type it?")
            return
        self.send(f"Heard: {heard}")
        self.run_command(heard)

    def _voice_answer(self, download):
        heard = self._voice_text(download) or ""
        if self.question is not None:
            self.question.answer = heard.lower()
            self.question.event.set()


# ---------------------------------------------------------------- the laptop side (tests replace these)
def take_screenshot():
    import pyautogui

    path = Path(tempfile.gettempdir()) / f"jarvis-screen-{secrets.token_hex(4)}.png"
    pyautogui.screenshot(str(path))
    return str(path)


def _ffmpeg():
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ogg_to_pcm(path):
    """Telegram's OGG/Opus voice note -> 16 kHz mono 16-bit PCM: ffmpeg if available, else PyAV, else pydub."""
    import subprocess

    ffmpeg = _ffmpeg()
    if ffmpeg:
        out = subprocess.run([ffmpeg, "-v", "quiet", "-i", str(path), "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
                             capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode == 0 and out.stdout:
            return out.stdout
    try:
        import av  # comes with faster-whisper: decodes Opus without an ffmpeg program

        chunks = []
        with av.open(str(path)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for out_frame in resampler.resample(frame):
                    chunks.append(bytes(out_frame.planes[0])[:out_frame.samples * 2])
            for out_frame in resampler.resample(None):
                chunks.append(bytes(out_frame.planes[0])[:out_frame.samples * 2])
        return b"".join(chunks)
    except ImportError:
        pass
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(str(path)).set_frame_rate(16000).set_channels(1).set_sample_width(2)
        return audio.raw_data
    except Exception:
        log.warning("[telegram] can't decode voice notes: install ffmpeg (or pip install imageio-ffmpeg)")
        return None


def transcribe(pcm, language):
    from jarvis import stt

    return (stt.make(language=language).transcribe(pcm, language=language) or "").strip()




def _size(size):
    return f"{size / 1024 ** 2:.1f} MB" if size >= 1024 ** 2 else f"{max(1, size // 1024)} KB"


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        log.debug("status part failed", exc_info=True)
        return default


def _wifi_name():
    from jarvis.skills import settings_control

    ssid, signal = settings_control.wifi_details()
    if not ssid:
        return "not connected"
    return ssid + (f" ({signal}%)" if signal is not None else "")


def _now_playing():
    from jarvis.skills import media_session

    playing = [s for s in media_session.sessions() if s["playing"]]
    if not playing:
        return "nothing"
    return f"{media_session.describe(playing[0])} on {playing[0]['app']}"


def _jarvis_state(laptop_ctx):
    if laptop_ctx is None:
        return "running"
    assistant = laptop_ctx.state.get("assistant")
    if getattr(assistant, "mic_muted", False):
        return "awake, mic muted"
    return "asleep (say the wake word or press the hotkey)" if laptop_ctx.sleeping else "awake and listening"


def _last_action():
    recent = actions.recent(1)
    if recent:
        return f"{recent[0].what} ({recent[0].when:%I:%M %p})"
    tail = [line for line in actions.read_log_tail(30)
            if not line.startswith(("Telegram", "  routine", "Didn't", "Refused"))]  # actions, not log notes
    return tail[0] if tail else "nothing yet"


def status_text(laptop_ctx=None):
    import psutil

    lines = []
    battery = _safe(psutil.sensors_battery)
    if battery is not None:
        state = "charging" if battery.power_plugged else "on battery"
        left = ""
        if not battery.power_plugged and battery.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
            left = f", about {battery.secsleft // 3600} h {battery.secsleft % 3600 // 60} min left"
        lines.append(f"Battery: {round(battery.percent)}%, {state}{left}")
    lines.append(f"CPU: {psutil.cpu_percent(interval=0.5):.0f}%")
    ram = psutil.virtual_memory()
    lines.append(f"RAM: {ram.percent:.0f}% used ({ram.used / 1024**3:.1f} of {ram.total / 1024**3:.1f} GB)")
    drive = (os.environ.get("SystemDrive", "C:") + os.sep) if os.name == "nt" else "/"
    disk = _safe(lambda: psutil.disk_usage(drive))
    if disk is not None:
        lines.append(f"Disk {drive}: {disk.free / 1024**3:.0f} GB free of {disk.total / 1024**3:.0f} GB")
    lines.append(f"Wi-Fi: {_safe(_wifi_name, 'unknown')}")
    lines.append(f"Playing: {_safe(_now_playing, 'unknown')}")
    lines.append(f"{config.ASSISTANT_NAME}: {_jarvis_state(laptop_ctx)}")
    lines.append(f"Last action: {_safe(_last_action, 'unknown')}")
    up = time.time() - psutil.boot_time()
    lines.append(f"Laptop on for {int(up // 3600)} h {int(up % 3600 // 60)} min")
    return "\n".join(lines)


# ---------------------------------------------------------------- python-telegram-bot glue
class BotTransport:
    """Sends from any thread through the bot's own event loop."""

    def __init__(self, app, loop):
        self.app, self.loop = app, loop

    def _run(self, coroutine, timeout=30):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=timeout)

    def send(self, chat_id, text):
        self._run(self.app.bot.send_message(chat_id=chat_id, text=text))

    def typing(self, chat_id):
        self._run(self.app.bot.send_chat_action(chat_id=chat_id, action="typing"), timeout=10)

    def ask(self, chat_id, text, token):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("Yes", callback_data=f"{token}:yes"),
                                         InlineKeyboardButton("No", callback_data=f"{token}:no")]])
        return self._run(self.app.bot.send_message(chat_id=chat_id, text=text, reply_markup=buttons)).message_id

    def expire(self, chat_id, message_id, text):
        self._run(self.app.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text))

    def photo(self, chat_id, path, caption):
        with open(path, "rb") as image:
            self._run(self.app.bot.send_photo(chat_id=chat_id, photo=image, caption=caption), timeout=120)

    def document(self, chat_id, path, caption):
        with open(path, "rb") as document:
            self._run(self.app.bot.send_document(chat_id=chat_id, document=document, filename=Path(path).name,
                                                 caption=caption), timeout=300)


_remote = None


def _who(user):
    if user is None:
        return None
    return f"@{user.username}" if user.username else (user.full_name or str(user.id))


def run_bot(laptop_ctx, on_ready=None):
    """One long-polling session on this thread's own event loop. Returns (or raises) when it stops."""
    global _remote
    from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    previous = _remote
    remote = Remote(BotTransport(app, loop), laptop_ctx)
    if previous is not None:  # a reconnect: keep the once-only alerts' memory
        remote.battery_warned, remote.announced = previous.battery_warned, previous.announced
    _remote = remote

    async def on_update(update, context):
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        who = _who(update.effective_user)
        if message.voice or message.audio:
            item = message.voice or message.audio
            file = await context.bot.get_file(item.file_id)

            def download(path, file=file):
                asyncio.run_coroutine_threadsafe(file.download_to_drive(path), loop).result(timeout=60)

            remote.on_voice(chat.id, download, who)
        elif message.text:
            remote.on_message(chat.id, message.text, who)

    async def on_callback(update, context):
        query = update.callback_query
        shown = remote.on_button(update.effective_chat.id, query.data, _who(update.effective_user))
        await query.answer()
        if shown:
            try:
                await query.edit_message_text(shown)
            except Exception:
                pass

    async def on_error(update, context):
        log.warning("[telegram] %s: %s (still running)", type(context.error).__name__, context.error)

    async def post_init(application):
        me = await application.bot.get_me()
        state = "paired" if paired_chat() is not None else "not paired yet: send /start to the bot from your phone"
        log.info("[telegram] phone remote connected as @%s (%s)", me.username, state)
        if on_ready:
            loop.run_in_executor(None, on_ready, remote)

    app.post_init = post_init
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ALL, on_update))
    app.add_error_handler(on_error)
    # drop_pending_updates: a "shut down" sent while the laptop was off must never run hours later.
    # Network drops while running are retried by python-telegram-bot itself; a failed start raises to serve().
    app.run_polling(stop_signals=None, close_loop=False, drop_pending_updates=True)


def serve(laptop_ctx, run_once=None, sleep=time.sleep, running=lambda ctx: getattr(ctx, "running", True),
          on_ready=None):
    """Keep the bot running: if Telegram or the network is down, log it, wait (5, 10, 30, 60 s...) and reconnect.
    Never raises, so Jarvis carries on without the phone remote."""
    run_once = run_once or run_bot
    failures = 0
    while running(laptop_ctx):
        started = time.monotonic()
        try:
            run_once(laptop_ctx, on_ready=on_ready)
            if not running(laptop_ctx):
                break
            failures = 0
            log.info("[telegram] polling stopped; reconnecting in 5 s")
            sleep(5)
            continue
        except Exception as exc:
            name = type(exc).__name__
            if "InvalidToken" in name or "Unauthorized" in name or "Forbidden" in name:
                log.error("[telegram] the bot token was rejected. Check the bot token in Settings, Connections "
                          "(get one from @BotFather). The phone remote is off.")
                return "bad token"
            if time.monotonic() - started > 300:
                failures = 0  # it had been up for a while: start the back-off again
            wait = RETRY_SECONDS[min(failures, len(RETRY_SECONDS) - 1)]
            failures += 1
            log.warning("[telegram] can't reach Telegram (%s: %s). Retrying in %d s.", name, exc, wait)
            sleep(wait)
    return "stopped"


def _watch(remote_of, laptop_ctx, stop):
    """Background checks while the remote is up: low battery, and Jarvis stopping."""
    while not stop.wait(60):
        remote = remote_of()
        if remote is None:
            continue
        _safe(remote.check_battery)
        if laptop_ctx is not None and not getattr(laptop_ctx, "running", True):
            remote.announce(started=False)
            return


@on_startup
def start(ctx):
    """Start the phone remote if TELEGRAM_BOT_TOKEN is set (called by the Assistant's startup hooks)."""
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    try:
        import telegram  # noqa: F401
    except ImportError:
        log.warning("[telegram] TELEGRAM_BOT_TOKEN is set but python-telegram-bot isn't installed. "
                    "Run: pip install -r requirements.txt")
        return None
    stop = threading.Event()
    watching = []

    def ready(remote):
        if config.TELEGRAM_NOTIFY:
            notify.add_phone_forwarder(_forward)
        remote.announce(started=True)
        if not watching:
            watching.append(True)
            threading.Thread(target=_watch, args=(lambda: _remote, ctx, stop), name="telegram-watch",
                             daemon=True).start()

    def on_exit():
        stop.set()
        if _remote is not None:
            _safe(lambda: _remote.announce(started=False))

    import atexit

    atexit.register(on_exit)
    thread = threading.Thread(target=serve, args=(ctx,), kwargs={"on_ready": ready}, name="telegram-remote",
                              daemon=True)
    thread.start()
    log.info("[telegram] starting the phone remote")
    return thread


def _forward(title, message):
    """notify.toast(..., phone=True) -> the paired chat (one function, so it's only ever added once)."""
    return _remote.forward(title, message) if _remote is not None else False
