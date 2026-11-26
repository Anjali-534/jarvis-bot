"""The fast path to a web answer: obvious "look it up" questions skip the AI routing call.

"petrol price in mumbai today", "who won the match yesterday", "latest iphone", "sone ka bhav aaj kya hai" are
matched by regex and searched straight away (skills/browse.py). This module is registered after weather, AQI
and news, so "weather today" and "aqi today" still reach those skills, and before Wikipedia and the AI chat,
so "who is the current prime minister" is searched instead of answered from old knowledge. Anything about
the user themselves ("my", "I", "you") is left to the AI. "news about X" already skips the AI too: the
headlines skill (skills/web.py) answers it.
"""
import re

from jarvis.router import skill
from jarvis.skills import browse

_LIVE = (
    r"latest|newest|today|today'?s|tonight|yesterday|yesterday'?s|last night|right now|currently|current|"
    r"this week|aaj|abhi|"
    r"prices?|rates?|bhaa?v|daam|stock|share price|sensex|nifty|"
    r"scores?|scorecard|who won|kaun jeeta|kisne jeeta|(?:match|election|exam) results?"
)
_FILLER = re.compile(r"^(?:(?:hey |ok |okay )?jarvis,? |please |(?:can|could|will) you (?:please )?"
                     r"(?:tell me|check|find out|look up) |tell me |do you know |i want to know |mujhe batao )+")
_PERSONAL = re.compile(r"\b(?:my|mine|me|i|i'm|i've|we|our|us|you|your|yours|mera|meri|mere|mujhe|hamara|"
                       r"tum|tumhara|aap|aapka)\b")
_ASIDE = re.compile(r"\b(?:remind|reminder|alarm|timer|note|todo|to-do|schedule|calendar|meeting|weather|"
                    r"temperature|forecast|aqi|pollution|email|mail|whatsapp|play|song|open|volume|brightness)\b")


@skill("web question", rf"\b(?:{_LIVE})\b", category="web",
       help=["petrol price in Mumbai today", "who won the cricket match yesterday", "latest iPhone"])
def web_question(ctx, command, match):
    question = _FILLER.sub("", command).strip(" ?.!")
    if not question or _PERSONAL.search(question) or _ASIDE.search(question) or len(question.split()) < 2:
        return None  # "how are you today", "what's my day like": the AI decides
    heard = ctx.state.get("heard") or command
    return browse.look_up(ctx, browse.search_query(question), question=heard)
