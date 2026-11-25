"""Live web browsing (search, fetch, AI all mocked): providers, trimming, answers, follow-ups, Hinglish, timeouts."""
import json
import logging
import time

import pytest
from helpers import make_ctx, openai_stream

import config
from jarvis import cache, http
from jarvis.router import handle
from jarvis.skills import browse

REAL_DDGS_TEXT = browse.ddgs_text  # conftest blocks it; the race tests below call the real one

GROQ = "https://api.groq.com/openai/v1"
PETROL_PAGE = ("Petrol price in Delhi\n" + "Delhi is the capital of India and fuel prices matter to everyone. " * 30 +
               "\nOn 23 November 2026 petrol in Delhi costs Rs 94.77 per litre, unchanged from yesterday.\n" +
               "Diesel and CNG rates are listed in the table below. " * 20)
HITS = [
    {"title": "Petrol Price in Delhi Today", "href": "https://www.goodreturns.in/petrol-delhi.html", "body": "Rs 94.77 today"},
    {"title": "Fuel rate report", "href": "https://example.org/report.pdf", "body": "PDF snippet about fuel"},
    {"title": "Petrol rates - WSJ", "href": "https://www.wsj.com/petrol", "body": "Paywalled snippet"},
    {"title": "My Petrol Price", "href": "https://www.mypetrolprice.com/delhi", "body": "Delhi petrol 94.77"},
    {"title": "Fuel news", "href": "https://news.example.com/fuel", "body": "Fuel prices steady in metros"},
]


class Response:
    def __init__(self, data, status=200):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


def tool_call(name, **args):
    return Response({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]})


@pytest.fixture
def web(monkeypatch):
    """Groq for the AI (no compound model), DuckDuckGo hits from HITS, pages from web['pages']."""
    monkeypatch.setattr(config, "AI_API_KEY", "groq-test-key")
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(config, "AI_BASE_URL", GROQ)
    monkeypatch.setattr(config, "AI_MODEL", "qwen/qwen3.8-27b")
    state = {"searches": [], "downloads": [], "answers": [], "prompts": [], "routes": [],
             "pages": {HITS[0]["href"]: PETROL_PAGE, HITS[3]["href"]: "Delhi petrol is Rs 94.77 a litre. " * 20},
             "models": {"data": [{"id": "qwen/qwen3.8-27b"}]}, "model_lists": 0, "opened": []}

    def ddgs_text(query):
        state["searches"].append(query)
        return [dict(h) for h in HITS]

    def download(url, timeout=browse.PAGE_TIMEOUT):
        state["downloads"].append(url)
        return state["pages"].get(url)

    def post_stream(url, payload, headers=None, timeout=None):
        state["prompts"].append(payload)
        return openai_stream(state["answers"].pop(0) if state["answers"] else "Petrol costs Rs 94.77 a litre "
                             "in Delhi as of 23 November 2026. That's unchanged from yesterday.")

    def post_json(url, payload, headers=None, timeout=None):
        state["routes"].append({"payload": payload, "timeout": timeout})
        return state["route_reply"].pop(0)

    def get_json(url, params=None, headers=None, timeout=None, **kwargs):
        state["model_lists"] += 1
        return state["models"]

    monkeypatch.setattr(browse, "ddgs_text", ddgs_text)
    monkeypatch.setattr(browse, "download", download)
    monkeypatch.setattr(browse, "extract_text", lambda html: html)
    monkeypatch.setattr(browse.webbrowser, "open", state["opened"].append)
    monkeypatch.setattr(http, "post_stream", post_stream)
    monkeypatch.setattr(http, "post_json", post_json)
    monkeypatch.setattr(http, "get_json", get_json)
    state["route_reply"] = []
    return state


def prompt_text(payload):
    return "\n".join(m["content"] for m in payload["messages"])


