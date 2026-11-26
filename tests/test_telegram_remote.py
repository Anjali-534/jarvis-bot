"""Telegram phone remote (jarvis/telegram_remote.py): pairing, strangers ignored, buttons for risky actions, text
replies, voice notes, screenshots, /status. Telegram itself is faked: nothing is sent anywhere."""
import json
import threading
import time

import pytest
from helpers import make_ctx

import config
from jarvis import actions, notify, router
from jarvis import telegram_remote as tg
from jarvis.skills import system

ME, STRANGER = 1111, 6666


class FakeTelegram:
    def __init__(self):
        self.sent, self.asked, self.expired, self.photos = [], [], [], []
        self.documents, self.typings = [], []
        self.new_question = threading.Event()

    def send(self, chat, text):
        self.sent.append((chat, text))

    def ask(self, chat, text, token):
        self.asked.append((chat, text, token))
        self.new_question.set()
        return len(self.asked)

    def expire(self, chat, message_id, text):
        self.expired.append((chat, message_id, text))

    def photo(self, chat, path, caption):
        self.photos.append((chat, path, caption))

    def document(self, chat, path, caption):
        self.documents.append((chat, path, caption))

    def typing(self, chat):
        self.typings.append(chat)

    def texts(self, chat=ME):
        return [t for c, t in self.sent if c == chat]

    def wait_question(self, timeout=5):
        assert self.new_question.wait(timeout), "no [Yes] [No] question was sent"
        self.new_question.clear()
        return self.asked[-1]


@pytest.fixture
def phone(monkeypatch):
    toasts = []
    monkeypatch.setattr(notify, "toast", lambda title, message: toasts.append((title, message)))
    laptop = make_ctx()
    remote = tg.Remote(FakeTelegram(), laptop)
    remote.toasts = toasts
    remote.laptop = laptop
    return remote


@pytest.fixture
def paired(phone):
    tg.save_state({"chat_id": ME})
    return phone


def send(remote, text, chat=ME, who="@meera"):
    remote.on_message(chat, text, who)
    remote.join()


def press(remote, choice):
    chat, text, token = remote.transport.wait_question()
    return remote.on_button(ME, f"{token}:{choice}")


@pytest.fixture
def skill_log(monkeypatch):
    """Every skill records what it got (and confirms like the real ones do when they ask)."""
    from conftest import PURE_SKILLS  # these fall through when it isn't theirs ("open chrome" isn't a file)

    ran = []
    for sk in router.SKILLS:
        if sk.name not in PURE_SKILLS:
            monkeypatch.setattr(sk, "handler", lambda ctx, cmd, m, name=sk.name: ran.append((name, cmd)) or "Done.")
    monkeypatch.setattr(router.FALLBACK, "handler", lambda ctx, cmd, m: ran.append(("ai", cmd)) or "Chat answer.")
    return ran


# ---------------------------------------------------------------- pairing
def test_pairing_needs_the_code_shown_on_the_laptop(phone, caplog):
    with caplog.at_level("WARNING"):
        send(phone, "/start")
    code = phone.pairing["code"]
    assert code in caplog.text and any(code in message for _, message in phone.toasts)  # terminal + toast
    assert code not in " ".join(phone.transport.texts())  # never sent to the phone itself
    send(phone, "000000" if code != "000000" else "111111")
    assert tg.paired_chat() is None and "2 tries left" in phone.transport.texts()[-1]
    send(phone, code)
    assert tg.paired_chat() == ME and phone.transport.texts()[-1].startswith("Paired!")
    assert json.loads(config.TELEGRAM_FILE.read_text())["chat_id"] == ME


def test_three_wrong_codes_cancel_pairing(phone):
    send(phone, "/start")
    code = phone.pairing["code"]
    for wrong in ("123", "456", "789"):
        send(phone, wrong if wrong != code else "000")
    assert phone.pairing is None and "Too many wrong codes" in phone.transport.texts()[-1]
    send(phone, code)
    assert tg.paired_chat() is None


def test_the_code_expires(phone, monkeypatch):
    send(phone, "/start")
    code = phone.pairing["code"]
    phone.pairing["expires"] = time.monotonic() - 1
    send(phone, code)
    assert tg.paired_chat() is None


def test_a_stranger_cant_pair_with_my_code_or_flood_codes(phone):
    send(phone, "/start")
    code = phone.pairing["code"]
    send(phone, code, chat=STRANGER, who="@evil")       # someone else sends the code
    send(phone, "/start", chat=STRANGER, who="@evil")   # or asks for a new one right away
    assert tg.paired_chat() is None and phone.pairing["code"] == code
    assert phone.transport.texts(STRANGER) == []


def test_strangers_are_ignored_and_logged_after_pairing(paired, skill_log, caplog):
    with caplog.at_level("WARNING"):
        send(paired, "shut down the laptop", chat=STRANGER, who="@evil")
        send(paired, "/start", chat=STRANGER, who="@evil")
        assert paired.on_button(STRANGER, "abcd:yes", "@evil") is None
    assert skill_log == [] and paired.transport.sent == []  # no reply at all
    assert "@evil" in caplog.text and "unpaired chat 6666" in actions.log_path().read_text(encoding="utf-8")


