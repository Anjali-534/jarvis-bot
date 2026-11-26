"""Long-term memory (skills/memory.py): saving, date reminders, recall, forget, update, privacy, chat context.
The AI is mocked; the memory file lives in tmp_path (conftest)."""
import json
from datetime import date, datetime

import pytest
from helpers import make_ctx, openai_stream

import config
from jarvis import http, intent, notify
from jarvis.router import handle
from jarvis.skills import memory


def saved():
    return json.loads(config.MEMORY_FILE.read_text(encoding="utf-8"))["memories"]


@pytest.fixture
def friend(monkeypatch):
    monkeypatch.setattr(config, "PERSONALITY", "friend")


def remember(*facts):
    for fact in facts:
        handle(make_ctx(), f"remember that {fact}")


# ---------------------------------------------------------------- saving
@pytest.mark.parametrize("said, text, category", [
    ("remember that Dhruv's birthday is 12 Feb", "Dhruv's birthday is 12 Feb", "dates"),
    ("remember I like cold coffee", "I like cold coffee", "preferences"),
    ("yaad rakhna ki meri bike ki service March mein hai", "Meri bike ki service March mein hai", "dates"),
    ("remember that the acme project demo is on friday", "The acme project demo is on friday", "work"),
    ("remember that mom's favourite flower is the lily", "Mom's favourite flower is the lily", "preferences"),
    ("remember that my sister lives in Pune", "My sister lives in Pune", "personal"),
    ("dhruv ka number 98765 43210 hai yaad rakhna", "Dhruv ka number 98765 43210 hai", "people"),
])
def test_saving(said, text, category):
    ctx = make_ctx()
    assert handle(ctx, said) == "remember"
    entry = saved()[-1]
    assert entry["text"] == text and entry["category"] == category
    assert entry["id"].startswith("m") and entry["created"][:4].isdigit()


def test_saving_confirms_casually(friend):
    ctx = make_ctx()
    handle(ctx, "remember I like cold coffee")
    assert ctx.voice.last in ("Done, yaad rahega!", "Pakka, yaad rakhunga.", "Noted yaar, yaad rahega.")


def test_a_birthday_becomes_a_yearly_reminder(friend):
    ctx = make_ctx()
    handle(ctx, "remember that Dhruv's birthday is 12 Feb")
    entry = saved()[-1]
    assert entry["remind_on"].endswith("-02-12") and entry["yearly"] is True
    assert "12 February ko" in ctx.voice.last and "har saal" in ctx.voice.last


def test_month_only_dates_remind_on_the_first():
    entry = memory.make("Meri bike ki service March mein hai", today=date(2026, 11, 26))
    assert entry["remind_on"] == "2027-03-01" and "yearly" not in entry
    assert "remind_on" not in memory.make("The rent is due this November", today=date(2026, 11, 26))


def test_reminders_fire_at_9_on_the_day_and_the_day_before_for_birthdays(monkeypatch):
    toasts = []
    monkeypatch.setattr(notify, "toast", lambda title, message, **kw: toasts.append((title, message)))
    birthday = memory.add("Dhruv's birthday is 12 Feb", today=date(2026, 11, 26))
    service = memory.add("Meri bike ki service March mein hai", today=date(2026, 11, 26))
    ctx = make_ctx()
    assert memory.tick(ctx, datetime(2027, 2, 11, 8, 59)) == []           # before 9 am: nothing yet
    assert memory.tick(ctx, datetime(2027, 2, 11, 9, 0)) == [("before", birthday["text"])]
    assert memory.tick(ctx, datetime(2027, 2, 11, 18, 0)) == []           # once a day
    assert memory.tick(ctx, datetime(2027, 2, 12, 9, 5)) == [("day", birthday["text"])]
    assert memory.tick(ctx, datetime(2027, 2, 28, 9, 0)) == []            # not yearly: no day-before for the bike
    assert memory.tick(ctx, datetime(2027, 3, 1, 9, 0)) == [("day", service["text"])]
    assert memory.tick(ctx, datetime(2028, 2, 12, 9, 30)) == [("day", birthday["text"])]  # every year
    assert memory.tick(ctx, datetime(2028, 3, 1, 9, 0)) == []             # the bike was a one-off
    assert ("Tomorrow", "Dhruv's birthday is 12 Feb") in toasts and len(toasts) == 4
    assert any("Dhruv's birthday" in said for said in ctx.voice.said)


def test_reminder_fires_later_that_day_if_jarvis_started_late(monkeypatch):
    monkeypatch.setattr(notify, "toast", lambda *a, **kw: None)
    memory.add("Dhruv's birthday is 12 Feb", today=date(2026, 11, 26))
    assert memory.tick(make_ctx(), datetime(2027, 2, 12, 15, 0))


