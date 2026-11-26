"""Sensible friend advice (safety rules in the prompts, the crisis response) and lean routing (AI mocked)."""
import json

import pytest
from helpers import make_ctx, openai_stream
from test_intent import Response, ai_router, tool_call  # noqa: F401  (fixture)

import config
from jarvis import http, intent, persona, timing
from jarvis.router import SKILLS, handle
from jarvis.skills import ai


@pytest.fixture
def friend(monkeypatch):
    monkeypatch.setattr(config, "PERSONALITY", "friend")


# ---------------------------------------------------------------- 1. good-friend rules in the chat prompt (15)
RULES = [
    "advice must be correct and safe",
    "never suggest chai, coffee or anything with caffeine at night",
    "putting the phone away",
    "dim lights",
    "haldi doodh",
    "slow breathing",
    "a calm playlist",
    "never suggest anything risky",
    "no medicine doses",
    "chest pain, breathing problems, or symptoms lasting days",
    "casually tell them to see a doctor",
    "no jokes in that moment",
    "ask how they're doing, suggest talking to someone they trust",
    "Tele-MANAS helpline, 14416",
    "'CA/lawyer se confirm kar lena' when it matters",
]


@pytest.mark.parametrize("rule", RULES)
def test_chat_prompt_has_the_good_friend_rule(friend, rule):
    assert rule in ai.system_prompt()


def test_professional_mode_keeps_the_safety_rules():
    prompt = ai.system_prompt()
    assert "close friend" not in prompt
    assert "haldi doodh" in prompt and "Tele-MANAS helpline, 14416" in prompt and "see a doctor" in prompt


def test_routing_prompt_has_the_short_safety_rules(friend):
    text = intent.system_text(make_ctx())
    for rule in ("no caffeine for sleep", "doctor for pain, fever, chest or breathing trouble", "Tele-MANAS 14416",
                 "'CA/lawyer se confirm kar lena'"):
        assert rule in text


# ---------------------------------------------------------------- the crisis response: always, AI or not
@pytest.mark.parametrize("said", ["i want to die", "i don't want to live anymore", "sometimes i think about suicide",
                                  "i want to hurt myself", "marna chahti hoon", "jeena nahi chahti",
                                  "i'm so sad i want to die"])
def test_crisis_gets_care_and_the_helpline(friend, monkeypatch, said):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")  # the AI is never asked: http is blocked in tests
    ctx = make_ctx()
    assert handle(ctx, said) == "crisis"
    assert "14416" in ctx.voice.last and "112" in ctx.voice.last
    assert "Tele-MANAS" in ctx.voice.last and "haha" not in ctx.voice.last.lower()


def test_crisis_in_professional_mode_too():
    ctx = make_ctx()
    handle(ctx, "i want to end my life")
    assert ctx.voice.last.startswith("I'm really sorry you're feeling this way") and "14416" in ctx.voice.last


def test_crisis_is_not_in_the_help_list():
    from jarvis.skills import general

    assert "want to die" not in general.full_command_list().lower()


