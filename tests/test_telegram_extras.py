"""Telegram remote, part 2: sending files, /lock, phone notifications, the battery alert, reconnecting, /status
details and "typing...". The Telegram API is faked (FakeTelegram from test_telegram_remote)."""
import os
import time
from datetime import datetime

import pytest
from helpers import make_ctx
from test_telegram_remote import ME, FakeTelegram, press, send  # noqa: F401  (shared fakes)

import config
from jarvis import actions, notify, router
from jarvis import telegram_remote as tg
from jarvis.skills import files, notes, system


@pytest.fixture
def paired(monkeypatch):
    monkeypatch.setattr(notify, "toast", notify.toast)  # the real one (it forwards); NOTIFY is off in tests
    tg.save_state({"chat_id": ME})
    laptop = make_ctx()
    remote = tg.Remote(FakeTelegram(), laptop)
    remote.laptop = laptop
    return remote


# ---------------------------------------------------------------- sending files
@pytest.fixture
def documents(monkeypatch, tmp_path):
    """A fake Documents folder in the file index, with tmp paths judged as if they were on D: (not AppData)."""
    from test_laptop_control import as_if_on_d_drive

    as_if_on_d_drive(monkeypatch, tmp_path)
    folder = tmp_path / "home" / "Documents"
    folder.mkdir(parents=True)
    (folder / "Resume_Anjali_2026.pdf").write_bytes(b"%PDF resume")
    (folder / "holiday video.mp4").write_bytes(b"x")
    os.truncate(folder / "holiday video.mp4", 60 * 1024 * 1024)  # 60 MB, sparse
    (folder / "server.env").write_text("TOKEN=abc")
    (folder / "projects").mkdir()
    monkeypatch.setattr(files, "known_folders", lambda: {"Documents": folder})
    monkeypatch.setattr(files, "index_roots", lambda: [folder])
    files.reset_index()
    return folder


def test_send_me_my_resume_asks_then_sends_the_document(paired, documents):
    paired.on_message(ME, "send me my resume")
    chat, question, token = paired.transport.wait_question()
    assert "Resume_Anjali_2026.pdf" in question and "in Documents" in question
    paired.on_button(ME, f"{token}:yes")
    paired.join()
    assert paired.transport.documents == [(ME, str(documents / "Resume_Anjali_2026.pdf"), "Resume_Anjali_2026.pdf")]
    assert "Sent" in actions.log_path().read_text(encoding="utf-8")


def test_no_button_sends_nothing(paired, documents):
    paired.on_message(ME, "resume bhej do")
    press(paired, "no")
    paired.join()
    assert paired.transport.documents == [] and paired.transport.texts()[-1] == "Okay, not sent."


def test_files_over_50_mb_are_refused(paired, documents):
    send(paired, "send me the holiday video")
    assert paired.transport.documents == [] and paired.transport.asked == []
    assert "60 MB" in paired.transport.texts()[-1] and "50 MB" in paired.transport.texts()[-1]


def test_system_folders_are_refused(paired, documents, monkeypatch):
    windows = documents.parent.parent / "Windows"
    (windows / "System32").mkdir(parents=True)
    (windows / "System32" / "hosts.txt").write_text("x")
    monkeypatch.setenv("SystemRoot", "D:\\testhome\\Windows")
    files._index.update(entries=files.scan([documents]) + [files.Entry(str(windows / "System32" / "hosts.txt"),
                                                                      time.time(), False)],
                        built=datetime.now().isoformat())
    send(paired, "send me hosts.txt")
    assert paired.transport.documents == [] and paired.transport.asked == []
    assert "system folders" in paired.transport.texts()[-1]


def test_secret_files_and_folders_are_never_sent(paired, documents):
    send(paired, "send me server.env")
    assert "keys, tokens or passwords" in paired.transport.texts()[-1]
    send(paired, "send me the projects folder")
    assert "is a folder" in paired.transport.texts()[-1]
    assert paired.transport.documents == []