# ---------------------------------------------------------------- privacy
@pytest.mark.parametrize("secret", [
    "my gmail password is hunter22", "the otp is 482913", "my card number is 4111 1111 1111 1111",
    "my aadhaar is 1234 5678 9012", "my pan is ABCDE1234F", "my bank account number is 001234567890",
    "my atm pin is 4321", "my upi pin is 1234",
])
def test_secrets_are_never_saved(friend, secret):
    ctx = make_ctx()
    handle(ctx, f"remember that {secret}")
    assert not config.MEMORY_FILE.exists() or saved() == []
    assert ctx.voice.last.startswith(("Ye mat bata mujhe yaar", "Arre ye sab mujhe mat bata"))


def test_a_phone_number_is_fine():
    remember("Dhruv's number is 9876543210")
    assert saved()[-1]["text"] == "Dhruv's number is 9876543210"


def test_health_and_sad_memories_stay_quiet_unless_brought_up(monkeypatch):
    remember("I like cold coffee", "my dad passed away in 2019", "I have a thyroid problem")
    assert [m.get("sensitive", False) for m in saved()] == [False, True, True]
    ctx = make_ctx()
    handle(ctx, "what do you know about me")
    assert "cold coffee" in ctx.voice.last and "dad" not in ctx.voice.last and "thyroid" not in ctx.voice.last
    assert "thyroid" not in memory.context_for("what should i eat for dinner")
    assert "thyroid" in memory.context_for("my thyroid medicine time")  # I brought it up


# ---------------------------------------------------------------- recall
def test_recall_ranking_without_ai():
    remember("Dhruv's birthday is 12 Feb", "Riya's birthday is 3 June", "meri bike ki service March mein hai",
             "I like cold coffee")
    ctx = make_ctx()
    assert handle(ctx, "dhruv ka birthday kab hai") == "recall memory"
    assert "Dhruv's birthday is 12 Feb" in ctx.voice.last and "February" in ctx.voice.last
    handle(ctx, "meri bike ki service kab hai?")
    assert "teri bike ki service march mein hai" in ctx.voice.last.lower()
    handle(ctx, "what did i tell you about coffee")
    assert "You like cold coffee" in ctx.voice.last
    handle(ctx, "tujhe yaad hai maine kya bola tha bike ke baare mein?")
    assert "bike" in ctx.voice.last.lower()


def test_ranking_puts_the_best_match_first():
    remember("Riya's birthday is 3 June", "Dhruv's birthday is 12 Feb", "Dhruv likes chess")
    ranked = memory.rank("dhruv birthday")
    assert ranked[0][1]["text"] == "Dhruv's birthday is 12 Feb"
    assert ranked[0][0] > ranked[1][0]


def test_nothing_matches(friend):
    ctx = make_ctx()
    remember("I like cold coffee")
    handle(ctx, "what did i tell you about my car")
    assert "kuch nahi bataya" in ctx.voice.last or "yaad nahi" in ctx.voice.last
    assert handle(make_ctx(), "when is my next meeting") != "recall memory"  # the calendar's


