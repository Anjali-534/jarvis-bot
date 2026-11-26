"""Jarvis answers EVERY question like a close friend (AI and search mocked), with the right Hindi genders.

Branches for anything that isn't a skill: a) web search, b) chat answer, c) can't do -> offer the closest thing,
d) unclear -> one clarifying question. Plus: AI down, chat follow-ups with 10-turn memory, language matching.
"""
import re

import pytest
from helpers import make_ctx, openai_stream
from test_backup_ai import PER_MINUTE, Response as BackupResponse, backends  # noqa: F401  (fixture)
from test_intent import ai_router, tool_call  # noqa: F401  (fixture)

import config
from jarvis import http, intent, persona
from jarvis.router import handle
from jarvis.skills import ai, browse


@pytest.fixture
def friend(monkeypatch):
    monkeypatch.setattr(config, "PERSONALITY", "friend")
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    monkeypatch.setattr(config, "TTS_VOICE", "en-IN-PrabhatNeural")  # Jarvis is male
    monkeypatch.setattr(config, "USER_GENDER", "female")


def routing_prompt(ai_router, index=0):
    return ai_router.requests[index]["payload"]["messages"][0]["content"]


# ---------------------------------------------------------------- a) current facts -> web search
def test_a_current_facts_go_to_the_web(ai_router, friend, monkeypatch):
    looked_up = []
    monkeypatch.setattr(browse, "look_up", lambda ctx, query, question=None, earlier=None:
                        looked_up.append(query) or "")
    ai_router.append(tool_call("web_search", query="is the delhi metro magenta line still closed"))
    assert handle(make_ctx(), "is the magenta line still closed") == "web search"
    assert looked_up == ["is the delhi metro magenta line still closed"]
    rules = routing_prompt(ai_router)
    assert "'is X still...'" in rules and "anything after your training data" in rules


# ---------------------------------------------------------------- b) knowledge, advice, opinions -> chat answer
def test_b_questions_get_a_friendly_answer(ai_router, friend):
    ai_router.append(tool_call("reply", text="Haan yaar, MBA tab karna jab goal clear ho. Pehle 2 saal kaam kar le, "
                                             "phir decide kar."))
    ctx = make_ctx()
    assert handle(ctx, "mba ke baare mein bata, karun ya nahi") == "ai"
    assert ctx.voice.said == ["Haan yaar, MBA tab karna jab goal clear ho.", "Pehle 2 saal kaam kar le, phir decide kar."]
    rules = routing_prompt(ai_router)
    assert "3) Knowledge, advice, opinions, maths, coding, chat: reply" in rules
    assert "close Delhi friend" in rules and "real opinions with reasons" in rules
    assert "CA/lawyer se confirm kar lena" in rules and "never make up facts" in rules
    assert "Pakka nahi pata yaar" in rules  # says so plainly when it doesn't know
    assert ctx.state["ai_history"][-1]["content"].startswith("Haan yaar, MBA")  # remembered for follow-ups


# ---------------------------------------------------------------- c) can't do it -> offer the closest thing
def test_c_cant_do_offers_an_alternative_and_haan_runs_it(ai_router, friend, monkeypatch):
    opened = []
    monkeypatch.setattr(browse.webbrowser, "open", opened.append)
    ai_router.append(tool_call("cant_do", text="Pizza order toh abhi nahi kar sakta yaar, par Google pe pizza "
                                               "delivery khol deta hoon?", alternative="show me pizza delivery on google"))
    ctx = make_ctx()
    assert handle(ctx, "order me a pizza") == "offer"
    assert ctx.voice.said == ["Pizza order toh abhi nahi kar sakta yaar, par Google pe pizza delivery khol deta hoon?"]
    assert handle(ctx, "haan") == "google in browser"
    assert opened == ["https://www.google.com/search?q=pizza+delivery"]


def test_c_saying_no_to_the_offer(ai_router, friend):
    ai_router.append(tool_call("cant_do", text="Cab book nahi kar sakta, Uber khol doon?", alternative="open uber"))
    ctx = make_ctx()
    handle(ctx, "book me a cab to the airport")
    assert handle(ctx, "nahi rehne do") == "offer"
    assert ctx.voice.last in {"Koi nahi, chhodo.", "Theek hai, rehne do.", "Chal, cancel."}
    assert "offer" not in ctx.state