def test_not_a_file_goes_to_the_router(paired, documents, monkeypatch):
    ran = []
    for sk in router.SKILLS:
        if sk.name == "whatsapp message":
            monkeypatch.setattr(sk, "handler", lambda ctx, cmd, m: ran.append(cmd) or "Sent?")
            monkeypatch.setattr(sk, "confirms", True)
    send(paired, "send a whatsapp to mom saying hi")
    assert ran == ["send a whatsapp to mom saying hi"] and paired.transport.documents == []


def test_unknown_file(paired, documents):
    send(paired, "send me my tax pdf")
    assert "couldn't find a file" in paired.transport.texts()[-1]


# ---------------------------------------------------------------- /lock
def test_lock_needs_no_confirmation(paired, monkeypatch):
    ran = []
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "run", lambda args, timeout=20: ran.append(args))
    send(paired, "/lock")
    assert ran == [["rundll32.exe", "user32.dll,LockWorkStation"]] and paired.transport.asked == []
    send(paired, "lock the laptop")
    assert len(ran) == 2 and paired.transport.asked == []


def test_close_this_window_still_asks(paired, monkeypatch):
    closed = []
    from jarvis.skills import windows

    monkeypatch.setattr(windows, "close_active", lambda ctx: closed.append(1) or "")
    paired.on_message(ME, "close this window")
    press(paired, "no")
    paired.join()
    assert closed == []


