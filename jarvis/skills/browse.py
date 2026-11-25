"""Live web browsing: search, read the top pages, and ANSWER by voice from those pages only (no browser window).

Search providers, tried in order (SEARCH_PROVIDER, default "auto" = tavily, groq, ddgs):
- tavily: Tavily's search API, if TAVILY_API_KEY is set (an answer plus the top 5 results with their content);
- groq:   Groq's built-in web-search models (groq/compound-mini, groq/compound), if the key has access to one;
- ddgs:   free, no key: the top 5 results from several engines (DuckDuckGo's ddgs package), asked in parallel.
Quick answer first: the AI answers from the search results alone (Tavily's own answer, or the result
snippets), in 1-2 spoken sentences. Only when they don't answer it, or for "tell me more", are the top 3
pages fetched in parallel and their main text extracted with trafilatura (trimmed to about 1,500 words).
The AI must answer ONLY from the sources. The search has an 8 s budget (SEARCH_BUDGET); whatever has
arrived by then is used. Results are cached for 30 minutes.
Obvious web questions ("petrol price today", "who won the match yesterday") come here by regex
(skills/web_questions.py), without the AI routing call. `python main.py --timing` shows search, page
reading and AI time per turn.

Follow-ups use ctx.state["last_search"]: "tell me more", "which website said that", "open the first source",
"search more about X".
"""
import logging
import re
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlparse

import config
from jarvis import cache, http, persona, timing
from jarvis.router import on_startup, skill

log = logging.getLogger(__name__)

CHECKING = "Checking online."
NO_ANSWER = "I couldn't find a reliable answer."
NOTHING_FOUND = "I couldn't find anything online about that right now. Please try again in a bit."
NO_SEARCH_YET = "I haven't searched for anything yet."

RESULTS = 5          # links asked from the search engine
PAGES = 3            # pages actually read (ddgs)
PAGE_TIMEOUT = 4.0   # seconds per page (download + text extraction), in parallel
MAX_PAGE_BYTES = 2_000_000
MAX_WORDS = 1500     # all sources together, so the AI request stays inside Groq's free per-minute budget
SHORT_TOKENS = 220
DETAIL_TOKENS = 600
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 "
              "Safari/537.36 JarvisVoiceAssistant/1.0 (personal voice assistant; reads a page once per question)")
PAYWALLED = ("wsj.com", "ft.com", "nytimes.com", "economist.com", "bloomberg.com", "washingtonpost.com",
             "thetimes.co.uk", "barrons.com")
_PAYWALL_TEXT = re.compile(r"subscribe to (?:continue|read)|to continue reading|this (?:article|content) is for "
                           r"(?:subscribers|members)|already a subscriber|sign in to (?:continue|read)", re.IGNORECASE)
COMPOUND_MODELS = ("groq/compound-mini", "groq/compound")
# asked in parallel. Measured Nov 2026 (ddgs 9.16): yahoo answers in ~1.3 s when it answers, "auto" in 2-5 s;
# duckduckgo, brave and mojeek fail at once from here but cost nothing to ask (bing and google aren't offered).
DDGS_BACKENDS = ("yahoo", "auto", "duckduckgo", "brave", "mojeek")
GRACE = 1.0  # seconds past the budget for a provider to return what it already has
_STOP = {"the", "and", "for", "what", "who", "how", "is", "are", "was", "today", "latest", "with", "from", "about",
         "of", "in", "on", "at", "to", "a", "an", "me", "tell", "search", "web", "please", "kya", "hai", "aaj"}


# ---------------------------------------------------------------- time budget
def remaining(deadline):
    return max(0.0, deadline - time.monotonic())