def test_c_only_harmless_alternatives_are_kept(ai_router, friend):
    ai_router.append(tool_call("cant_do", text="Yeh nahi hoga, laptop band kar doon?", alternative="shut down the laptop"))
    ctx = make_ctx()
    handle(ctx, "make my laptop fly")
    assert ctx.state["offer"] == "show me make my laptop fly on google"  # power off is never a one-word "haan"
    assert "shut" not in ctx.state["offer"]                              # away: Google is offered instead
    assert intent.safe_alternative("search the web for pizza near me") == "search the web for pizza near me"
    assert intent.safe_alternative("close chrome") is None


def test_c_offer_without_wording_uses_a_friend_line(ai_router, friend):
    ai_router.append(tool_call("cant_do", text="", alternative="open swiggy"))
    ctx = make_ctx()
    handle(ctx, "order biryani")
    assert "open swiggy" in ctx.voice.last and ctx.state["offer"] == "open swiggy"


# ---------------------------------------------------------------- d) unclear -> one clarifying question
def test_d_unclear_asks_one_question(ai_router, friend):
    ai_router.append(tool_call("clarify", question="Kaunsa wala? Thoda bata na."))
    ctx = make_ctx()
    assert handle(ctx, "wo wala kar do") == "ai"
    assert ctx.voice.said == ["Kaunsa wala?", "Thoda bata na."]
    ai_router.append(tool_call("reply", text="Achha, woh! Samajh gaya."))
    handle(ctx, "jo kal bola tha")
    assert "You replied: Kaunsa wala? Thoda bata na." in routing_prompt(ai_router, 1)  # the answer has context


