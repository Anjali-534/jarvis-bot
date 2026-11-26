"""Factual questions are checked on the web before answering (skills/facts.py); "pakka nahi hoon" if not confirmed.
The search engine and the AI are faked: the AI may only use what the search returned."""
import pytest
from helpers import make_ctx, openai_stream

import config
from jarvis import http, router
from jarvis.router import handle
from jarvis.skills import browse, facts

PAGES = {
    "godaan": [{"title": "Godaan - Wikipedia", "href": "https://en.wikipedia.org/wiki/Godaan",
                "body": "Godaan is a Hindi novel by Munshi Premchand, published in 1936."}],
    "capital australia": [{"title": "Canberra - Wikipedia", "href": "https://en.wikipedia.org/wiki/Canberra",
                           "body": "Canberra is the capital city of Australia."}],
    "taj mahal": [{"title": "Taj Mahal", "href": "https://en.wikipedia.org/wiki/Taj_Mahal",
                   "body": "The Taj Mahal was built between 1632 and 1653 by Shah Jahan."}],
}
# what the (fake) AI says, given the search results it was handed
ANSWERS = {"Munshi Premchand": "Godaan was written by Munshi Premchand, in 1936.",
           "Canberra is the capital": "The capital of Australia is Canberra, not Sydney.",
           "1632 and 1653": "The Taj Mahal was built between 1632 and 1653."}


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "groq-test-key")
    monkeypatch.setattr(config, "AI_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(config, "AI_MODEL", "qwen/qwen3.8-27b")
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "ddgs")
    state = {"searches": [], "prompts": [], "pages": PAGES}

    def ddgs_text(query):
        state["searches"].append(query)
        for key, hits in state["pages"].items():
            if all(word in query.lower() for word in key.split()):
                return [dict(h) for h in hits]
        return []

    def post_stream(url, payload, headers=None, timeout=None):
        prompt = "\n".join(m["content"] for m in payload["messages"])
        state["prompts"].append(prompt)
        reply = next((answer for found, answer in ANSWERS.items() if found in prompt), browse.NO_ANSWER)
        return openai_stream(reply)

    monkeypatch.setattr(browse, "ddgs_text", ddgs_text)
    monkeypatch.setattr(browse, "download", lambda url, timeout=4: None)
    monkeypatch.setattr(http, "post_stream", post_stream)
    return state


@pytest.mark.parametrize("question, answer", [
    ("who wrote godaan", "Munshi Premchand"),
    ("who wrote godaan?", "Munshi Premchand"),
    ("godaan kisne likha", "Munshi Premchand"),
    ("what is the capital of australia", "Canberra"),
    ("capital of australia", "Canberra"),
    ("australia ki rajdhani kya hai", "Canberra"),
    ("when was the taj mahal built", "1632 and 1653"),
    ("taj mahal kab bana", "1632 and 1653"),
])
def test_facts_are_searched_before_answering(web, question, answer):
    ctx = make_ctx()
    assert handle(ctx, question) == "fact check"
    assert web["searches"], "it must search before answering"
    assert answer in " ".join(ctx.voice.said)
    assert "using only" in web["prompts"][0].lower()  # the AI answered from the results, not from memory


def test_the_answer_comes_from_the_results_not_memory(web):
    """If the web says something, that's what is spoken (the fake AI only knows what it was handed)."""
    web["pages"] = {"godaan": [{"title": "Godaan", "href": "https://example.com/godaan",
                                "body": "Godaan is a Hindi novel by Munshi Premchand."}]}
    ctx = make_ctx()
    handle(ctx, "who wrote godaan")
    assert "Munshi Premchand" in ctx.voice.said[-1]
    assert "Godaan is a Hindi novel by Munshi Premchand" in web["prompts"][0]


def test_not_confirmed_says_not_sure_and_offers_google(web, monkeypatch):
    monkeypatch.setattr(config, "PERSONALITY", "friend")
    ctx = make_ctx()
    handle(ctx, "who invented the zorblax engine")  # nothing found online
    assert ctx.voice.last.startswith(("Pakka nahi hoon", "Sach bolun toh pakka nahi hoon"))
    assert ctx.state["offer"] == "show me who invented the zorblax engine on google"


def test_results_that_dont_answer_never_get_guessed(web):
    """The search found pages, but they don't say: the AI's "couldn't find" is held back, "not sure" is said."""
    web["pages"] = {"zorblax": [{"title": "Zorblax fan page", "href": "https://example.com/z", "body": "Nice engine."}]}
    ctx = make_ctx()
    handle(ctx, "who invented the zorblax engine")
    assert browse.NO_ANSWER not in ctx.voice.said
    assert ctx.voice.last.startswith("I'm not sure, and I couldn't confirm it online")


def test_haan_opens_google_after_not_sure(web, monkeypatch):
    opened = []
    monkeypatch.setattr(browse.webbrowser, "open", opened.append)
    ctx = make_ctx()
    handle(ctx, "who invented the zorblax engine")
    handle(ctx, "haan")
    assert opened and "zorblax" in opened[0]


@pytest.mark.parametrize("phrase, expected", [
    ("who wrote godaan", "fact check"), ("who founded infosys", "fact check"), ("who invented the telephone", "fact check"),
    ("what is the capital of australia", "fact check"), ("when was india's constitution adopted", "fact check"),
    ("how many states are there in india", "fact check"), ("in which year did the titanic sink", "fact check"),
    ("godaan kisne likha", "fact check"), ("australia ki rajdhani kya hai", "fact check"),
    ("india mein kitne states hain", "fact check"),
    # not world facts: they stay where they were
    ("how many emails do i have", "ai"), ("when is my next meeting", "next meeting"),
    ("who is sachin tendulkar", "wikipedia"), ("what is python", "wikipedia"),
    ("write a poem about rain", "ai"), ("what is 25 times 4", "calculator"),
])
def test_routing(recorded_skills, phrase, expected):
    assert handle(make_ctx(), phrase) == expected


def test_without_an_ai_key_the_search_snippet_is_read(web, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    ctx = make_ctx()
    handle(ctx, "who wrote godaan")
    assert "Munshi Premchand" in ctx.voice.last


def test_fact_check_is_before_wikipedia_and_the_ai():
    names = [s.name for s in router.SKILLS]
    assert names.index("fact check") < names.index("wikipedia") < names.index("chat direct")
    assert names.index("fact check") < names.index("web question")
    assert facts.clean("jarvis, can you tell me who wrote godaan?") == "who wrote godaan"