def within(fn, seconds):
    """fn() in a daemon thread; TimeoutError after `seconds` (the thread then finishes in the background)."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            box["error"] = exc

    thread = threading.Thread(target=run, name="browse", daemon=True)
    thread.start()
    thread.join(max(0.0, seconds))
    if thread.is_alive():
        raise TimeoutError(f"no result within {seconds:.1f} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------------------------------------------------------------- reading pages
def site_name(url):
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def fetchable(url):
    if not url or not url.startswith(("http://", "https://")):
        return False
    path = urlparse(url).path.lower()
    site = site_name(url)
    return not path.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".zip")) and \
        not any(site == p or site.endswith("." + p) for p in PAYWALLED)


def download(url, timeout=PAGE_TIMEOUT):
    """The page's HTML (at most 2 MB), or None for errors, PDFs and other non-HTML."""
    response = http.session().get(url, timeout=timeout, stream=True, headers={"User-Agent": USER_AGENT})
    try:
        kind = response.headers.get("Content-Type", "").lower()
        if response.status_code != 200 or ("html" not in kind and "text/plain" not in kind):
            return None
        body = b""
        for block in response.iter_content(64 * 1024):
            body += block
            if len(body) >= MAX_PAGE_BYTES:
                break
        return body.decode(response.encoding or "utf-8", errors="replace")
    finally:
        response.close()


def extract_text(html):
    """The page's main text (no menus, ads or comments), via trafilatura."""
    import trafilatura

    return trafilatura.extract(html, include_comments=False, include_tables=True, favor_precision=True) or ""


def read_page(url):
    html = download(url)
    if not html:
        return None
    text = extract_text(html).strip()
    if len(text.split()) < 40 or _PAYWALL_TEXT.search(text[:3000]):
        return None  # an error page, a cookie wall or a paywall
    return text


def read_pages(urls, deadline):
    """{url: text} for the pages that were read in time (in parallel, PAGE_TIMEOUT each)."""
    texts, threads = {}, []
    for url in urls:
        def work(url=url):
            try:
                text = read_page(url)
                if text:
                    texts[url] = text
            except Exception as exc:
                log.info("[browse] couldn't read %s: %s", url, exc)

        thread = threading.Thread(target=work, name="browse-page", daemon=True)
        thread.start()
        threads.append(thread)
    stop = time.monotonic() + min(PAGE_TIMEOUT, remaining(deadline))
    for thread in threads:
        thread.join(max(0.0, stop - time.monotonic()))
    return dict(texts)


# ---------------------------------------------------------------- trimming to ~1,500 words
def _keywords(query):
    return {w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 2 and w not in _STOP}


def relevant_text(text, query, limit):
    """At most `limit` words of text, keeping the paragraphs that mention the query (and numbers) first."""
    if limit <= 0:
        return ""
    if len(text.split()) <= limit:
        return text.strip()
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    keys = _keywords(query)

    def score(index):
        lowered = paragraphs[index].lower()
        return -(sum(k in lowered for k in keys) + (0.5 if re.search(r"\d", lowered) else 0)), index

    chosen, count = set(), 0
    for index in sorted(range(len(paragraphs)), key=score):
        words = len(paragraphs[index].split())
        if count + words > limit:
            if not chosen:  # one huge paragraph: cut it
                return " ".join(paragraphs[index].split()[:limit])
            continue
        chosen.add(index)
        count += words
    return "\n".join(paragraphs[i] for i in sorted(chosen))