def test_unpair_asks_then_removes_the_chat(paired):
    paired.on_message(ME, "/unpair")
    press(paired, "yes")
    paired.join()
    assert tg.paired_chat() is None and not config.TELEGRAM_FILE.exists()
    assert "Unpaired" in paired.transport.texts()[-1]


# ---------------------------------------------------------------- commands
def test_text_commands_go_through_the_normal_router_and_reply_in_text(paired, skill_log):
    send(paired, "open chrome")
    assert skill_log == [("open", "open chrome")]
    assert paired.transport.texts()[-1] == "Done."
    assert paired.laptop.voice.said == []  # silent on the laptop


def test_say_it_out_loud_also_speaks_on_the_laptop(paired, skill_log):
    send(paired, "what time is it, laptop pe bol")
    assert skill_log == [("time", "what time is it")]
    assert paired.laptop.voice.said == ["Done."] and paired.transport.texts()[-1] == "Done."
    send(paired, "tell me a joke say it out loud")
    assert skill_log[-1] == ("joke", "tell me a joke")


def test_risky_actions_ask_with_buttons_and_no_does_nothing(paired, skill_log):
    paired.on_message(ME, "close chrome")
    chat, question, token = paired.transport.wait_question()
    assert "close chrome" in question
    assert paired.on_button(ME, f"{token}:no").endswith("(No)")
    paired.join()
    assert skill_log == []
    paired.on_message(ME, "close chrome")
    press(paired, "yes")
    paired.join()
    assert skill_log == [("close", "close chrome")]


@pytest.mark.parametrize("command, skill", [
    ("shut down the laptop", "power"), ("delete invoice.pdf", "delete file"), ("move resume to documents", "move file"),
    ("close all apps", "close all apps"), ("pull jarvis", "git pull"), ("run acme backend", "run project"),
    ("send a whatsapp to mom saying hi", "whatsapp message"), ("email dhruv saying hi", "email draft"),
    ("add meeting with dhruv tomorrow at 5 pm", "add event"), ("cancel my 5 pm meeting", "cancel event"),
    ("close this window", "close window"),
])
def test_every_risky_action_needs_a_button_press(paired, skill_log, command, skill):
    """Whether or not the skill asks on the laptop, from the phone nothing risky runs without a Yes."""
    by_name = {sk.name: sk for sk in router.SKILLS}
    asks_itself = by_name[skill].confirms
    paired.on_message(ME, command)
    if not asks_itself:
        press(paired, "no")
        paired.join()
        assert skill_log == []
    else:  # these ask themselves (their real handler does); the router lets them
        paired.join()
        assert skill_log == [(skill, command)]


def test_real_confirmations_become_buttons(paired, monkeypatch):
    """A skill's own ctx.confirm (here: shutting down) arrives as [Yes] [No] and a 'no' stops it."""
    ran = []
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "run", lambda args, timeout=20: ran.append(args))
    paired.on_message(ME, "shut down the laptop")
    chat, question, token = paired.transport.wait_question()
    assert "shut down" in question.lower()
    paired.on_button(ME, f"{token}:no")
    paired.join()
    assert ran == [] and "won't shut down" in paired.transport.texts()[-1]