# ---------------------------------------------------------------- notifications to the phone
def test_reminders_and_alerts_go_to_the_phone_when_enabled(paired, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY", True)
    notify.add_phone_forwarder(paired.forward)
    notify.toast("Reminder", "drink water", phone=True)
    notify.toast("Upcoming event", "Standup at 10 AM", phone=True)
    notify.toast("Telegram pairing code", "123456")  # never to the phone
    notify.toast("Jarvis", "No microphone found.")
    assert paired.transport.texts() == ["Reminder: drink water", "Upcoming event: Standup at 10 AM"]


def test_nothing_goes_to_the_phone_when_off(paired):
    notify.add_phone_forwarder(paired.forward)
    notify.toast("Reminder", "drink water", phone=True)
    assert paired.transport.sent == []


def test_a_laptop_reminder_arrives_on_the_phone(paired, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY", True)
    notify.add_phone_forwarder(paired.forward)
    laptop = make_ctx()
    notes.schedule(laptop, 0.05, "stretch")
    deadline = time.monotonic() + 5
    while not paired.transport.sent and time.monotonic() < deadline:
        time.sleep(0.02)
    assert paired.transport.texts() == ["Reminder: stretch"]
    assert "stretch" in laptop.voice.said[-1]  # still spoken on the laptop


def test_a_phone_set_reminder_arrives_once(paired, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY", True)
    notify.add_phone_forwarder(paired.forward)
    notes.schedule(paired.ctx, 0.05, "call mom")  # set from the phone: its own voice would send it too
    deadline = time.monotonic() + 5
    while len(paired.transport.sent) < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    assert len(paired.transport.sent) == 1


def test_memory_date_reminders_are_marked_for_the_phone(paired, monkeypatch):
    from datetime import date

    from jarvis.skills import memory

    monkeypatch.setattr(config, "TELEGRAM_NOTIFY", True)
    notify.add_phone_forwarder(paired.forward)
    memory.add("Dhruv's birthday is 12 Feb", today=date(2026, 11, 26))
    memory.tick(make_ctx(), datetime(2027, 2, 12, 9, 0))
    assert paired.transport.texts() == ["Today: Dhruv's birthday is 12 Feb"]


def test_battery_alert_once_per_drop(paired, monkeypatch):
    assert paired.battery_alert(30, False) is None
    assert paired.battery_alert(14, False) == "Battery at 14% and not charging. Plug in the laptop."
    assert paired.battery_alert(12, False) is None   # not again while it keeps dropping
    assert paired.battery_alert(10, False) is None
    assert paired.battery_alert(11, True) is None    # plugged in: re-armed
    assert paired.battery_alert(13, False) is not None
    assert paired.battery_alert(17, False) is None   # 15-20%: still not re-armed
    assert paired.battery_alert(14, False) is None
    assert paired.battery_alert(21, False) is None   # above 20%: re-armed
    assert paired.battery_alert(14, False) is not None


def test_battery_alert_is_sent_when_notify_is_on(paired, monkeypatch):
    import psutil

    class Battery:
        percent, power_plugged, secsleft = 12, False, 1800

    monkeypatch.setattr(psutil, "sensors_battery", lambda: Battery())
    paired.check_battery()
    assert paired.transport.sent == []  # TELEGRAM_NOTIFY off
    paired.battery_warned = False
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY", True)
    paired.check_battery()
    paired.check_battery()
    assert paired.transport.texts() == ["Battery at 12% and not charging. Plug in the laptop."]


def test_started_and_stopped_are_announced_once(paired, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY", True)
    assert paired.announce(started=True) and not paired.announce(started=True)
    assert paired.announce(started=False) and not paired.announce(started=False)
    texts = paired.transport.texts()
    assert texts[0].startswith("Jarvis started on the laptop") and texts[1].startswith("Jarvis stopped")


# ---------------------------------------------------------------- reliability
class NetworkError(Exception):
    pass


class InvalidToken(Exception):
    pass


def test_reconnects_after_a_network_error(caplog):
    calls, waits = [], []
    ctx = make_ctx()

    def run_once(laptop_ctx, on_ready=None):
        calls.append(1)
        if len(calls) <= 2:
            raise NetworkError("getaddrinfo failed")
        laptop_ctx.running = False  # third time: connected, and later Jarvis exits

    with caplog.at_level("WARNING"):
        assert tg.serve(ctx, run_once=run_once, sleep=waits.append) == "stopped"
    assert len(calls) == 3 and waits == [5, 10]  # backed off, then reconnected
    assert "can't reach Telegram (NetworkError: getaddrinfo failed). Retrying in 5 s." in caplog.text


def test_a_bad_token_stops_cleanly(caplog):
    def run_once(laptop_ctx, on_ready=None):
        raise InvalidToken("The token was rejected")

    with caplog.at_level("ERROR"):
        assert tg.serve(make_ctx(), run_once=run_once, sleep=lambda s: None) == "bad token"
    assert "TELEGRAM_BOT_TOKEN" in caplog.text


def test_telegram_down_never_breaks_a_command(paired, monkeypatch):
    def down(chat, text):
        raise NetworkError("Telegram is down")

    monkeypatch.setattr(paired.transport, "send", down)
    assert paired.send("hello") is False  # logged, not raised
    notify.add_phone_forwarder(paired.forward)
    monkeypatch.setattr(config, "TELEGRAM_NOTIFY", True)
    notify.toast("Reminder", "still works", phone=True)  # no exception reaches the reminder


def test_typing_is_shown_while_working(paired, monkeypatch):
    gate = []
    slow = next(sk for sk in router.SKILLS if sk.name == "time")
    monkeypatch.setattr(slow, "handler", lambda ctx, cmd, m: time.sleep(0.2) or gate.append(1) or "Ten o'clock.")
    send(paired, "what time is it")
    assert paired.transport.typings and paired.transport.typings[0] == ME


# ---------------------------------------------------------------- /status
def test_status_has_wifi_playing_state_and_last_action(paired, monkeypatch):
    monkeypatch.setattr(tg, "_wifi_name", lambda: "Home WiFi (82%)")
    monkeypatch.setattr(tg, "_now_playing", lambda: "Kesariya by Arijit Singh on Spotify")
    actions.record("Opened chrome")
    paired.laptop.sleeping = True
    text = tg.status_text(paired.laptop)
    for part in ("Battery", "CPU:", "RAM:", "Wi-Fi: Home WiFi (82%)", "Playing: Kesariya by Arijit Singh on Spotify",
                 "Jarvis: asleep", "Last action: Opened chrome"):
        assert part in text, part
    paired.laptop.sleeping = False
    assert "Jarvis: awake and listening" in tg.status_text(paired.laptop)


def test_status_survives_broken_parts(monkeypatch):
    def broken():
        raise RuntimeError("netsh missing")

    monkeypatch.setattr(tg, "_wifi_name", broken)
    monkeypatch.setattr(tg, "_now_playing", broken)
    text = tg.status_text(make_ctx())
    assert "Wi-Fi: unknown" in text and "Playing: unknown" in text