def trim_sources(sources, query, max_words=MAX_WORDS):
    """Share max_words between the sources: short ones keep everything, the rest is split among the long ones."""
    lengths = [len(s.get("text", "").split()) for s in sources]
    caps, left = [0] * len(sources), max_words
    pending = sorted(range(len(sources)), key=lambda i: lengths[i])
    while pending:
        index = pending.pop(0)
        caps[index] = min(lengths[index], left // (len(pending) + 1))
        left -= caps[index]
    trimmed = [{**s, "text": relevant_text(s.get("text", ""), query, caps[i])} for i, s in enumerate(sources)]
    return [s for s in trimmed if s["text"]]


# ---------------------------------------------------------------- providers
def tavily_search(query, timeout):
    from tavily import TavilyClient

    return TavilyClient(api_key=config.TAVILY_API_KEY).search(
        query, search_depth="basic", include_answer=True, max_results=RESULTS, timeout=timeout)


def search_tavily(query, deadline):
    if not config.TAVILY_API_KEY:
        return None
    data = tavily_search(query, max(1.0, remaining(deadline)))
    sources = [{"title": r.get("title") or site_name(r.get("url")), "url": r.get("url", ""), "text": r.get("content", "")}
               for r in data.get("results", []) if r.get("content")]
    return {"summary": data.get("answer") or "", "sources": sources}


def compound_model():
    """Groq's web-search model if this key can use it (checked once a day), else None."""
    if not config.AI_API_KEY or "api.groq.com" not in config.AI_BASE_URL:
        return None
    key = "browse|groq-compound|" + config.AI_BASE_URL
    cached = cache.get(key, 24 * 3600)
    if cached is not None:
        return cached or None
    data = http.get_json(f"{config.AI_BASE_URL}/models", headers={"Authorization": f"Bearer {config.AI_API_KEY}"},
                         timeout=4)
    offered = {m.get("id") for m in data.get("data", [])}
    model = next((m for m in COMPOUND_MODELS if m in offered), "")
    cache.put(key, model)
    if not model:
        log.info("[browse] Groq's web-search models aren't available on this key; skipping them")
    return model or None


def search_groq(query, deadline):
    model = compound_model()
    if not model:
        return None
    response = http.post_json(
        f"{config.AI_BASE_URL}/chat/completions",
        {"model": model, "max_tokens": 400, "messages": [{"role": "user", "content":
            f"Search the web and list the key facts, with dates, that answer: {query}"}]},
        headers={"Authorization": f"Bearer {config.AI_API_KEY}"}, timeout=max(1.0, remaining(deadline)))
    if response.status_code >= 400:
        log.info("[browse] Groq web search HTTP %s", response.status_code)
        return None
    message = response.json()["choices"][0]["message"]
    sources = []
    for tool in message.get("executed_tools") or []:
        for result in ((tool.get("search_results") or {}).get("results") or []):
            if result.get("content"):
                sources.append({"title": result.get("title") or site_name(result.get("url")),
                                "url": result.get("url", ""), "text": result["content"]})
    summary = (message.get("content") or "").strip()
    if not sources and summary:
        sources = [{"title": "Groq web search", "url": "", "text": summary}]
    return {"summary": summary if len(sources) > 1 else "", "sources": sources[:RESULTS]}


def ddgs_backend(query, backend):
    from ddgs import DDGS

    return DDGS(timeout=4).text(query, max_results=RESULTS, region="in-en", backend=backend) or []


def ddgs_text(query):
    """[{title, href, body}] via the ddgs package (free, no key). Its engines are flaky: each one answers
    "No results found." for some queries (measured Nov 2026, every engine failed on at least one of three
    everyday questions). Trying them in turn used up the whole budget, so they are asked in parallel and
    the first non-empty answer wins; the rest finish in the background."""
    import queue

    answers = queue.Queue()

    def ask(backend):
        try:
            answers.put((backend, ddgs_backend(query, backend), None))
        except Exception as exc:  # noqa: BLE001 - "No results found." happens now and then
            answers.put((backend, [], exc))

    for backend in DDGS_BACKENDS:
        threading.Thread(target=ask, args=(backend,), name=f"ddgs-{backend}", daemon=True).start()
    last = None
    for _ in DDGS_BACKENDS:
        backend, results, error = answers.get()  # the caller's within() bounds the wait
        if results:
            return results
        last = error or last
    if last:
        raise last
    return []


def search_ddgs(query, deadline):
    """The result snippets only; the pages are read later if they're needed (see deepen)."""
    hits = within(lambda: ddgs_text(query), remaining(deadline))
    hits = [h for h in hits or [] if h.get("href")][:RESULTS]
    return {"summary": "", "sources": [{"title": h.get("title") or site_name(h["href"]), "url": h["href"],
                                        "text": h.get("body") or ""} for h in hits if h.get("body")]}


PROVIDERS = {"tavily": search_tavily, "groq": search_groq, "ddgs": search_ddgs}


def provider_order():
    raw = (config.SEARCH_PROVIDER or "auto").lower()
    names = ["tavily", "groq", "ddgs"] if raw == "auto" else [n.strip() for n in raw.split(",")]
    return [n for n in names if n in PROVIDERS]


def search(query, budget=None):
    """{'query', 'provider', 'summary', 'sources': [{title, url, text}], 'at'} or None if nothing was found."""
    query = re.sub(r"\s+", " ", query or "").strip()
    key = "browse|" + query.lower()
    cached = cache.get(key, config.SEARCH_CACHE_MINUTES * 60)
    if cached:
        return cached
    started_search = time.perf_counter()
    try:
        return _search(query, key, budget)
    finally:
        timing.add("search", time.perf_counter() - started_search)


def _search(query, key, budget):
    deadline = time.monotonic() + (budget or config.SEARCH_BUDGET)
    for name in provider_order():
        if remaining(deadline) < 0.3:
            log.info("[browse] out of time before trying %s", name)
            break
        started = time.monotonic()
        try:
            # providers stop at the deadline themselves (pages not read by then -> snippets); the grace second
            # lets them hand back what they have instead of losing it all to this outer timeout
            found = within(lambda: PROVIDERS[name](query, deadline), remaining(deadline) + GRACE)
        except Exception as exc:
            log.info("[browse] %s search failed: %s", name, exc)
            continue
        if not found or not found.get("sources"):
            continue
        found = {"query": query, "provider": name, "summary": found.get("summary", ""),
                 "sources": trim_sources(found["sources"], query), "at": datetime.now().strftime("%d %B %Y, %I:%M %p")}
        log.info("[browse] %s: %d sources in %.1f s", name, len(found["sources"]), time.monotonic() - started)
        cache.put(key, found)
        return found
    return None


def deepen(found, budget=None):
    """The same search with the top pages read in full (for "tell me more", or when the snippets didn't answer).
    Pages that can't be read in time keep their snippet."""
    if found.get("deep"):
        return found
    started = time.perf_counter()
    deadline = time.monotonic() + (budget or config.SEARCH_BUDGET)
    urls = [s["url"] for s in found["sources"] if fetchable(s.get("url"))][:PAGES]
    pages = read_pages(urls, deadline) if urls else {}
    timing.add("fetch", time.perf_counter() - started)
    sources = [{**s, "text": pages.get(s.get("url")) or s["text"]} for s in found["sources"]]
    sources.sort(key=lambda s: s.get("url") not in pages)  # pages actually read first
    deep = {**found, "sources": trim_sources(sources, found["query"]), "deep": True}
    log.info("[browse] read %d of %d pages in %.1f s", len(pages), len(urls), time.perf_counter() - started)
    cache.put("browse|" + found["query"].lower(), deep)
    return deep


# ---------------------------------------------------------------- the search query
_YESTERDAY = re.compile(r"\b(?:yesterday(?:'s)?|last night(?:'s)?)\b")
_TODAY = re.compile(r"\b(?:today(?:'s)?|tonight(?:'s)?)\b")
_WHO_WON = re.compile(r"^who won (?:the |that |last )?(.+?)$")
# Hinglish words -> English search words ("sone ka bhav aaj kya hai" -> "gold rate today"): English pages
# carry the numbers in their snippets far more often. None = drop the word.
_HINGLISH_WORDS = {
    "sona": "gold", "sone": "gold", "chandi": "silver", "chaandi": "silver", "bhav": "rate", "bhaav": "rate",
    "daam": "price", "keemat": "price", "kimat": "price", "aaj": "today", "abhi": "now", "jeeta": "won",
    "jeeti": "won", "kaun": "who", "kisne": "who", "khabar": "news", "khabrein": "news",
    "kya": None, "hai": None, "hain": None, "ka": None, "ki": None, "ke": None, "ko": None, "kitna": None,
    "kitni": None, "kitne": None, "batao": None, "bataiye": None, "bata": None, "mein": None, "me": None,
    "tha": None, "thi": None, "raha": None, "rahi": None, "chal": None,
}


def _in_english(text):
    words = text.split()
    if len(_HINGLISH.findall(text)) < 2:
        return text
    english = [_HINGLISH_WORDS.get(w, w) for w in words]
    english = [w for w in english if w]
    if "won" in english and "who" in english:  # "kal ka match kaun jeeta" -> "who won match yesterday"
        english = ["who", "won"] + [w for w in english if w not in ("who", "won")]
        english = ["yesterday" if w == "kal" else w for w in english]
    return " ".join(english) or text


def search_query(text, now=None):
    """What to type into the search engine: Hinglish in English words, relative days as dates ("who won the
    cricket match yesterday" -> "cricket match result 24 November 2026"), so the results are about the right day.
    """
    now = now or datetime.now()
    query = _in_english(re.sub(r"\s+", " ", (text or "").strip(" ?.!")).strip())
    text = query
    won = _WHO_WON.match(query)
    if won:
        query = won.group(1) + " result"
    query = _YESTERDAY.sub("", query)  # put the date at the end, where it reads naturally
    day = None
    if _YESTERDAY.search(text or ""):
        day = now - timedelta(days=1)
    elif _TODAY.search(query):
        query, day = _TODAY.sub("", query), now
    if day:
        query = f"{query} {day.day} {day:%B %Y}"
    return re.sub(r"\s+", " ", query).strip()


# ---------------------------------------------------------------- answering from the sources
_HINGLISH = re.compile(r"\b(?:kya|kaun|kaunsa|kaunsi|kitn[aie]|kaise|kab|kahan|kyun|hai|hain|tha|thi|batao|bataiye|"
                       r"bata|dekho|aaj|kal|abhi|mein|ka|ki|ke|ko|wala|wali|jeeta|jeeti|kiska|kiski)\b")


def wants_hinglish(ctx, text):
    from jarvis import translit
    from jarvis.skills.briefing import in_hindi_mode

    return translit.has_devanagari(text) or in_hindi_mode(ctx) or len(_HINGLISH.findall(text or "")) >= 2


def answer_prompt(question, found, detail=False, hinglish=False, earlier=None):
    quick = not detail and not found.get("deep")
    length = ("5 to 7 short sentences with the useful details" if detail
              else "1 or 2 short sentences" if quick else "2 or 3 short sentences")
    language = ("Reply in Hinglish (Hindi written ONLY in Roman letters, never Devanagari), except that sentence, "
                "which stays in English. " if hinglish else "Reply in English. ")
    what = ("search results (titles and short snippets)" if quick else "web sources")
    system = (
        f"You are {config.ASSISTANT_NAME}, a voice assistant. Today is {datetime.now():%A %d %B %Y}. "
        f"Answer the question using ONLY the numbered {what} below (found {found['at']}), never your own "
        "knowledge. Your reply is spoken: " + length + ", plain speech, no markdown, no URLs, no [1] numbers. "
        "Start with the answer itself. " + persona.ai_style() +
        "For time-sensitive facts (prices, scores, results, news, who holds a job now) say the date they apply to. "
        "If the question leaves out a detail (which match, which city, which model), answer for the most relevant "
        "or most recent one in the sources and name it, for example: India vs Australia on 24 November: India won "
        "by 5 wickets. "
        f"Only if the {what} don't contain the answer at all, or contradict each other, reply exactly: {NO_ANSWER} "
        + language
    )
    parts = [f"Question: {question}"]
    if earlier:
        parts.append(f"(Earlier question in this conversation: {earlier})")
    if found.get("summary"):
        parts.append(("The search engine's own answer (rephrase it for speaking, checked against the results): "
                      if quick else "Search engine summary (check it against the sources): ") + found["summary"])
    parts.append("Search results:" if quick else "Sources:")
    for number, source in enumerate(found["sources"], 1):
        where = site_name(source["url"]) or "web search"
        parts.append(f"[{number}] {source['title']} ({where})\n{source['text']}")
    return system, "\n\n".join(parts)


def source_list(found):
    return "\n".join(f"  [{n}] {s['title']} - {s['url'] or site_name(s['url'])}"
                     for n, s in enumerate(found["sources"], 1))


def _no_answer(sentence):
    return "find a reliable answer" in sentence.lower().replace("’", "'")


class _NeedPages(Exception):
    """The search results alone didn't answer it: read the pages and ask again."""


def _stream_answer(ctx, question, found, detail, earlier, pages_can_help):
    """Speak the AI's answer sentence by sentence. Returns (spoken sentences, error to say or None)."""
    from jarvis.skills import ai

    system, prompt = answer_prompt(question, found, detail, wants_hinglish(ctx, question), earlier)
    spoken = []
    started = time.perf_counter()
    try:
        status, chunks = ai.stream(system, [], prompt, DETAIL_TOKENS if detail else SHORT_TOKENS)
        if status >= 400:
            log.warning("[browse] AI HTTP %s: %s", status, (chunks or "")[:200])
            return spoken, ai.error_message(status, chunks)
        ai.announce_switch(ctx)
        for sentence in ai.sentences(chunks):
            text = ai.strip_markdown(sentence)
            if not text:
                continue
            if not spoken:
                timing.add("ai", time.perf_counter() - started)
                if pages_can_help and _no_answer(text):
                    close = getattr(chunks, "close", None)
                    if close:
                        close()
                    raise _NeedPages()
                timing.mark("answer")
            if getattr(ctx.voice, "_hushed", False):
                break  # barge-in
            ctx.say(text, wait=False)
            spoken.append(text)
    except ai.BothFailed:
        return spoken, ai.BOTH_DOWN
    except _NeedPages:
        raise
    except Exception as exc:
        if not spoken:
            raise
        log.warning("[browse] answer stream broke off: %s", exc)
    return spoken, None


def speak_answer(ctx, question, found, detail=False, earlier=None):
    """Stream the AI's answer, sentence by sentence: from the search results first, and from the pages themselves
    if those didn't answer it (or for "tell me more"). Returns what's left for the router to say."""
    if detail:
        found = deepen(found)
    ctx.state["last_search"] = {"question": question, "found": found}
    if not config.AI_API_KEY:
        first = found.get("summary") or found["sources"][0]["text"]
        return "Here's what I found online. " + " ".join(re.split(r"(?<=[.!?])\s+", first.strip())[:2])
    try:
        spoken, error = _stream_answer(ctx, question, found, detail, earlier, pages_can_help=not found.get("deep"))
    except _NeedPages:
        log.info("[browse] the search results didn't answer it; reading the pages")
        found = deepen(found)
        ctx.state["last_search"]["found"] = found
        spoken, error = _stream_answer(ctx, question, found, detail, earlier, pages_can_help=False)
    if error:
        return error
    wait = getattr(ctx.voice, "wait_idle", None)
    if wait:
        wait()
    answer = " ".join(spoken) or NO_ANSWER
    ctx.state["last_search"]["answer"] = answer
    ctx.state["last_said"] = answer
    log.info("\n%s\nSources (%s%s):\n%s", answer, found["provider"], ", pages read" if found.get("deep") else "",
             source_list(found))
    return "" if spoken else NO_ANSWER


def look_up(ctx, query, question=None, earlier=None):
    question = question or query
    cached = cache.get("browse|" + re.sub(r"\s+", " ", query).strip().lower(), config.SEARCH_CACHE_MINUTES * 60)
    if not cached:
        ctx.say(persona.line("checking_online"), wait=False)  # spoken in the background while the search runs
    found = search(query)
    if not found:
        return persona.line("nothing_found")
    return speak_answer(ctx, question, found, earlier=earlier)


# ---------------------------------------------------------------- follow-ups
BROWSE_SKILLS = {"web search", "search more", "tell me more", "search sources", "open source"}
_ORDINALS = {"first": 0, "1st": 0, "top": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2,
             "three": 2, "fourth": 3, "4th": 3, "fifth": 4, "5th": 4}


def last_search(ctx, recent_only=True):
    """The last search, if there is one (and, with recent_only, if the last turn was about it)."""
    last = ctx.state.get("last_search")
    if not last:
        return None
    turns = ctx.state.get("turns") or []
    if recent_only and (not turns or turns[-1]["skill"] not in BROWSE_SKILLS):
        return None
    return last


@skill("tell me more", r"^(?:aur|and|phir|aur kuch|what else|anything else)\??$",
       r"^(?:please\s+)?(?:tell me more|more details?|more info(?:rmation)?|explain more|go on|"
       r"elaborate|tell me more about (?:it|that|this)|aur batao|aur bataiye|thoda aur batao|detail (?:mein|me) batao)$",
       category="web", help=["tell me more (after a web search)"])
def tell_me_more(ctx, command, match):
    last = last_search(ctx)
    if not last:
        return None  # not after a search: the AI chat handles it
    return speak_answer(ctx, last["question"], last["found"], detail=True)


@skill("search sources", r"\b(?:which|what) (?:website|site|source|page|link)s?\b",
       r"^(?:sources?|what(?:'s| is| are) the sources?|source kya (?:hai|tha)|kahan se pata chala)\??$",
       r"\bwhere did you (?:read|find|get|see) (?:that|this|it)\b", category="web",
       help=["which website said that"])
def sources(ctx, command, match):
    last = last_search(ctx, recent_only=False)
    if not last:
        return NO_SEARCH_YET
    found = last["found"]
    names = []
    for source in found["sources"]:
        name = site_name(source["url"]) or source["title"]
        if name not in names:
            names.append(name)
    log.info("Sources:\n%s", source_list(found))
    from jarvis.router import join_and

    return f"That came from {join_and(names[:3])}." + (" The full list is in the terminal." if len(names) > 3 else "")


_OPEN = (r"^(?:please\s+)?(?:open|show(?: me)?)\s+(?:the\s+)?(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|"
         r"top|that|this|it)(?:\s+(?:one|source|link|result|website|site|page|article))?"
         r"(?:\s+(?:in|on)\s+(?:the\s+)?(?:browser|chrome|google chrome|edge))?$")


@skill("open source", _OPEN, r"^(?:open|show)\s+(?:the\s+)?(source|link|article)(?:\s+(?:in|on)\s+(?:the\s+)?"
       r"(?:browser|chrome|edge))?$", category="web", help=["open the first source"])
def open_source(ctx, command, match):
    which = match.group(1)
    explicit = which in _ORDINALS
    last = last_search(ctx, recent_only=not explicit)
    if not last:
        return None  # "open it" with no search behind it: the normal "open" skill handles it
    found = [s for s in last["found"]["sources"] if s.get("url")]
    index = _ORDINALS.get(which, 0)
    if index >= len(found):
        return f"I only have {len(found)} sources." if found else "That answer didn't come with a link."
    webbrowser.open(found[index]["url"])
    return f"Opening {site_name(found[index]['url'])}."


_PRONOUN = re.compile(r"\b(?:it|its|that|this|they|their|them|he|his|she|her)\b")


@skill("search more",r"^(?:please\s+)?(?:search|look up|find|google|check)\s+(?:some\s+)?more\s+(?:about|on|for)\s+(.+)$",
       r"^(.+?)\s+(?:ke|ka|ki) baare mein aur (?:search karo|dekho|batao)$", category="web",
       help=["search more about its battery life"])
def search_more(ctx, command, match):
    topic = match.group(1).strip(" ?.")
    if _ELSEWHERE.search(topic):
        return None
    last = ctx.state.get("last_search")
    query = topic
    if last and (len(topic.split()) <= 3 or _PRONOUN.search(topic)):
        query = last["found"]["query"] + " " + _PRONOUN.sub("", topic)  # "its battery" -> "latest iphone battery"
    return look_up(ctx, re.sub(r"\s+", " ", query).strip(), question=topic,
                   earlier=last["question"] if last else None)


# ---------------------------------------------------------------- opening Google in the browser (on request)
@skill("google in browser",
       r"^(?:please\s+)?show (?:me |it |that )?(?:in|on) (?:the\s+)?(browser|chrome)$",
       r"^(?:please\s+)?(?:open|show(?: me)?|search)\s+(.+?)\s+(?:in|on)\s+(?:the\s+)?(?:browser|chrome|google chrome|edge)$",
       r"^(?:please\s+)?show me (.+?) on google$",
       category="web", help=["open best laptops under 60000 in browser"])
def google_in_browser(ctx, command, match):
    from jarvis.skills import apps

    query = match.group(1).strip()
    if query in ("browser", "chrome"):  # "show me in chrome": the last search, on Google
        last = ctx.state.get("last_search")
        if not last:
            return None
        query = last["found"]["query"]
    elif query in apps.WEBSITES or query in apps.APPS:
        return None  # "open youtube in chrome" is the website itself
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(query)}")
    return f"Showing Google results for {query}."