# ---------------------------------------------------------------- no dead ends
def test_never_a_dead_end_without_an_ai_key(friend, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    looked_up = []
    monkeypatch.setattr(browse, "look_up", lambda ctx, query, question=None, earlier=None:
                        looked_up.append(query) or "Here's what I found online. Sydney Opera House.")
    ctx = make_ctx()
    handle(ctx, "australia mein sabse famous jagah konsi hai")  # facts (capital of...) has its own test
    assert looked_up == ["australia mein sabse famous jagah konsi hai"] and "Opera" in ctx.voice.last


def test_ai_down_still_answers_like_a_friend(backends, friend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "AI_ROUTING", True)
    backends["groq"].append(BackupResponse(status=429, text=PER_MINUTE))
    backends["gemini"].append(BackupResponse(status=503, text="high demand"))  # the backup was tried once
    ctx = make_ctx()
    handle(ctx, "life mein kya karun yaar")
    assert ctx.voice.said == ["Net thoda slow hai yaar, ek minute mein try karte hain"]
    assert [c["who"] for c in backends["calls"]] == ["groq", "gemini"]


def test_ai_rate_limit_message_is_friendly(friend):
    assert ai.error_message(429) == "Aaj bahut baatein ho gayi yaar, AI ki limit khatam. Ek minute baad try karte hain"


# ---------------------------------------------------------------- follow-ups and memory
@pytest.fixture
def chat(monkeypatch, ai_router):  # noqa: F811
    sent = []

    def stream(url, payload, headers=None, timeout=None):
        sent.append(payload)
        return openai_stream(answers.pop(0))

    answers = []
    monkeypatch.setattr(http, "post_stream", stream)
    return {"sent": sent, "answers": answers, "router": ai_router}


def test_follow_ups_go_to_chat_with_the_conversation(chat, friend):
    chat["router"].append(tool_call("reply", text="iPhone smooth hai, Pixel ka camera aur AI features better hain."))
    ctx = make_ctx()
    handle(ctx, "kaunsa better hai iphone ya pixel")
    chat["answers"].append("Aur haan, Pixel sasta padta hai aur updates bhi 7 saal milte hain.")
    assert handle(ctx, "aur?") == "chat follow-up"
    assert len(chat["router"].requests) == 1  # no routing call for "aur?"
    messages = chat["sent"][0]["messages"]
    assert messages[1:3] == [{"role": "user", "content": "kaunsa better hai iphone ya pixel"},
                             {"role": "assistant", "content": "iPhone smooth hai, Pixel ka camera aur AI features "
                                                              "better hain."}]
    assert messages[-1] == {"role": "user", "content": "aur?"}
    chat["answers"].append("Kyunki Google khud Android banata hai.")
    assert handle(ctx, "kyun?") == "chat follow-up"


def test_detail_requests_get_a_longer_answer(chat, friend):
    chat["answers"] += ["Recursion matlab function khud ko call karta hai.",
                        "Socho ek Russian doll ke andar choti doll hai..."]
    ctx = make_ctx()
    assert handle(ctx, "explain recursion simply") == "chat direct"  # obvious chat: no routing call
    assert handle(ctx, "detail mein bata") == "chat follow-up"
    assert chat["sent"][1]["max_tokens"] == ai.DETAIL_TOKENS and chat["router"].requests == []


def test_aur_after_a_web_answer_reads_more_from_the_same_sources(friend, monkeypatch):
    calls = []
    monkeypatch.setattr(browse, "search", lambda q, budget=None: {"query": q, "provider": "ddgs", "summary": "",
                                                                  "at": "now", "deep": True, "sources": [
                                                                      {"title": "t", "url": "", "text": "Rs 94"}]})
    monkeypatch.setattr(browse, "speak_answer", lambda ctx, question, found, detail=False, earlier=None:
                        calls.append(detail) or ctx.state.update(last_search={"question": question, "found": found})
                        or "")
    ctx = make_ctx()
    handle(ctx, "search the web for petrol price in delhi")
    assert handle(ctx, "aur?") == "tell me more"
    assert calls == [False, True]  # the longer answer, from the same search


def test_offered_sites_open(monkeypatch):
    from jarvis.skills import apps, web

    opened = []
    monkeypatch.setattr(web, "open_url", opened.append)
    assert apps.open_target("swiggy") == "swiggy" and opened == ["https://www.swiggy.com"]


def test_aur_with_nothing_to_follow_is_routed(chat, friend):
    chat["router"].append(tool_call("clarify", question="Aur kya? Kis baare mein?"))
    assert handle(make_ctx(), "aur?") == "ai"


def test_chat_remembers_10_turns(chat, friend):
    ctx = make_ctx()
    for i in range(12):
        chat["answers"].append(f"Answer number {i}.")
        ai.ai(ctx, f"question number {i}")
    assert len(ctx.state["ai_history"]) == 20  # 10 exchanges
    assert ctx.state["ai_history"][0]["content"] == "question number 2"
    assert len(chat["sent"][-1]["messages"]) == 1 + 20 + 1  # system + 10 turns + the new question


# ---------------------------------------------------------------- language matching
def test_language_rule_in_every_prompt(ai_router, friend):  # noqa: F811
    rule = "English question -> English answer; Hinglish -> Hinglish; Hindi (Devanagari) -> Hinglish"
    assert rule in ai.system_prompt() and rule in persona.ai_style()
    ai_router.append(tool_call("reply", text="Sure! Recursion is a function calling itself."))
    handle(make_ctx(), "what's a good name for a cat")
    assert "match their language (English, or Hinglish in Roman letters)" in routing_prompt(ai_router)
    from jarvis.skills import browse as b

    system, _ = b.answer_prompt("aaj sona kitne ka hai", {"query": "q", "at": "now", "provider": "ddgs",
                                                           "sources": [{"title": "t", "url": "", "text": "x"}]},
                                hinglish=True)
    assert "Reply in Hinglish" in system


# ---------------------------------------------------------------- Hindi genders
_GENDERED = re.compile(r"\b(?:akela|akeli|gaya|gayi|gai|raha|rahi|kaisa|kaisi|sakta|sakti|dunga|dungi|karta|karti|dekhta|dekhti|"
                       r"leta|leti|deta|deti|sunta|sunti|jaunga|jaungi|jaata|jaati|thaka|thaki|paya|payi|aaya|aayi|"
                       r"soya|soyi|samjha|samjhi|hua|hui|gaye|lagi|laga)\b(?!\{)",  # laga{ta}: a placeholder
                       re.IGNORECASE)
# Literal forms whose subject is a thing, not Jarvis or the user ("Shaam ho gayi", "AI jawab nahi de raha")
_NEUTRAL = re.compile(r"lunch hua|chal raha|din kaisa raha|ho gayi|ho gaya|ho gaye|baj gaye|atak gaya|ho pa raha|"
                      r"ho pa rahi|laga diya|laga doon|haath nahi laga|pareshan kar raha|kya hua|bhookh lagi|"
                      r"neend nahi aa rahi|jawab nahi de raha|soojh raha|fikar ho rahi|khayal aa raha",
                      re.IGNORECASE)


def all_templates():
    return [t for entry in persona.PHRASES.values() for options in entry.values() for t in options] + \
        [t for options in persona.AQI_FRIEND.values() for t in options]


def test_no_hard_coded_gender_in_any_phrase():
    """Verbs about Jarvis or the user must use {ta}/{ga}/{a} or {u_a}/{u_ta}, so they follow the settings."""
    wrong = [(t, m.group()) for t in all_templates() for m in _GENDERED.finditer(_NEUTRAL.sub("", t))]
    assert wrong == []


def render_all(**extra):
    values = dict(name="Anjali", assistant="Jarvis", part_of_day="morning", when="in 10 minutes", message="m",
                  action="restart", delay="15 seconds", duration="5 minutes", text="t", count="2 notes", time="2",
                  alternative="Google pe dhoondh doon", place="Rohini", aqi="312", day="12 February", every="", memory="m", topic="bike", old="o", new="n", folder="Documents", 
                  **persona._verb_forms(), **extra)
    return " ".join(t.format(**values) for t in all_templates())


@pytest.mark.parametrize("voice, user, right, wrong", [
    ("en-IN-PrabhatNeural", "female",
     ["kar dunga", "wapas aa gaya!", "dekhta hoon", "Arre aa gayi!", "kaisi hai", "thak gayi kya", "sakti hai",
      "rahi hai"],
     ["dungi", "dekhti", "kar sakti hoon", "Arre aa gaya!", "kaisa hai", "thak gaya kya", "jaag raha hai"]),
    ("hi-IN-SwaraNeural", "male",
     ["kar dungi", "dekhti hoon", "wapas aa gayi!", "Arre aa gaya!", "kaisa hai", "thak gaya kya", "sakta hai"],
     ["dunga", "dekhta hoon", "Arre aa gayi!", "kaisi hai", "thak gayi kya"]),
])
def test_genders_render_right_both_ways(monkeypatch, voice, user, right, wrong):
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    monkeypatch.setattr(config, "TTS_VOICE", voice)
    monkeypatch.setattr(config, "USER_GENDER", user)
    text = render_all()
    missing = [w for w in right if w.lower() not in text.lower()]
    present = [w for w in wrong if re.search(rf"\b{re.escape(w)}", text, re.IGNORECASE)]
    assert missing == [] and present == []


def test_ai_is_told_both_genders(friend, monkeypatch):
    style = persona.ai_style()
    assert "you are male, so use male verb forms for yourself ('main kar dunga', 'main aa gaya'" in style
    assert "Anjali is female, so address her with female forms ('tu kaisi hai?', 'thak gayi kya?'" in style
    monkeypatch.setattr(config, "USER_GENDER", "male")
    assert "address him with male forms ('tu kaisa hai?'" in persona.ai_style()


def test_voice_gender_is_detected_from_the_voice_name(monkeypatch):
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    for name, female in [("en-IN-PrabhatNeural", False), ("hi-IN-MadhurNeural", False), ("hi-IN-SwaraNeural", True),
                         ("en-IN-NeerjaNeural", True)]:
        monkeypatch.setattr(config, "TTS_VOICE", name)
        assert persona.voice_is_female() is female
    monkeypatch.setattr(config, "TTS_ENGINE", "pyttsx3")
    monkeypatch.setattr(config, "VOICE_NAME", "david")
    assert persona.voice_is_female() is False