def test_low_mood_goes_to_the_caring_chat(friend, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    sent = []
    monkeypatch.setattr(http, "post_stream", lambda url, payload, **k: sent.append(payload) or openai_stream(
        "Arre, kya hua yaar? Aaj kya hua, bata na."))
    ctx = make_ctx()
    assert handle(ctx, "i feel really low today") == "feeling"
    assert ctx.voice.said == ["Arre, kya hua yaar?", "Aaj kya hua, bata na."]
    assert "Tele-MANAS helpline, 14416" in sent[0]["messages"][0]["content"]


# ---------------------------------------------------------------- 2. the local pre-filter
@pytest.mark.parametrize("command, tool", [
    ("yaar thoda sound badha do", "volume"), ("awaaz kam karo thodi", "volume"),
    ("kal subah 9 baje gym ki yaad dila dena", "reminder"), ("mom ko whatsapp pe bolo main late hoon", "whatsapp_message"),
    ("mausam kaisa hai noida mein", "weather"), ("noida ka pradushan kitna hai", "air_quality"),
    ("spotify pe arijit ke gaane bajao", "play"), ("shut down the laptop in 10 minutes", "power"),
    ("set a timr for 5 minutes", "timer"), ("dhruv ko mail likho ki demo 5 baje hai", "email_draft"),
    ("ek note likh lo milk lana hai", "take_note"), ("kal 5 baje dentist ki meeting add karo", "calendar_add"),
])
def test_shortlist_finds_the_right_tool(command, tool):
    picked = intent.shortlist(make_ctx(), command)
    assert tool in picked and "web_search" in picked
    assert len(picked) <= intent.SHORTLIST + 2


def test_shortlist_keeps_the_previous_turns_tool():
    ctx = make_ctx()
    ctx.state["turns"] = [{"you": "set a timer for 10 minutes", "understood": "set a timer for 10 minutes",
                           "skill": "timer", "jarvis": ""}]
    assert "timer" in intent.shortlist(ctx, "make it 5 minutes instead")


def test_routed_question_stays_small(ai_router, friend):  # noqa: F811
    ai_router.append(tool_call("reply", text="Canberra hai, Sydney nahi!"))
    handle(make_ctx(), "australia mein sabse famous jagah konsi hai")  # not a fact-check pattern
    body = ai_router.requests[0]["payload"]
    names = [t["function"]["name"] for t in body["tools"]]
    assert names == ["web_search", "reply"]
    assert len(json.dumps(body, ensure_ascii=False)) // 4 < 700  # Groq counted ~800 tokens for this live


def test_reply_kinds_cover_cant_do_and_clarify(ai_router, friend):  # noqa: F811
    ai_router.append(tool_call("reply", kind="cant_do", text="Pizza order nahi kar sakta, Swiggy khol doon?",
                               alternative="open swiggy"))
    ctx = make_ctx()
    assert handle(ctx, "order me a pizza") == "offer" and ctx.state["offer"] == "open swiggy"
    ai_router.append(tool_call("reply", kind="clarify", text="Kaunsa wala?"))
    assert handle(make_ctx(), "wo wala kar do") == "ai"


def test_cant_do_without_an_alternative_still_offers_google(ai_router, friend, monkeypatch):  # noqa: F811
    from jarvis.skills import browse

    opened = []
    monkeypatch.setattr(browse.webbrowser, "open", opened.append)
    ai_router.append(tool_call("reply", kind="cant_do", text="Pizza order karne ka tool mere paas nahi hai yaar."))
    ctx = make_ctx()
    assert handle(ctx, "order me a pizza") == "offer"
    assert ctx.voice.last.endswith("Google pe khol doon?") and ctx.state["offer"] == "show me order me a pizza on google"
    handle(ctx, "haan")
    assert opened == ["https://www.google.com/search?q=order+me+a+pizza"]


# ---------------------------------------------------------------- obvious chat skips routing
@pytest.mark.parametrize("said", ["tu kya sochta hai, mujhe mba karna chahiye?", "what do you think about remote work",
                                  "mujhe neend nahi aa rahi", "sar dard ho raha hai", "explain recursion simply",
                                  "kya main saara paisa crypto mein daal doon?", "tu kaisa hai", "should i learn rust"])
def test_obvious_chat_skips_routing(ai_router, friend, monkeypatch, said):  # noqa: F811
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: openai_stream("Theek hai yaar."))
    assert handle(make_ctx(), said) == "chat direct"
    assert ai_router.requests == []  # one AI call, not two


def test_obvious_chat_without_an_ai_key_is_not_a_dead_end(monkeypatch):
    names = [s.name for s in SKILLS]
    assert names.index("chat direct") < names.index("chat follow-up")
    assert ai.chat_direct(make_ctx(), "tu kya sochta hai", None) is None  # no key: the normal path (web search)


# ---------------------------------------------------------------- chat history: 10 turns within ~600 tokens
def test_long_history_is_summarised():
    history = []
    for i in range(10):
        history += [{"role": "user", "content": f"question {i} about cricket " + "word " * 60},
                    {"role": "assistant", "content": "answer " * 80}]
    recent, summary = ai.compact_history(history)
    assert sum(ai._tokens(m["content"]) for m in recent) <= ai.HISTORY_TOKENS
    assert recent[-2]["content"].startswith("question 9") and len(recent) % 2 == 0
    assert summary.startswith("Earlier in this chat the user talked about: question 0 about cricket")
    assert len(summary) <= 300


def test_short_history_is_sent_whole():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert ai.compact_history(history) == (history, "")


def test_chat_sends_the_summary_in_the_system_prompt(friend, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    sent = []
    monkeypatch.setattr(http, "post_stream", lambda url, payload, **k: sent.append(payload) or openai_stream("Ok."))
    ctx = make_ctx()
    ctx.state["ai_history"] = [m for i in range(10) for m in (
        {"role": "user", "content": f"topic {i} " + "x " * 200}, {"role": "assistant", "content": "y " * 200})]
    ai.ai(ctx, "aur?")
    messages = sent[0]["messages"]
    assert "Earlier in this chat the user talked about: topic 0" in messages[0]["content"]
    assert len(messages) < 22


# ---------------------------------------------------------------- input tokens in --timing
def test_timing_shows_input_tokens(ai_router, friend, monkeypatch):  # noqa: F811
    reply = tool_call("reply", text="Canberra.")
    reply._data["usage"] = {"prompt_tokens": 812}
    ai_router.append(reply)
    turn = timing.begin()
    handle(make_ctx(), "australia mein sabse famous jagah konsi hai")
    assert turn.tokens["routing"] == (812, False)
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: openai_stream("Theek hai."))
    handle(make_ctx(), "tu kya sochta hai")
    count, estimated = turn.tokens["chat"]
    assert estimated and 100 < count < 1500
    line = timing.finish()
    assert "routing input 812 tokens" in line and f"chat input ~{count} tokens" in line