# ---------------------------------------------------------------- the basic flow
def test_search_answers_by_voice(web, caplog):
    ctx = make_ctx()
    with caplog.at_level(logging.INFO):
        assert handle(ctx, "search the web for petrol price in delhi today") == "web search"
    assert ctx.voice.said == [browse.CHECKING, "Petrol costs Rs 94.77 a litre in Delhi as of 23 November 2026.",
                              "That's unchanged from yesterday."]  # streamed, sentence by sentence
    assert web["searches"] == ["petrol price in delhi today"]
    prompt = prompt_text(web["prompts"][0])
    assert "ONLY the numbered web sources" in prompt and browse.NO_ANSWER in prompt
    assert "[1] Petrol Price in Delhi Today (goodreturns.in)" in prompt and "Rs 94.77 per litre" in prompt
    assert "say the date they apply to" in prompt and "Reply in English." in prompt
    assert "Sources (ddgs):" in caplog.text and "[1] Petrol Price in Delhi Today - https://www.goodreturns.in" in caplog.text


@pytest.mark.parametrize("phrase, query", [
    ("google best laptops under 60000", "best laptops under 60000"),
    ("look up the population of japan", "the population of japan"),
    ("search for pollution in delhi", "pollution in delhi"),
    ("search online for cheap flights to goa", "cheap flights to goa"),
    ("ipl final score internet pe dekho", "ipl final score"),
    ("internet par dekho sensex today", "sensex today"),
])
def test_search_phrases(web, phrase, query):
    assert handle(make_ctx(), phrase) == "web search"
    assert web["searches"] == [query]


def test_reads_top_3_pages_and_skips_pdfs_and_paywalls(web):
    found = browse.search("petrol price in delhi today")
    assert web["downloads"] == [HITS[0]["href"], HITS[3]["href"], HITS[4]["href"]]  # not the PDF or WSJ
    urls = [s["url"] for s in found["sources"]]
    assert urls[:2] == [HITS[0]["href"], HITS[3]["href"]]  # pages actually read come first
    assert set(urls[2:]) == {HITS[1]["href"], HITS[2]["href"], HITS[4]["href"]}  # the rest: search snippets
    assert next(s for s in found["sources"] if s["url"] == HITS[4]["href"])["text"] == "Fuel prices steady in metros"


def test_paywall_and_error_pages_are_skipped(monkeypatch):
    monkeypatch.setattr(browse, "extract_text", lambda html: html)
    monkeypatch.setattr(browse, "download", lambda url, timeout=4: "Subscribe to continue reading. " + "word " * 100)
    assert browse.read_page("https://news.example.com/a") is None
    monkeypatch.setattr(browse, "download", lambda url, timeout=4: "403 Forbidden")
    assert browse.read_page("https://news.example.com/a") is None
    assert not browse.fetchable("https://x.org/file.PDF") and not browse.fetchable("https://www.ft.com/x")


