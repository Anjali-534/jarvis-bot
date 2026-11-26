"""Verified facts: "who wrote Godaan", "capital of Australia", "when was the Taj Mahal built", "how many states
does India have" are checked with a quick web search and answered only from what the search found.

AI models sound sure even when they're wrong about these, so they never answer them from memory. If the search
doesn't confirm an answer, Jarvis says it isn't sure ("Pakka nahi hoon yaar...") and offers a Google search,
instead of guessing. Registered before web_questions, Wikipedia and the AI.
"""
import re

from jarvis import persona
from jarvis.router import skill

_VERBS = (r"wrote|written|founded|invented|discovered|directed|composed|painted|created|built|designed|developed|"
          r"started|established|sang|made|produced|authored|coined|first climbed|won the first")
_HI_VERBS = (r"likha|likhi|likhe|banaya|banayi|banaye|bnaya|khoja|khoji|invent kiya|discover kiya|shuru kiya|"
             r"start kiya|found kiya|founded kiya|gaaya|gaayi|direct kiya|paint kiya|chalu kiya|sthapit kiya")
_HI_WHEN = (r"bana|bani|bane|banaya gaya|bana tha|hua|hui|hue|likha gaya|likhi gayi|shuru hua|shuru hui|aazad hua|"
            r"azaad hua|azad hua|paida hua|paida hui|mila|aaya|aayi|khula|khuli|release hua|release hui|launch hua")
_NOT_MINE = (r"^(?!.*\b(?:my|mine|me|i|i'm|i've|we|our|us|you|your|yours|mera|meri|mere|mujhe|hamara|tum|"
             r"tumhara|aap|aapka)\b)")  # "how many emails do I have" isn't a world fact
PATTERNS = tuple(_NOT_MINE + p.lstrip("^") for p in (
    rf"^(?:who|which person|which company|which author)\s+(?:{_VERBS})\s+(.+)$",
    rf"^(?:who\s+(?:is|was)\s+the\s+)(?:author|writer|founder|inventor|creator|director|composer|painter)\s+of\s+(.+)$",
    r"^(?:what(?:'s| is| was)\s+)?the\s+capital(?:\s+city)?\s+of\s+(.+)$",
    r"^capital(?:\s+city)?\s+of\s+(.+)$",
    r"^when\s+(?:was|were|did|is|was the)\s+(.+)$",
    r"^(?:in\s+)?(?:what|which)\s+year\s+(?:was|were|did)\s+(.+)$",
    r"^how\s+many\s+(.+)$",
    rf"^(.+?)\s+(?:kisne|kis ne|kaun ne)\s+(?:{_HI_VERBS})(?:\s+(?:tha|thi|the|hai))?$",
    r"^(.+?)\s+(?:ki|ka|ke)\s+(?:rajdhani|raajdhani|capital)\s*(?:kya|kaun si|kaunsi|kaun sa|kaunsa)?\s*(?:hai)?$",
    rf"^(.+?)\s+kab\s+(?:{_HI_WHEN})(?:\s+(?:tha|thi|the|hai))?$",
    r"^(.+?)\s+(?:mein|me|ke paas)\s+(?:kitne|kitni|kitna)\s+(.+?)\s+(?:hai|hain|the|thi|tha)$",
))
# Questions about the user or the laptop aren't world facts ("how many emails do I have", "when is my meeting")
_PERSONAL = re.compile(r"\b(?:my|mine|me|i|i'm|i've|we|our|us|you|your|yours|mera|meri|mere|mujhe|hamara|"
                       r"tum|tumhara|aap|aapka)\b")
_ASIDE = re.compile(r"\b(?:remind|reminder|alarm|timer|note|notes|todo|schedule|calendar|meeting|weather|temperature|"
                    r"forecast|aqi|pollution|email|emails|mail|whatsapp|song|songs|volume|brightness|battery|tabs?|"
                    r"windows? open|apps? open|files?|downloads?|routines?|minutes? left|days? left|days? until)\b")
_FILLER = re.compile(r"^(?:(?:hey |ok |okay )?jarvis,? |please |(?:can|could|will) you (?:please )?"
                     r"(?:tell me|check|find out) |tell me |do you know |i want to know |mujhe batao |yaar |bata )+")


def clean(command):
    return _FILLER.sub("", command).strip(" ?.!")


# Hinglish fact questions -> the English search that finds them ("godaan kisne likha" -> "who wrote godaan")
_HI_WHO = {"likha": "wrote", "likhi": "wrote", "likhe": "wrote", "banaya": "made", "banayi": "made", "banaye": "made",
           "bnaya": "made", "khoja": "discovered", "khoji": "discovered", "gaaya": "sang", "gaayi": "sang"}
_HI_WHEN = {"bana": "built", "bani": "built", "bane": "built", "hua": "", "hui": "", "aazad": "independent",
            "azaad": "independent", "azad": "independent", "paida": "born", "likha": "written", "likhi": "written",
            "khula": "opened", "khuli": "opened", "release": "released", "launch": "launched", "shuru": "started"}


def english_query(question):
    q = question.lower().strip()
    found = re.match(r"^(.+?)\s+(?:kisne|kis ne|kaun ne)\s+(\w+)", q)
    if found:
        verb = _HI_WHO.get(found.group(2), "made" if found.group(2) in ("invent", "start", "found") else found.group(2))
        return f"who {verb} {found.group(1)}"
    found = re.match(r"^(.+?)\s+(?:ki|ka|ke)\s+(?:rajdhani|raajdhani|capital)\b", q)
    if found:
        return f"capital of {found.group(1)}"
    found = re.match(r"^(.+?)\s+kab\s+(\w+)", q)
    if found:
        return f"when was {found.group(1)} {_HI_WHEN.get(found.group(2), found.group(2))}".strip()
    found = re.match(r"^(.+?)\s+(?:mein|me|ke paas)\s+(?:kitne|kitni|kitna)\s+(.+?)\s+(?:hai|hain|the|thi|tha)$", q)
    if found:
        return f"how many {found.group(2)} in {found.group(1)}"
    return question


def not_sure(ctx, question):
    """Never guess: say so, and offer a Google search ("haan" opens it)."""
    from jarvis import intent

    offer = intent.safe_alternative(f"show me {question} on google")
    if offer:
        ctx.state["offer"] = offer
    return persona.line("not_sure")


def verified_answer(ctx, question, heard=None):
    """Search first, then answer only from the results. 'Not sure' if they don't settle it."""
    from jarvis import cache
    from jarvis.skills import browse

    import config

    query = browse.search_query(english_query(question))
    if not cache.get("browse|" + re.sub(r"\s+", " ", query).strip().lower(), config.SEARCH_CACHE_MINUTES * 60):
        ctx.say(persona.line("checking_online"), wait=False)
    found = browse.search(query)
    if not found:
        return not_sure(ctx, question)
    result = browse.speak_answer(ctx, heard or question, found, hold_no_answer=True)
    if result == browse.NO_ANSWER:
        return not_sure(ctx, question)
    return result


@skill("fact check", *PATTERNS, category="web",
       help=["who wrote Godaan", "what is the capital of Australia", "when was the Taj Mahal built",
             "godaan kisne likha"])
def fact_check(ctx, command, match):
    question = clean(command)
    if not question or _PERSONAL.search(question) or _ASIDE.search(question) or len(question.split()) < 3:
        return None  # "how many emails do I have", "when is my meeting": other skills or the AI
    return verified_answer(ctx, question, heard=ctx.state.get("heard") or command)