def test_unanswered_buttons_expire_as_no(paired, skill_log, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CONFIRM_SECONDS", 0.2)
    send(paired, "close chrome")
    assert skill_log == [] and "expired" in paired.transport.expired[-1][2]
    assert paired.on_button(ME, f"{paired.transport.asked[-1][2]}:yes") == "This question has expired."


def test_a_typed_answer_works_too(paired, skill_log):
    paired.on_message(ME, "close chrome")
    paired.transport.wait_question()
    paired.on_message(ME, "haan")
    paired.join()
    assert skill_log == [("close", "close chrome")]


def test_keyboard_and_clipboard_are_off_unless_enabled(paired, skill_log, monkeypatch):
    for command in ("type hello", "press enter", "paste", "read my clipboard", "copy that"):
        send(paired, command)
        assert tg.KEYBOARD_OFF in paired.transport.texts()[-1], command
    assert skill_log == []
    monkeypatch.setattr(config, "TELEGRAM_ALLOW_KEYBOARD", True)
    allowed = tg.Remote(paired.transport, paired.laptop)
    send(allowed, "read my clipboard")
    assert skill_log == [("read clipboard", "read my clipboard")]


def test_no_shell_from_telegram(paired, skill_log):
    """Unknown slash commands and anything shell-like only ever reach the normal router (or nothing)."""
    send(paired, "/exec rm -rf /")
    assert skill_log == [] and "don't know that one" in paired.transport.texts()[-1]
    send(paired, "powershell Remove-Item C:\\ -Recurse")
    assert all(name in ("open", "ai") for name, _ in skill_log)  # no skill runs shell text; at most chat/open


def test_one_command_at_a_time(paired, skill_log, monkeypatch):
    gate = threading.Event()
    slow = next(sk for sk in router.SKILLS if sk.name == "time")
    monkeypatch.setattr(slow, "handler", lambda ctx, cmd, m: gate.wait(5) and "Ten o'clock.")
    paired.on_message(ME, "what time is it")
    paired.on_message(ME, "tell me a joke")
    assert "Still on the last one" in paired.transport.texts()[-1]
    gate.set()
    paired.join()


def test_one_sec_goes_out_on_its_own(paired):
    from jarvis import persona

    paired.voice.active = True
    paired.voice.speak(persona.line("one_sec"))
    paired.voice.speak("The answer.")
    paired.voice.flush()
    assert paired.transport.texts() == [persona.line("one_sec"), "The answer."]


def test_reminders_set_from_the_phone_arrive_on_the_phone(paired):
    paired.voice.speak("Reminder: drink water.")  # a timer firing later, outside any command
    assert paired.transport.texts()[-1] == "Reminder: drink water."


# ---------------------------------------------------------------- screenshot, status, voice notes
def test_screenshot_asks_first(paired, monkeypatch, tmp_path):
    shot = tmp_path / "screen.png"
    shot.write_bytes(b"png")
    monkeypatch.setattr(tg, "take_screenshot", lambda: str(shot))
    paired.on_message(ME, "screenshot bhej")
    chat, question, token = paired.transport.wait_question()
    assert "private" in question
    paired.on_button(ME, f"{token}:no")
    paired.join()
    assert paired.transport.photos == []
    paired.on_message(ME, "/screenshot")
    press(paired, "yes")
    paired.join()
    assert paired.transport.photos[0][1] == str(shot) and not shot.exists()  # sent, then deleted


def test_status(paired, monkeypatch):
    monkeypatch.setattr(tg, "status_text", lambda ctx=None: "Battery: 80%, charging\nCPU: 12%\nRAM: 40% used")
    send(paired, "/status")
    assert paired.transport.texts()[-1].startswith("Battery: 80%, charging")


def test_status_text_has_the_numbers():
    text = tg.status_text(make_ctx())
    assert "CPU:" in text and "RAM:" in text and "Disk" in text


def test_voice_notes_are_transcribed_then_handled(paired, skill_log, monkeypatch):
    monkeypatch.setattr(tg, "ogg_to_pcm", lambda path: b"\x00\x01" * 800)
    heard = []
    monkeypatch.setattr(tg, "transcribe", lambda pcm, language: heard.append(language) or "volume up")
    paired.on_voice(ME, lambda path: open(path, "wb").write(b"OggS"))
    paired.join()
    assert skill_log == [("volume up", "volume up")]
    assert paired.transport.texts()[0] == "Heard: volume up" and heard == [config.SPEECH_LANGUAGE]


def test_voice_notes_from_strangers_are_never_downloaded(paired):
    downloaded = []
    paired.on_voice(STRANGER, downloaded.append, "@evil")
    paired.join()
    assert downloaded == []


def test_ogg_decoding_without_ffmpeg(tmp_path, monkeypatch):
    """PyAV (installed with faster-whisper) decodes Telegram's Opus when there's no ffmpeg program."""
    av = pytest.importorskip("av")
    import numpy as np

    path = tmp_path / "tone.ogg"
    with av.open(str(path), "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=48000, layout="mono")
        samples = (np.sin(np.arange(48000) * 2 * np.pi * 440 / 48000) * 8000).astype("int16").reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = 48000
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    monkeypatch.setattr(tg, "_ffmpeg", lambda: None)
    pcm = tg.ogg_to_pcm(path)
    assert pcm and 25000 < len(pcm) < 40000  # about a second at 16 kHz, 16-bit mono


def test_wifi_off_from_the_phone_warns_it_cuts_the_chat(paired, monkeypatch):
    from jarvis.skills import settings_control

    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(settings_control, "radio_states", lambda: {"wifi": True})
    switched = []
    monkeypatch.setattr(settings_control, "set_radio", lambda kind, on: switched.append(on) or True)
    paired.on_message(ME, "turn off wifi")
    chat, question, token = paired.transport.wait_question()
    assert "cuts this Telegram chat off" in question
    paired.on_button(ME, f"{token}:no")
    paired.join()
    assert switched == []


def test_the_bot_never_starts_without_a_token():
    assert tg.start(make_ctx()) is None


def test_the_remote_hook_is_registered():
    import jarvis.assistant  # noqa: F401

    assert tg.start in router.STARTUP


def test_help_mentions_the_safety_rules(paired):
    send(paired, "/help")
    text = paired.transport.texts()[-1]
    assert "/status" in text and "/screenshot" in text and "Yes/No" in text and "/unpair" in text


def test_logs_every_phone_command(paired, skill_log):
    send(paired, "open chrome")
    assert "Telegram command: open chrome" in actions.log_path().read_text(encoding="utf-8")