# ---------------------------------------------------------------- providers
def test_tavily_first_when_its_key_is_set(web, monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-test")
    calls = []

    def tavily(query, timeout):
        calls.append((query, timeout))
        return {"answer": "Rs 94.77 per litre.", "results": [
            {"title": "Fuel prices", "url": "https://fuel.example.com", "content": "Petrol Rs 94.77 in Delhi today."}]}

    monkeypatch.setattr(browse, "tavily_search", tavily)
    found = browse.search("petrol price in delhi")
    assert found["provider"] == "tavily" and found["summary"] == "Rs 94.77 per litre."
    assert calls[0][0] == "petrol price in delhi" and 0 < calls[0][1] <= 8
    assert web["searches"] == [] and web["model_lists"] == 0


def test_tavily_failing_falls_back_to_groq_compound(web, monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(browse, "tavily_search", lambda q, t: (_ for _ in ()).throw(RuntimeError("401")))
    web["models"] = {"data": [{"id": "qwen/qwen3.8-27b"}, {"id": "groq/compound-mini"}]}
    web["route_reply"].append(Response({"choices": [{"message": {"content": "India won by 5 wickets on 22 Nov.",
        "executed_tools": [{"search_results": {"results": [
            {"title": "Match report", "url": "https://cricket.example.com/r", "content": "India won by 5 wickets."},
            {"title": "Scorecard", "url": "https://scores.example.com/s", "content": "IND 251/5 beat AUS 250."}]}}]}}]}))
    found = browse.search("who won the india match yesterday")
    assert found["provider"] == "groq" and [s["title"] for s in found["sources"]] == ["Match report", "Scorecard"]
    assert web["routes"][0]["payload"]["model"] == "groq/compound-mini"
    assert web["searches"] == []


def test_groq_compound_unavailable_is_skipped_and_remembered(web):
    browse.search("latest iphone")
    browse.search("latest pixel")
    assert web["searches"] == ["latest iphone", "latest pixel"]  # DuckDuckGo both times
    assert web["model_lists"] == 1  # Groq's model list checked once, then cached
    assert web["routes"] == []


def test_search_provider_setting(web, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "ddgs")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(browse, "tavily_search", lambda *a: pytest.fail("tavily is not in SEARCH_PROVIDER"))
    assert browse.provider_order() == ["ddgs"]
    assert browse.search("latest iphone")["provider"] == "ddgs"
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "auto")
    assert browse.provider_order() == ["tavily", "groq", "ddgs"]


def test_every_provider_failing(web, monkeypatch):
    monkeypatch.setattr(browse, "ddgs_text", lambda q: (_ for _ in ()).throw(RuntimeError("No results found.")))
    ctx = make_ctx()
    handle(ctx, "search the web for something obscure")
    assert ctx.voice.said == [browse.CHECKING, browse.NOTHING_FOUND]
    assert web["prompts"] == []  # the AI isn't asked to make something up


# ---------------------------------------------------------------- trimming
def test_trimming_keeps_about_1500_words_and_the_relevant_part():
    long_page = PETROL_PAGE * 4
    sources = [{"title": "a", "url": "https://a.com", "text": long_page},
               {"title": "b", "url": "https://b.com", "text": "Short snippet: Rs 94.77."},
               {"title": "c", "url": "https://c.com", "text": "filler words here " * 2000}]
    trimmed = browse.trim_sources(sources, "petrol price in delhi")
    total = sum(len(s["text"].split()) for s in trimmed)
    assert 1200 < total <= browse.MAX_WORDS
    assert trimmed[1]["text"] == "Short snippet: Rs 94.77."  # short sources are kept whole
    assert "Rs 94.77 per litre" in trimmed[0]["text"]  # the paragraph that answers the question survives


def test_relevant_text_cuts_one_huge_paragraph():
    assert len(browse.relevant_text("word " * 5000, "x", 100).split()) == 100
    assert browse.relevant_text("short text", "x", 100) == "short text"


# ---------------------------------------------------------------- answers
def test_no_reliable_answer(web):
    web["answers"].append(browse.NO_ANSWER)
    ctx = make_ctx()
    handle(ctx, "search the web for the exact number of ants on earth today")
    assert ctx.voice.said == [browse.CHECKING, browse.NO_ANSWER]


def test_hinglish_question_gets_hinglish_answer(web, monkeypatch):
    monkeypatch.setattr(config, "AI_ROUTING", True)
    web["route_reply"].append(tool_call("web_search", query="petrol price in delhi today"))
    web["answers"].append("Delhi mein aaj petrol Rs 94.77 per litre hai, 23 November 2026 tak.")
    ctx = make_ctx()
    assert handle(ctx, "aaj delhi mein petrol ka rate kya hai") == "web search"
    prompt = prompt_text(web["prompts"][0])
    assert "Reply in Hinglish (Hindi written ONLY in Roman letters" in prompt
    assert "Question: aaj delhi mein petrol ka rate kya hai" in prompt  # the user's own words, not the English query
    assert web["searches"] == ["petrol price in delhi today"]
    assert ctx.voice.last == "Delhi mein aaj petrol Rs 94.77 per litre hai, 23 November 2026 tak."


def test_devanagari_question_gets_hinglish_answer(web):
    ctx = make_ctx()
    ctx.state["heard"] = "आज दिल्ली में पेट्रोल का रेट क्या है"
    browse.look_up(ctx, "petrol price in delhi today", question=ctx.state["heard"])
    assert "Reply in Hinglish" in prompt_text(web["prompts"][0])


def test_without_an_ai_key_it_reads_what_it_found(web, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    ctx = make_ctx()
    handle(ctx, "search the web for petrol price in delhi")
    assert ctx.voice.last.startswith("Here's what I found online. Petrol price in Delhi")


# ---------------------------------------------------------------- follow-ups
def test_tell_me_more_uses_the_same_sources(web):
    ctx = make_ctx()
    handle(ctx, "search the web for petrol price in delhi today")
    web["answers"].append("Petrol is Rs 94.77 a litre in Delhi as of 23 November 2026. Diesel and CNG are listed too.")
    assert handle(ctx, "tell me more") == "tell me more"
    assert len(web["searches"]) == 1  # no new search
    assert web["prompts"][1]["max_tokens"] == browse.DETAIL_TOKENS
    assert "5 to 7 short sentences" in prompt_text(web["prompts"][1])


def test_tell_me_more_without_a_search_is_chat(web):
    ctx = make_ctx()
    assert handle(ctx, "tell me more") == "ai"


@pytest.mark.parametrize("phrase", ["which website said that", "source?", "sources", "where did you read that"])
def test_which_website(web, phrase):
    ctx = make_ctx()
    handle(ctx, "search the web for petrol price in delhi today")
    assert handle(ctx, phrase) == "search sources"
    assert ctx.voice.last == ("That came from goodreturns.in, mypetrolprice.com and example.org. "
                              "The full list is in the terminal.")


def test_which_website_before_any_search(web):
    ctx = make_ctx()
    handle(ctx, "which website said that")
    assert ctx.voice.last == browse.NO_SEARCH_YET


@pytest.mark.parametrize("phrase, url", [
    ("open the first source", HITS[0]["href"]),
    ("open that in chrome", HITS[0]["href"]),
    ("open the second link", HITS[3]["href"]),
    ("show me the first result in the browser", HITS[0]["href"]),
])
def test_open_a_source(web, phrase, url):
    ctx = make_ctx()
    handle(ctx, "search the web for petrol price in delhi today")
    assert handle(ctx, phrase) == "open source"
    assert web["opened"] == [url]


def test_open_that_without_a_search_is_the_normal_open_skill(web):
    from jarvis.router import SKILLS

    ctx = make_ctx()
    open_skill = next(s for s in SKILLS if s.name == "open source")
    match = open_skill.patterns[0].search("open that in chrome")
    assert browse.open_source(ctx, "open that in chrome", match) is None  # falls through


def test_search_more_about_uses_context(web):
    ctx = make_ctx()
    handle(ctx, "search the web for latest iphone")
    assert handle(ctx, "search more about its battery life") == "search more"
    assert web["searches"] == ["latest iphone", "latest iphone battery life"]
    assert "(Earlier question in this conversation: latest iphone)" in prompt_text(web["prompts"][1])


def test_search_more_about_a_new_topic(web):
    ctx = make_ctx()
    handle(ctx, "search the web for latest iphone")
    handle(ctx, "search more about samsung galaxy s27 ultra price in india")
    assert web["searches"][-1] == "samsung galaxy s27 ultra price in india"


# ---------------------------------------------------------------- opening Google on request
def test_open_in_browser_still_opens_google(web):
    ctx = make_ctx()
    assert handle(ctx, "open best laptops under 60000 in browser") == "google in browser"
    assert web["opened"] == ["https://www.google.com/search?q=best+laptops+under+60000"]
    handle(ctx, "search the web for latest iphone")
    handle(ctx, "show me in chrome")
    assert web["opened"][-1] == "https://www.google.com/search?q=latest+iphone"


def test_open_youtube_in_chrome_is_the_website(web, monkeypatch):
    from jarvis.skills import apps

    opened = []
    monkeypatch.setattr(apps, "open_target", lambda target, *a, **k: opened.append(target) or "ok", raising=False)
    assert handle(make_ctx(), "open youtube in chrome") != "google in browser"


# ---------------------------------------------------------------- speed: cache and time budget
def test_results_are_cached_for_30_minutes(web, monkeypatch):
    ctx = make_ctx()
    handle(ctx, "search the web for latest iphone")
    handle(ctx, "search the web for latest iphone")
    assert web["searches"] == ["latest iphone"]
    assert ctx.voice.said.count(browse.CHECKING) == 1  # no "let me check" for a cached answer
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 31 * 60)
    handle(ctx, "search the web for latest iphone")
    assert web["searches"] == ["latest iphone", "latest iphone"]


def test_slow_search_gives_up_within_the_budget(web, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_BUDGET", 0.3)
    monkeypatch.setattr(browse, "ddgs_text", lambda q: time.sleep(3) or HITS)
    ctx = make_ctx()
    started = time.monotonic()
    handle(ctx, "search the web for latest iphone")
    assert time.monotonic() - started < 1.5
    assert ctx.voice.last == browse.NOTHING_FOUND


def test_slow_pages_fall_back_to_snippets(web, monkeypatch):
    monkeypatch.setattr(browse, "PAGE_TIMEOUT", 0.2)

    def slow_download(url, timeout=4):
        if "goodreturns" in url:
            time.sleep(2)
        return web["pages"].get(url)

    monkeypatch.setattr(browse, "download", slow_download)
    started = time.monotonic()
    found = browse.search("petrol price in delhi today")
    assert time.monotonic() - started < 1.5
    goodreturns = next(s for s in found["sources"] if "goodreturns" in s["url"])
    assert goodreturns["text"] == "Rs 94.77 today"  # the search snippet, since the page was too slow
    assert found["sources"][0]["url"] == HITS[3]["href"]  # the page that did arrive comes first


def test_pages_cut_off_at_the_deadline_still_give_an_answer(web, monkeypatch):
    """The search arrives late; pages still loading at the deadline are dropped, but the snippets are kept."""
    monkeypatch.setattr(config, "SEARCH_BUDGET", 0.6)
    monkeypatch.setattr(browse, "ddgs_text", lambda q: time.sleep(0.4) or [dict(h) for h in HITS])
    monkeypatch.setattr(browse, "download", lambda url, timeout=4: time.sleep(2) or PETROL_PAGE)
    found = browse.search("petrol price in delhi today")
    assert found and found["sources"][0]["text"] == "Rs 94.77 today"


def test_within_times_out():
    with pytest.raises(TimeoutError):
        browse.within(lambda: time.sleep(1), 0.05)
    assert browse.within(lambda: 42, 1) == 42


# ---------------------------------------------------------------- routing: which questions go to the web
@pytest.fixture
def routing(web, monkeypatch):
    monkeypatch.setattr(config, "AI_ROUTING", True)
    return web


@pytest.mark.parametrize("question, query", [
    ("who won the match yesterday", "who won the cricket match yesterday"),
    ("petrol price in delhi today", "petrol price in delhi today"),
    ("latest iphone", "latest iphone model"),
])
def test_routing_sends_live_questions_to_the_web(routing, question, query):
    routing["route_reply"].append(tool_call("web_search", query=query))
    ctx = make_ctx()
    assert handle(ctx, question) == "web search"
    assert routing["searches"] == [query]
    body = routing["routes"][0]["payload"]
    assert "web_search" in {t["function"]["name"] for t in body["tools"]}
    assert "Use web_search for current events" in body["messages"][0]["content"]


def test_search_the_web_needs_no_routing(routing):
    ctx = make_ctx()
    assert handle(ctx, "search the web for the new delhi metro line") == "web search"
    assert routing["routes"] == []  # matched directly, no AI routing call


@pytest.mark.parametrize("phrase, skill_name", [("what is 2+2", "calculator"), ("tell me a joke", "joke")])
def test_maths_and_jokes_dont_browse(routing, phrase, skill_name):
    ctx = make_ctx()
    assert handle(ctx, phrase) == skill_name
    assert routing["routes"] == [] and routing["searches"] == []


def test_current_role_questions_skip_wikipedia(routing):
    routing["route_reply"].append(tool_call("web_search", query="current CEO of Twitter"))
    assert handle(make_ctx(), "who is the current ceo of twitter") == "web search"


# ---------------------------------------------------------------- ddgs engines asked in parallel
def test_ddgs_engines_race_and_first_real_answer_wins(monkeypatch):
    monkeypatch.setattr(browse, "DDGS_BACKENDS", ("dead", "slow", "good"))
    hit = [{"title": "t", "href": "https://x.com", "body": "b"}]

    def backend(query, name):
        if name == "dead":
            raise RuntimeError("No results found.")
        if name == "slow":
            time.sleep(5)
        return hit

    monkeypatch.setattr(browse, "ddgs_backend", backend)
    started = time.monotonic()
    assert REAL_DDGS_TEXT("q") == hit
    assert time.monotonic() - started < 1  # neither the failure nor the slow engine held it up


def test_ddgs_every_engine_failing_raises(monkeypatch):
    monkeypatch.setattr(browse, "DDGS_BACKENDS", ("a", "b"))
    monkeypatch.setattr(browse, "ddgs_backend", lambda q, n: (_ for _ in ()).throw(RuntimeError("No results found.")))
    with pytest.raises(RuntimeError):
        REAL_DDGS_TEXT("q")
