"""Gmail skill tests. The Gmail API is faked; nothing is ever sent."""
import json
import logging
from types import SimpleNamespace

import pytest
from fakes import FakeGmail, b64, decode_raw, mail
from helpers import make_ctx

import config
from jarvis import google_auth, http
from jarvis.google_auth import service as real_service
from jarvis.router import handle
from jarvis.skills import gmail, whatsapp

UNREAD = gmail.PRIMARY_UNREAD


@pytest.fixture
def contacts():
    config.CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CONTACTS_FILE.write_text(json.dumps({
        "mom": "919876543210",
        "dhruv": {"phone": "919812345678", "email": "dhruv@example.com"},
        "riya": {"email": "riya@example.com"},
    }), encoding="utf-8")


def use(monkeypatch, fake):
    monkeypatch.setattr(google_auth, "service", lambda name, version: fake)
    return fake


# ---------------------------------------------------------------- not connected
@pytest.mark.parametrize("phrase", ["check my email", "search email for invoice", "read the email from dhruv",
                                    "email dhruv saying hi"])
def test_not_connected_message(monkeypatch, phrase):
    monkeypatch.setattr(google_auth, "service", real_service)  # real check: there is no token file
    ctx = make_ctx(["yes"])
    handle(ctx, phrase)
    assert ctx.voice.last == google_auth.NOT_CONNECTED


def test_connect_google_without_client_file_prints_steps(caplog):
    ctx = make_ctx()
    with caplog.at_level(logging.INFO):
        assert handle(ctx, "connect google") == "connect google"
    assert "enable both \"Gmail API\" and \"Google Calendar API\"" in caplog.text.replace("ENABLE", "enable")
    assert "test user" in caplog.text and "Desktop app" in caplog.text
    assert "google credentials dot json" in ctx.voice.last


def test_connect_google_runs_flow_and_starts_reminders(monkeypatch):
    config.GOOGLE_CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_CREDENTIALS_FILE.write_text("{}", encoding="utf-8")
    flows, started = [], []
    monkeypatch.setattr(google_auth, "run_flow", lambda: flows.append(1))
    from jarvis.skills import calendar

    monkeypatch.setattr(calendar, "start_reminders", lambda ctx: started.append(ctx))
    ctx = make_ctx()
    handle(ctx, "connect my google account")
    assert flows == [1] and len(started) == 1
    assert ctx.voice.last.startswith("Google is connected.")


def test_google_http_errors_are_spoken(monkeypatch):
    from googleapiclient.errors import HttpError

    def broken(name, version):
        raise HttpError(SimpleNamespace(status=403, reason="Forbidden"), b'{"error": "insufficient scopes"}')

    monkeypatch.setattr(google_auth, "service", broken)
    ctx = make_ctx()
    handle(ctx, "check my email")
    assert ctx.voice.last.startswith("Google refused that request.")


# ---------------------------------------------------------------- check / search / read
def test_check_email_counts_and_lists_top_five_without_bodies(monkeypatch):
    messages = [mail(str(i), f"Person {i} <p{i}@example.com>", f"Subject {i}", body=f"SECRET BODY {i}")
                for i in range(7)]
    fake = use(monkeypatch, FakeGmail(messages, {UNREAD: [m["id"] for m in messages]}))
    ctx = make_ctx()
    assert handle(ctx, "check my email") == "check email"
    reply = ctx.voice.said[0]
    assert reply == ("You have 7 unread emails in your Primary inbox. from Person 0: Subject 0; "
                     "from Person 1: Subject 1; from Person 2: Subject 2.")
    assert ctx.voice.said[1:] == ["Want more?"]  # declined: Person 3 and 4 are only printed
    assert not any("SECRET" in s for s in ctx.voice.said)
    assert all(fmt == "metadata" for _, fmt in fake.fetched)  # bodies are never even downloaded


def test_check_email_none_and_many(monkeypatch):
    use(monkeypatch, FakeGmail())
    ctx = make_ctx()
    handle(ctx, "any new emails")
    assert ctx.voice.last == "You have no unread emails in your Primary inbox."

    one = [mail("1", '"Dhruv Sharma" <dhruv@example.com>', "Demo")]
    use(monkeypatch, FakeGmail(one, {UNREAD: ["1"]}, more={UNREAD}))
    handle(ctx, "do i have new emails")
    assert ctx.voice.last == "You have more than 100 unread emails in your Primary inbox. from Dhruv Sharma: Demo."


def test_search_email_top_three(monkeypatch):
    messages = [mail(str(i), f"Shop {i} <s{i}@x.com>", f"Invoice #{i}") for i in range(3)]
    use(monkeypatch, FakeGmail(messages, {"invoice": ["0", "1", "2"]}))
    ctx = make_ctx()
    assert handle(ctx, "search email for invoice") == "search email"
    assert ctx.voice.last.startswith("Top 3 matches for invoice. 1: from Shop 0, Invoice #0, on 23 November")
    handle(ctx, "search my email for nothing here")
    assert ctx.voice.last == "I didn't find any emails matching nothing here."


def test_read_email_from_contact_gives_first_two_sentences(monkeypatch, contacts):
    body = ("Hi Anjali. The demo moved to 5 pm. Please bring the laptop. Thanks!\n\n"
            "On Mon, 23 Nov 2026 Anjali wrote:\n> old quoted text")
    fake = use(monkeypatch, FakeGmail([mail("9", "Dhruv <dhruv@example.com>", "Demo", body)],
                                      {"in:inbox from:(dhruv@example.com)": ["9"]}))
    ctx = make_ctx()
    assert handle(ctx, "read the email from dhruv") == "read email"
    assert ctx.voice.last == "The latest email from Dhruv, subject Demo. Hi Anjali. The demo moved to 5 pm."
    assert fake.fetched == [("9", "full")]