# ---------------------------------------------------------------- searching
_SEARCH = (r"^(?!.*\b(?:on|in)\s+(?:youtube|spotify)$)(?:please\s+)?(?:search the web for|search the internet for|search online for|search google for|"
           r"search for|web search for|web search|check online for|check online|look up|lookup|find out|browse for|"
           r"google|search)\s+"
           r"(?:for\s+|about\s+)?(.+?)(?:\s+(?:on google|online|on the web|on the internet))?\??$")


_ELSEWHERE = re.compile(r"\b(?:on|in)\s+(?:youtube|spotify)$|^(?:my\s+)?(?:e-?mails?|mail|inbox|gmail)\b")


@skill("web search", _SEARCH,
       r"^(.+?)\s+(?:internet|net|online|google|web)\s+(?:pe|par|par se|pe se)\s+(?:dekho|dekh ke batao|search karo|"
       r"check karo|dhundo)$",
       r"^(?:internet|net|online|google|web)\s+(?:pe|par)\s+(?:dekho|search karo|check karo|dhundo)\s+(.+)$",
       category="web", help=["search the web for the latest iPhone", "look up petrol price in Delhi today",
                             "who won the match yesterday"],
       tool="web_search", params={"query": "a short search query in English, e.g. sensex today (no dates the user "
                                           "didn't say)"},
       command=lambda a: f"search the web for {a['query']}")
def web_search(ctx, command, match):
    query = match.group(1).strip(" ?.")
    if _ELSEWHERE.search(query):
        return None  # "search lofi on youtube", "search my email for the invoice": their own skills
    if not query:
        return "What should I search for?"
    heard = ctx.state.get("heard") or command
    return look_up(ctx, query, question=heard if heard != command else query)


@on_startup
def _warm_up(ctx):
    """Import trafilatura (about 1.5 s here) in the background, so the first search doesn't pay for it."""
    def load():
        try:
            import trafilatura  # noqa: F401
        except ImportError:
            log.info("[browse] trafilatura isn't installed: pip install -r requirements.txt")

    threading.Thread(target=load, name="browse-warm-up", daemon=True).start()
