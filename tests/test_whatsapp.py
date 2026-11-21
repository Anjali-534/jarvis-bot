"""WhatsApp skill tests. os.startfile, pyautogui and the browser are all faked."""
import json
import os

import pytest
from helpers import make_ctx

import config
from jarvis.router import handle
from jarvis.skills import whatsapp


@pytest.fixture
def contacts():
    config.CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CONTACTS_FILE.write_text(json.dumps({"Mom": "919876543210", "dhruv": "919812345678",
                                                "riya sharma": "919811112222"}), encoding="utf-8")


@pytest.fixture
def sent(monkeypatch):
    """Record what would be opened/pressed instead of touching real apps."""
    log = []
    monkeypatch.setattr(os, "startfile", lambda uri: log.append(("startfile", uri)), raising=False)
    monkeypatch.setattr(whatsapp, "open_uri", lambda uri: log.append(("uri", uri)))
    monkeypatch.setattr(whatsapp, "open_web", lambda url: log.append(("web", url)))
    monkeypatch.setattr(whatsapp, "press_enter", lambda: log.append(("enter",)))
    monkeypatch.setattr(whatsapp, "sleep", lambda s: log.append(("sleep", s)))
    monkeypatch.setattr(whatsapp, "whatsapp_installed", lambda: True)
    return log


@pytest.mark.parametrize("command, contact, message", [
    ("send a whatsapp to mom saying i'll be late", "mom", "i'll be late"),
    ("send a whatsapp message to mom that dinner is ready", "mom", "dinner is ready"),
    ("send a message to dhruv on whatsapp saying hi", "dhruv", "hi"),
    ("message dhruv on whatsapp that the demo is at 5", "dhruv", "the demo is at 5"),
    ("whatsapp mom saying on my way", "mom", "on my way"),
    ("tell riya sharma on whatsapp that i'm outside", "riya sharma", "i'm outside"),
    ("whatsapp mom", "mom", None),
    ("send a whats app to mom", "mom", None),
])
def test_parse_message_command(command, contact, message):
    assert whatsapp.parse_message_command(command) == (contact, message)


@pytest.mark.parametrize("raw, number", [
    ("9876543210", "919876543210"), ("98765 43210", "919876543210"), ("+91 98765-43210", "919876543210"),
    ("09876543210", "919876543210"), ("919876543210", "919876543210"), ("+1 415 555 0100", "14155550100"),
    ("12345", None), ("", None),
])
def test_normalize_number(raw, number):
    assert whatsapp.normalize_number(raw) == number


def test_fuzzy_contact_match(contacts):
    book = whatsapp.load_contacts()
    assert whatsapp.resolve_contact("druv", book)[:2] == ("dhruv", "919812345678")
    assert whatsapp.resolve_contact("my mom", book)[0] == "mom"
    assert whatsapp.resolve_contact("mom i'm on my way", book) == ("mom", "919876543210", "i'm on my way")
    assert whatsapp.resolve_contact("santa", book) is None


def test_send_confirmed(contacts, sent):
    ctx = make_ctx(["yes"])
    assert handle(ctx, "send a whatsapp to mom saying i'll be late") == "whatsapp message"
    assert ctx.voice.said[0] == "Send 'I'll be late' to Mom?"
    assert sent[0] == ("uri", "whatsapp://send?phone=919876543210&text=I%27ll%20be%20late")
    assert sent[1] == ("sleep", config.WHATSAPP_SEND_DELAY) and sent[2] == ("enter",)
    assert ctx.voice.last == "Sent to Mom."


def test_confirm_no_sends_nothing(contacts, sent):
    ctx = make_ctx(["no"])
    handle(ctx, "message dhruv on whatsapp that the demo is at 5")
    assert ctx.voice.said[0] == "Send 'The demo is at 5' to Dhruv?"
    assert ctx.voice.last == "Okay, I didn't send it."
    assert sent == []


def test_asks_for_missing_message(contacts, sent):
    ctx = make_ctx(["call me when you are free", "yeah"])
    handle(ctx, "whatsapp druv")
    assert ctx.voice.said[0] == "What should I say?"
    assert ctx.voice.said[1] == "Send 'Call me when you are free' to Dhruv?"
    assert ("enter",) in sent


def test_unknown_contact(contacts, sent):
    ctx = make_ctx()
    handle(ctx, "send a whatsapp to santa saying hi")
    assert "don't have santa" in ctx.voice.last and "add contact" in ctx.voice.last
    assert sent == []


def test_no_contacts_file(sent):
    ctx = make_ctx()
    handle(ctx, "whatsapp mom saying hi")
    assert "haven't saved any WhatsApp contacts" in ctx.voice.last


def test_web_fallback_when_desktop_missing(contacts, sent, monkeypatch):
    monkeypatch.setattr(whatsapp, "whatsapp_installed", lambda: False)
    ctx = make_ctx(["yes"])
    handle(ctx, "whatsapp mom saying hi")
    assert sent[0] == ("web", "https://web.whatsapp.com/send?phone=919876543210&text=Hi")
    assert sent[1][1] > config.WHATSAPP_SEND_DELAY and sent[2] == ("enter",)
    assert "WhatsApp Web" in ctx.voice.last


def test_call_opens_chat_only(contacts, sent):
    ctx = make_ctx()
    assert handle(ctx, "call mom on whatsapp") == "whatsapp call"
    assert sent == [("uri", "whatsapp://send?phone=919876543210")]
    assert "Tap the voice call button" in ctx.voice.last


def test_add_contact_confirms_and_saves(sent):
    ctx = make_ctx(["yes"])
    handle(ctx, "add contact riya 98765 43210")
    assert ctx.voice.said[0] == "Save Riya as +91 98765 43210?"
    assert json.loads(config.CONTACTS_FILE.read_text(encoding="utf-8")) == {"riya": "919876543210"}


def test_add_contact_refused(sent):
    ctx = make_ctx(["no"])
    handle(ctx, "add contact riya 9876543210")
    assert not config.CONTACTS_FILE.exists()