def test_read_email_uses_ai_summary_when_key_set(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "k")
    sent = []

    def fake_post(url, payload, headers=None, timeout=30):
        sent.append(payload)
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "**Demo is at 5.**"}}]})

    monkeypatch.setattr(http, "post_json", fake_post)
    use(monkeypatch, FakeGmail([mail("1", "Amazon <a@amazon.in>", "Order", "<p>Your order shipped.</p>", html=True)],
                               {"in:inbox from:(amazon)": ["1"]}))
    ctx = make_ctx()
    handle(ctx, "read me the latest email from amazon")
    assert ctx.voice.last == "The latest email from Amazon, subject Order. Demo is at 5."
    assert "Your order shipped." in sent[0]["messages"][1]["content"]


def test_read_email_not_found(monkeypatch):
    use(monkeypatch, FakeGmail())
    ctx = make_ctx()
    handle(ctx, "read the email from santa")
    assert ctx.voice.last == "I couldn't find an email from santa in your inbox."


@pytest.mark.parametrize("payload, text", [
    ({"mimeType": "text/plain", "body": {"data": b64("Hi")}}, "Hi"),
    ({"parts": [{"mimeType": "text/html", "body": {"data": b64("<b>Hi</b> there<br><style>x{}</style>")}}]}, "Hi there"),
    ({"parts": [{"mimeType": "text/html", "body": {"data": b64("<p>html</p>")}},
                {"mimeType": "text/plain", "body": {"data": b64("plain wins")}}]}, "plain wins"),
])
def test_body_text(payload, text):
    assert gmail.body_text(payload).split() == text.split()


# ---------------------------------------------------------------- drafts
@pytest.mark.parametrize("text, parsed", [
    ("dhruv saying the demo is at 5", ("dhruv", None, "the demo is at 5")),
    ("dhruv that i'll be late", ("dhruv", None, "i'll be late")),
    ("riya about the trip saying tickets are booked", ("riya", "the trip", "tickets are booked")),
    ("riya sharma", ("riya sharma", None, None)),
    ("to dhruv: hello", ("dhruv", None, "hello")),
])
def test_parse_compose(text, parsed):
    assert gmail.parse_compose(text) == parsed


@pytest.mark.parametrize("spoken, address", [
    ("riya dot sharma at gmail dot com", "riya.sharma@gmail.com"),
    ("dhruv@example.com", "dhruv@example.com"),
    ("dhruv", None),
])
def test_spoken_email(spoken, address):
    assert gmail.spoken_email(spoken) == address


def test_email_creates_draft_only_after_confirmation(monkeypatch, contacts):
    fake = use(monkeypatch, FakeGmail())
    ctx = make_ctx(["yes"])
    assert handle(ctx, "email dhruv saying the demo is at 5") == "email draft"
    assert ctx.voice.said[0] == ("Here's the draft to Dhruv, dhruv@example.com. Subject: The demo is at 5. "
                                 "Message: The demo is at 5. Save it as a draft?")
    assert ctx.voice.last.startswith("Draft saved in Gmail.")
    assert len(fake.created_drafts) == 1
    raw = decode_raw(fake.created_drafts[0]["message"]["raw"])
    assert "to: dhruv@example.com" in raw and "subject: The demo is at 5" in raw and "The demo is at 5" in raw


def test_email_refused_saves_nothing(monkeypatch, contacts):
    fake = use(monkeypatch, FakeGmail())
    ctx = make_ctx(["no"])
    handle(ctx, "write an email to riya about the trip saying tickets are booked")
    assert "Subject: The trip." in ctx.voice.said[0]
    assert ctx.voice.last == "Okay, I didn't save it."
    assert fake.created_drafts == []


def test_email_recipient_from_recent_senders(monkeypatch):
    sender = mail("5", '"Kabir Mehta" <kabir@work.com>', "Hello")
    fake = use(monkeypatch, FakeGmail([sender], {"from:(kabir)": ["5"]}))
    ctx = make_ctx(["see you at lunch", "yes"])
    handle(ctx, "send an email to kabir")
    assert ctx.voice.said[0] == "What should the email say?"
    assert "Kabir Mehta, kabir@work.com" in ctx.voice.said[1]
    assert len(fake.created_drafts) == 1


def test_email_unknown_recipient(monkeypatch):
    fake = use(monkeypatch, FakeGmail())
    ctx = make_ctx(["yes"])
    handle(ctx, "email santa saying hi")
    assert ctx.voice.last.startswith("I couldn't find an email address for santa.")
    assert len(ctx.voice.said) == 1 and fake.created_drafts == []


# ---------------------------------------------------------------- contacts with emails
def test_contacts_file_mixes_phones_and_emails(contacts):
    assert whatsapp.load_contacts() == {"mom": "919876543210", "dhruv": "919812345678"}
    assert whatsapp.load_emails() == {"dhruv": "dhruv@example.com", "riya": "riya@example.com"}
    whatsapp.save_contact("Dhruv", "919800000000")
    saved = json.loads(config.CONTACTS_FILE.read_text(encoding="utf-8"))
    assert saved["dhruv"] == {"phone": "919800000000", "email": "dhruv@example.com"}