@pytest.fixture
def ai(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    calls = []

    def post_stream(url, payload, headers=None, timeout=None):
        calls.append(payload)
        return openai_stream(calls.answers.pop(0) if calls.answers else "Okay.")

    calls_obj = type("Calls", (list,), {})()
    calls_obj.answers = []
    calls = calls_obj
    monkeypatch.setattr(http, "post_stream", post_stream)
    return calls


def test_many_matches_let_the_ai_pick_and_phrase_it(ai):
    remember("Dhruv's birthday is 12 Feb", "Dhruv's anniversary is 9 Nov", "Dhruv likes chess",
             "Dhruv's dog is called Bruno")
    ai.answers.append("Dhruv ka birthday 12 Feb ko hai yaar, gift soch le!")
    ctx = make_ctx()
    handle(ctx, "dhruv ka birthday kab hai")
    assert ctx.voice.last == "Dhruv ka birthday 12 Feb ko hai yaar, gift soch le!"
    prompt = "\n".join(m["content"] for m in ai[-1]["messages"])
    assert "Dhruv's birthday is 12 Feb" in prompt and "ONLY" in prompt


def test_ai_says_none_falls_back_to_the_best_match(ai):
    remember("Dhruv's birthday is 12 Feb")
    ai.answers.append("NONE")
    ctx = make_ctx()
    handle(ctx, "what did i tell you about dhruv's birthday")
    assert "Dhruv's birthday is 12 Feb" in ctx.voice.last


def test_chat_gets_only_relevant_memories(ai, friend):
    remember("I like cold coffee", "Dhruv's birthday is 12 Feb", "My sister lives in Pune")
    ai.answers.append("Cold coffee bana le, tujhe toh pasand hai na!")
    ctx = make_ctx()
    handle(ctx, "kuch thanda peene ka mann hai")
    system = ai[-1]["messages"][0]["content"]
    assert "I like cold coffee" in system and "Dhruv" not in system and "Pune" not in system
    assert "never force them in" in system
    assert ctx.voice.said[-1] == "Cold coffee bana le, tujhe toh pasand hai na!"
    ai.answers.append("Recursion matlab function khud ko call karta hai.")
    handle(ctx, "explain recursion simply")
    assert "Things" not in ai[-1]["messages"][0]["content"]  # nothing relevant: nothing added


# ---------------------------------------------------------------- forget, update, list, export
def test_forget_reads_back_and_a_no_keeps_it(friend):
    remember("meri bike ki service March mein hai")
    ctx = make_ctx(["no"])
    assert handle(ctx, "bhool ja ki meri bike ki service march mein hai") == "forget memory"
    assert "teri bike ki service" in ctx.voice.said[0].lower() and len(saved()) == 1
    ctx = make_ctx(["haan"])
    handle(ctx, "forget about my bike service")
    assert saved() == []


def test_forget_that_means_the_last_one():
    ctx = make_ctx()
    handle(ctx, "remember I like cold coffee")
    ctx.voice.answers = ["yes"]
    handle(ctx, "forget that")
    assert saved() == []
    ctx.voice.answers = ["yes"]
    handle(ctx, "undo that")
    assert [m["text"] for m in saved()] == ["I like cold coffee"]


def test_forget_our_conversation_is_still_the_chat_memory():
    assert handle(make_ctx(), "forget our conversation") == "forget conversation"


def test_update_replaces_the_old_one():
    remember("Dhruv's birthday is 12 Feb", "I like cold coffee")
    ctx = make_ctx()
    assert handle(ctx, "update: Dhruv's birthday is 14 Feb") == "update memory"
    texts = [m["text"] for m in saved()]
    assert texts == ["Dhruv's birthday is 14 Feb", "I like cold coffee"]
    assert saved()[0]["remind_on"].endswith("-02-14") and saved()[0]["updated_from"] == "Dhruv's birthday is 12 Feb"


def test_saving_a_changed_fact_offers_to_update():
    remember("Dhruv's birthday is 12 Feb")
    ctx = make_ctx(["yes"])
    handle(ctx, "remember that Dhruv's birthday is 14 Feb")
    assert "update" in ctx.voice.said[0].lower()
    assert [m["text"] for m in saved()] == ["Dhruv's birthday is 14 Feb"]


def test_list_memories(caplog):
    remember(*[f"fact number {n} is here" for n in range(1, 8)])
    ctx = make_ctx()
    with caplog.at_level("INFO"):
        handle(ctx, "list my memories")
    assert ctx.voice.last.startswith("I remember 7 things. The latest:")
    assert "fact number 7" in ctx.voice.last.lower() and "fact number 2" not in ctx.voice.last.lower()  # latest 5
    assert "Fact number 1" in caplog.text  # the full list is printed


def test_export_writes_a_readable_file(monkeypatch, tmp_path):
    from jarvis.skills import files

    monkeypatch.setattr(files, "known_folders", lambda: {"Documents": tmp_path / "Documents"})
    remember("Dhruv's birthday is 12 Feb", "I like cold coffee")
    ctx = make_ctx()
    handle(ctx, "export my memories")
    text = (tmp_path / "Documents" / "Jarvis memories.txt").read_text(encoding="utf-8")
    assert "Dhruv's birthday is 12 Feb" in text and "every year" in text and "Preferences" in text
    assert "Documents" in ctx.voice.last


def test_memory_file_is_git_ignored():
    gitignore = (config.BASE_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gitignore.splitlines()
    assert config.MEMORY_FILE.name == "memory.json"


# ---------------------------------------------------------------- routing and the AI pre-filter
@pytest.mark.parametrize("phrase, expected", [
    ("remember that dhruv's birthday is 12 feb", "remember"), ("remember i like cold coffee", "remember"),
    ("yaad rakhna ki meri bike ki service march mein hai", "remember"),
    ("dhruv ka birthday 12 feb ko hai yaad rakhna", "remember"),
    ("forget that", "forget memory"), ("bhool ja ki meri bike ki service march mein hai", "forget memory"),
    ("update: dhruv's birthday is 14 feb", "update memory"), ("list my memories", "list memories"),
    ("export my memories", "export memories"), ("take a note buy milk", "take note"),
    ("remind me in 10 minutes to drink water", "reminder"), ("forget our conversation", "forget conversation"),
    ("delete invoice.pdf", "delete file"),
])
def test_routing(recorded_skills, phrase, expected):
    assert handle(make_ctx(), phrase) == expected


def test_memory_tools_are_in_the_prefilter():
    names = {n for n, *_ in intent.tools()}
    assert {"remember", "recall_memory", "forget_memory", "update_memory"} <= names
    shortlist = intent.shortlist(make_ctx(), "tujhe yaad hai maine kya bola tha bike ke baare mein")
    assert "recall_memory" in shortlist
