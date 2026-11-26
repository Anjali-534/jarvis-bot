"""Long-term memory, like a friend: "remember that Dhruv's birthday is 12 Feb", "yaad rakhna ki meri bike ki
service March mein hai", "Dhruv ka birthday kab hai?", "what do you know about me?", "bhool ja ki...".

Memories live in data/memory.json (git-ignored): id, text, category (personal / people / dates / preferences /
work / other), created, and remind_on when the memory has a date. A dated memory gets a spoken reminder and a
toast at 9 am on that day; birthdays and anniversaries every year, with one more the day before.

Privacy:
* passwords, OTPs, PINs, card and bank numbers, Aadhaar and PAN are never saved
* health and sad personal memories are marked sensitive: Jarvis never brings them up (not in "what do you know
  about me", not as chat context) unless you bring the topic up first
* "export my memories" writes a readable copy to Documents; nothing goes into git
The 5 most relevant memories are given to the AI chat for each question (only when they're relevant).
"""
import difflib
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from jarvis import actions, grammar, notify, persona
from jarvis.router import on_startup, skill, speak_list

log = logging.getLogger(__name__)

CATEGORIES = ("personal", "people", "dates", "preferences", "work", "other")
MATCH = 0.5          # recall: at least half of the question's words must match
CONTEXT_MATCH = 0.3   # chat context: looser, it's only a hint
CONTEXT_COUNT = 5
AI_PICK_ABOVE = 2    # more candidates than this: the AI picks (and phrases) the answer

_lock = threading.Lock()

# ---------------------------------------------------------------- what may never be saved
_SECRET_WORDS = re.compile(
    r"\b(?:passwords?|passcode|pass code|pin(?: number| code)?|otp|one time password|cvv|cvc|card numbers?|"
    r"credit card|debit card|atm card|account numbers?|a/c (?:no|number)|bank account|ifsc|net ?banking|upi pin|"
    r"aadhaa?r|aadhar|adhaar|pan (?:card|number|no)|pan\b(?= is| hai| ka| number)|login|security code|"
    r"secret key|api key|private key|seed phrase|recovery phrase)\b", re.IGNORECASE)
_SECRET_SHAPES = [
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE),           # PAN
    re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),                         # Aadhaar (12 digits)
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                            # card / account numbers
]


def is_secret(text):
    text = text or ""
    return bool(_SECRET_WORDS.search(text)) or any(p.search(text) for p in _SECRET_SHAPES)


# ---------------------------------------------------------------- what kind of memory it is
_HEALTH_SAD = re.compile(
    r"\b(?:doctor|dr|hospital|clinic|medicine|medicines|dawai|dawa|tablet|tablets|surgery|operation|therapy|therapist|"
    r"anxiety|depression|depressed|diabetes|bp|blood pressure|thyroid|asthma|allergy|allergic|cancer|illness|ill|"
    r"sick|bimaar|beemar|bimari|fever|injury|fracture|pregnant|pregnancy|periods?|mental health|panic attacks?|"
    r"died|death|passed away|guzar gay\w*|nahi rahe|funeral|breakup|break up|broke up|divorce|divorced|"
    r"fired|laid off|lost my job|naukri chali|sad|udaas|udas|lonely|akela|akeli|rona|cried|miss (?:her|him))\b",
    re.IGNORECASE)
_DATE_WORDS = re.compile(r"\b(?:birthday|bday|b'day|janamdin|janmdin|anniversary|saalgirah|salgirah)\b", re.IGNORECASE)
_PREFERENCE = re.compile(r"\b(?:like|likes|love|loves|hate|hates|prefer|prefers|favou?rite|fav|pasand|pasand hai|"
                         r"nahi pasand|enjoy|allergic to|can't stand|don't like|dont like|vegetarian|vegan)\b",
                         re.IGNORECASE)
_WORK = re.compile(r"\b(?:work|office|project|client|boss|manager|team|deadline|meeting|job|naukri|salary|"
                   r"company|startup|interview|exam|college|class|assignment|standup|sprint|bogie)\b", re.IGNORECASE)
_PEOPLE = re.compile(r"\b(?:mom|mummy|mumma|maa|dad|papa|bhai|bhaiya|didi|sister|brother|friend|dost|wife|husband|"
                     r"boyfriend|girlfriend|bf|gf|uncle|aunty|aunt|cousin|nani|nana|dadi|dada|colleague|"
                     r"[a-z]+'s)\b", re.IGNORECASE)
_NAMED = re.compile(r"(?<![.!?] )\b(?!I\b|My\b|Meri\b|Mera\b|Mere\b)[A-Z][a-z]+\s+(?:ka|ki|ke)\b")
_PERSONAL = re.compile(r"\b(?:i|i'm|i am|my|me|mine|main|mera|meri|mere|mujhe)\b", re.IGNORECASE)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MONTH_ONLY = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|march|mar|april|apr|may|june?|july?|aug(?:ust)?|"
                         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", re.IGNORECASE)


def categorize(text):
    if _DATE_WORDS.search(text):
        return "dates"
    if _PREFERENCE.search(text):
        return "preferences"
    if _WORK.search(text):
        return "work"
    if _NAMED.search(text):
        return "people"  # "Dhruv ka number...", "Riya ki shaadi..."
    if _PEOPLE.search(text) and not re.match(r"^(?:i|my|meri|mera|mere|main)\b", text, re.IGNORECASE):
        return "people"
    if _date_in(text, date.today()):
        return "dates"
    if _PERSONAL.search(text):
        return "personal"
    return "other"


def is_sensitive(text):
    return bool(_HEALTH_SAD.search(text or ""))


def _date_in(text, today):
    """(date, yearly) for the date a memory mentions: '12 Feb', 'Feb 12', 'tomorrow', 'March mein' (the 1st)."""
    from jarvis.timeparse import find_day

    yearly = bool(_DATE_WORDS.search(text))
    found = find_day(text, datetime.combine(today, datetime.min.time()))
    if found:
        return found[0], yearly
    month = _MONTH_ONLY.search(text)
    if month and not re.search(r"\bmay (?:i|be|you|we)\b", text, re.IGNORECASE):
        number = next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(month.group(1).lower()[:3]))
        if number == today.month:
            return None  # "this month": no particular day to remind on
        year = today.year if number > today.month else today.year + 1
        return date(year, number, 1), yearly
    return None


# ---------------------------------------------------------------- the file
def _path():
    return config.MEMORY_FILE


def load():
    with _lock:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    data.setdefault("memories", [])
    data.setdefault("next_id", 1 + max((int(m["id"].lstrip("m")) for m in data["memories"]
                                        if str(m.get("id", "")).lstrip("m").isdigit()), default=0))
    return data


def save(data):
    with _lock:
        _path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(_path())


def memories():
    return load()["memories"]


def make(text, today=None):
    today = today or date.today()
    found = _date_in(text, today)
    entry = {"text": text, "category": categorize(text), "created": datetime.now().isoformat(timespec="seconds")}
    if is_sensitive(text):
        entry["sensitive"] = True
    if found:
        when, yearly = found
        entry["remind_on"] = when.isoformat()
        if yearly:
            entry["yearly"] = True
    return entry


def add(text, today=None):
    data = load()
    entry = {"id": f"m{data['next_id']}", **make(text, today)}
    data["next_id"] += 1
    data["memories"].append(entry)
    save(data)
    return entry


def replace(memory_id, text, today=None):
    data = load()
    for i, old in enumerate(data["memories"]):
        if old["id"] == memory_id:
            data["memories"][i] = {"id": memory_id, **make(text, today), "updated_from": old["text"]}
            save(data)
            return old, data["memories"][i]
    return None, None


def delete(memory_id):
    data = load()
    gone = next((m for m in data["memories"] if m["id"] == memory_id), None)
    data["memories"] = [m for m in data["memories"] if m["id"] != memory_id]
    save(data)
    return gone


def restore(entry):
    data = load()
    if not any(m["id"] == entry["id"] for m in data["memories"]):
        data["memories"].append(entry)
        save(data)


# ---------------------------------------------------------------- searching
_STOP = {"the", "a", "an", "is", "are", "was", "were", "my", "me", "i", "you", "your", "of", "to", "in", "on", "at",
         "and", "or", "what", "when", "where", "who", "how", "did", "do", "does", "tell", "told", "about", "that",
         "this", "it", "have", "has", "know", "remember", "kab", "kya", "hai", "h", "tha", "thi", "the", "ka", "ki",
         "ke", "ko", "se", "mein", "me", "meri", "mera", "mere", "tera", "teri", "tere", "tujhe", "mujhe", "maine",
         "tune", "bola", "bataya", "baare", "bare", "yaad", "hain", "kaun", "kaunsa", "kahan", "kitna", "kitni",
         "kitne", "hoon", "please", "jarvis", "yaar", "bata", "batao", "for", "with", "s", "so", "kuch", "bhi", "toh",
         "ne", "par", "pe", "ye", "yeh", "wo", "woh", "sabse", "konsi", "kaunsi"}
SYNONYMS = {"birthday": {"birthday", "bday", "janamdin", "janmdin"}, "bday": {"birthday", "bday"},
            "janamdin": {"birthday", "janamdin"}, "anniversary": {"anniversary", "saalgirah"},
            "coffee": {"coffee", "cofee"}, "bike": {"bike", "scooty", "motorcycle"}, "car": {"car", "gaadi"}}


def words(text):
    text = (text or "").lower().replace("'s", " ")
    return [w for w in re.findall(r"[a-z0-9]+", text) if w not in _STOP]


def _word_score(word, memory_words):
    options = SYNONYMS.get(word, {word})
    if options & memory_words:
        return 1.0
    if len(word) >= 4 and any(m.startswith(word) or word.startswith(m) for m in memory_words if len(m) >= 4):
        return 0.8
    close = difflib.get_close_matches(word, list(memory_words), n=1, cutoff=0.8)
    return 0.7 if close else 0.0


def rank(question, items=None, include_sensitive=None):
    """[(score, memory)] best first. Sensitive memories only if the question itself is about the same thing."""
    asked = words(question)
    if not asked:
        return []
    items = memories() if items is None else items
    scored = []
    for memory in items:
        mine = set(words(memory["text"]))
        if not mine:
            continue
        score = sum(_word_score(w, mine) for w in asked) / len(asked)
        if memory.get("sensitive") and not (include_sensitive or _brought_up(question, memory)):
            continue
        if score > 0:
            scored.append((round(score, 3), memory))
    scored.sort(key=lambda pair: pair[1].get("created", ""), reverse=True)  # ties: the newest first
    scored.sort(key=lambda pair: -pair[0])
    return scored


def _brought_up(question, memory):
    """The user mentioned the sensitive topic themselves: a health/sad word, or that memory's own key words."""
    if is_sensitive(question):
        return True
    mine = {w for w in words(memory["text"]) if len(w) >= 4}
    return bool(mine & set(words(question)))


# ---------------------------------------------------------------- speaking a memory back
_TO_YOU = [(r"\bI am\b", "you are"), (r"\bI'm\b", "you're"), (r"\bI was\b", "you were"), (r"\bI've\b", "you've"),
           (r"\bI\b", "you"), (r"\bmyself\b", "yourself"), (r"\bmine\b", "yours"), (r"\bmy\b", "your"),
           (r"\bme\b", "you"), (r"\bmeri\b", "teri"), (r"\bmera\b", "tera"), (r"\bmere\b", "tere"),
           (r"\bmujhe\b", "tujhe"), (r"\bmujhko\b", "tujhko"), (r"\bmaine\b", "tune"), (r"\bmain\b", "tu")]


def to_you(text):
    """'I like cold coffee' -> 'you like cold coffee'; 'meri bike' -> 'teri bike'."""
    out = text
    for pattern, new in _TO_YOU:
        out = re.sub(pattern, lambda m, new=new: new[:1].upper() + new[1:] if m.group()[0].isupper() and
                     (m.start() == 0 or m.group() not in ("I", "I'm", "I am", "I was", "I've")) else new, out,
                     flags=re.IGNORECASE)
    out = re.sub(r"\btu\b(.*?)\bhoon\b", r"tu\1hai", out)
    return out


def spoken(memory):
    return to_you(memory["text"].rstrip(". "))


def when_text(memory, today=None):
    """'on 12 February, in 78 days' for a dated memory."""
    if not memory.get("remind_on"):
        return ""
    today = today or date.today()
    day = next_occurrence(memory, today)
    if day is None:
        return ""
    ahead = (day - today).days
    label = f"{day.day} {day:%B}"
    if ahead == 0:
        return f"{label}, that's today"
    if ahead == 1:
        return f"{label}, that's tomorrow"
    return f"{label}, {ahead} days from now"


def next_occurrence(memory, today):
    try:
        day = date.fromisoformat(memory["remind_on"])
    except (KeyError, ValueError):
        return None
    if memory.get("yearly"):
        for year in (today.year, today.year + 1):
            try:
                candidate = day.replace(year=year)
            except ValueError:  # 29 Feb
                candidate = date(year, 3, 1)
            if candidate >= today:
                return candidate
    return day if day >= today else None


# ---------------------------------------------------------------- the AI: picking and phrasing
def _ai_answer(ctx, question, candidates):
    """Ask the AI to answer from these memories only. Returns the spoken text, or None if they don't answer it."""
    from jarvis.skills import ai

    if not config.AI_API_KEY:
        return None
    nick = persona.nickname()
    lines = "\n".join(f"[{n}] {m['text']}" + (f" (date: {when_text(m)})" if when_text(m) else "")
                      for n, m in enumerate(candidates, 1))
    system = (f"You are {config.ASSISTANT_NAME}, {nick}'s voice assistant and close friend. {nick} asks about "
              f"something they told you earlier. Answer using ONLY the numbered things {nick} told you; pick the one "
              f"(or few) that answer the question. 1 or 2 short spoken sentences, talking to {nick} directly "
              "(tu / you), no markdown. " + persona.ai_style(brief=True) +
              "If none of them answers the question, reply exactly: NONE")
    try:
        status, chunks = ai.stream(system, [], f"Things {nick} told you:\n{lines}\n\nQuestion: {question}", 150)
    except Exception as exc:
        log.info("[memory] AI unavailable: %s", exc)
        return None
    if status >= 400:
        return None
    text = ai.strip_markdown("".join(chunks)).strip()
    if not text or text.upper().startswith("NONE"):
        return None
    return grammar.fix(text)


# ---------------------------------------------------------------- skills: saving
_SAVE = r"(?:remember|yaad rakh(?:na|o|iyo|lena|le|na ki)?|yad rakhna|dhyan rakhna|don't forget|do not forget|note kar le)"


def _clean_fact(text):
    text = re.sub(r"^(?:that|ki|ke|kee|about|this|please|ye|yeh)\s+", "", text.strip(" .,!"), flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:please|yaar|ok|okay)$", "", text, flags=re.IGNORECASE)
    return text[:1].upper() + text[1:] if text else text


@skill("remember", rf"^(?:please\s+)?(?:jarvis\s+)?{_SAVE}\s+(?:that\s+|ki\s+|ke\s+)?(.+)$",
       rf"^(.+?)\s+(?:ye\s+|yeh\s+)?(?:yaad rakh(?:na|o|iyo|lena|le)?|yad rakhna)$",
       r"^(?:save|store) (?:this )?(?:in|to) (?:your )?memory:?\s+(.+)$",
       category="memory", help=["remember that Dhruv's birthday is 12 Feb", "yaad rakhna ki meri bike ki service March mein hai",
                                "remember I like cold coffee"],
       tool="remember", params={"fact": "the thing to remember, in the user's own words"}, command="remember that {fact}")
def remember(ctx, command, match):
    original = ctx.state.get("original") or ""
    if original.lower() == command:  # keep the capitals ("Dhruv")
        match = match.re.search(original) or match
    fact = _clean_fact(match.group(1))
    if re.fullmatch(r"(?:me|this|that|it|to|kuch|something)", fact.lower()) or len(fact.split()) < 2:
        return "Kya yaad rakhun? Poori baat bol." if persona.is_friend() else "What should I remember?"
    if is_secret(fact):
        actions.note("Refused to remember a secret (password, OTP, card, bank, Aadhaar or PAN)")
        return persona.line("memory_secret")
    best = rank(fact, include_sensitive=True)
    if best and best[0][0] >= 0.75 and best[0][1]["text"].lower() != fact.lower():
        old = best[0][1]
        if _same_subject(fact, old["text"]) and ctx.confirm(
                f"Pehle tune bataya tha: {spoken(old)}. Isko update kar doon?" if persona.is_friend()
                else f"You told me before: {spoken(old)}. Shall I update it?"):
            return _do_update(ctx, old, fact)
    elif best and best[0][1]["text"].lower() == fact.lower():
        ctx.state["last_memory"] = best[0][1]["id"]
        return persona.line("memory_known")
    entry = add(fact)
    ctx.state["last_memory"] = entry["id"]
    actions.record(f"Remembered: {fact}", undo=lambda: delete(entry["id"]), undo_text="forget it again")
    reply = persona.line("memory_saved")
    if entry.get("remind_on"):
        day = date.fromisoformat(entry["remind_on"])
        reply += " " + persona.line("memory_will_remind", day=f"{day.day} {day:%B}",
                                    every=" har saal" if entry.get("yearly") and persona.is_friend()
                                    else " every year" if entry.get("yearly") else "")
    return reply


def _same_subject(new, old):
    """Different only in the details ('12 Feb' vs '14 Feb')? Same words apart from numbers and dates."""
    strip = lambda t: {w for w in words(t) if not w.isdigit() and not _MONTH_ONLY.fullmatch(w)}  # noqa: E731
    a, b = strip(new), strip(old)
    return bool(a) and len(a & b) / max(len(a | b), 1) >= 0.6


# ---------------------------------------------------------------- skills: recall
def _topic(text):
    text = re.sub(r"\b(?:maine|tune|mene)\s+(?:kya|kuch)\s+(?:bola|bataya|kaha)\s+(?:tha|thi)?\b", " ", text)
    text = re.sub(r"\b(?:ke|ki)\s+(?:baare|bare)\s+(?:mein|me|mai)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ?.!")


def recall(ctx, question, about_me=False, must_answer=False):
    """Answer from memory. None if nothing matches and must_answer is False (so other skills can try)."""
    if about_me:
        found = [m for m in memories() if not m.get("sensitive")]
        if not found:
            return persona.line("memory_empty")
        order = {"personal": 0, "preferences": 1, "people": 2, "dates": 3, "work": 4, "other": 5}
        found.sort(key=lambda m: (order.get(m.get("category"), 9), m.get("created", "")))
        items = [spoken(m) for m in found]
        intro = (f"Tune mujhe {len(items)} baatein batayi hain:" if persona.is_friend()
                 else f"You've told me {len(items)} things:")
        return speak_list(ctx, intro, items, join=lambda part: ". ".join(part), end=".")
    ranked = [(s, m) for s, m in rank(question) if s >= MATCH]
    if not ranked:
        if not must_answer:
            return None
        topic = _topic(question) or "that"
        return persona.line("memory_none", topic=to_you(topic))
    ctx.state["last_memory"] = ranked[0][1]["id"]
    if len(ranked) > AI_PICK_ABOVE or config.AI_API_KEY:
        answer = _ai_answer(ctx, question, [m for _, m in ranked[:8]])
        if answer:
            return answer
        if len(ranked) > AI_PICK_ABOVE and config.AI_API_KEY and not must_answer:
            return None  # the AI says none of them answers it
    best = ranked[0][1]
    when = when_text(best)
    reply = persona.line("memory_recall", memory=spoken(best))
    return reply + (f" That's {when}." if when and not persona.is_friend() else f" Yaani {when}." if when else "")


@skill("recall memory",
       r"\bwhat (?:do|did) you (?:know|remember) about me\b", r"\b(?:what all|kya kya) (?:do you know|pata hai) (?:about me|mere baare mein)\b",
       r"\btu(?:jhe|mhe)? mere (?:baare|bare) (?:mein|me) kya (?:jaanta|janta|pata)\b",
       r"\bwhat did i (?:tell|say to) you about\s+(.+?)\??$", r"\bwhat did i (?:tell|say)\s+(?:you\s+)?(?:about\s+)?(.+?)\??$",
       r"\b(?:do you|tujhe|tumhe|aapko|tuje)\s+(?:remember|yaad (?:hai|h|he))\s*(.*?)\??$",
       r"^(?:yaad hai|remember)\s+(.+?)\??$",
       r"^(.+?)\s+kab\s+(?:hai|h|he|tha|thi|hoti hai|hota hai|aata hai|aati hai)\??$",
       r"^when(?:'s| is| was)\s+(.+?)\??$", r"^what(?:'s| is)\s+(.+?'s\s+.+?)\??$",
       r"^what do you know about\s+(.+?)\??$",
       category="memory", help=["Dhruv ka birthday kab hai?", "what do you know about me?",
                                "what did I tell you about my bike?"],
       tool="recall_memory", params={"topic": "what to recall, e.g. dhruv birthday, my bike service"},
       command="what did i tell you about {topic}")
def recall_memory(ctx, command, match):
    if re.search(r"\babout me\b|mere (?:baare|bare)", command):
        return recall(ctx, command, about_me=True)
    asked = (match.group(1) if match.re.groups else "") or ""
    explicit = bool(re.search(r"\b(?:what did i|tell you|yaad|remember)\b", command))
    if re.search(r"\b(?:conversation|chat|our talk)\b", command):
        return None
    if not explicit and re.search(r"\b(?:my next|my meeting|the time|the date|the weather|today)\b", command):
        return None  # "when is my next meeting": the calendar
    question = _topic(command)
    return recall(ctx, question if asked else command, must_answer=explicit)


# ---------------------------------------------------------------- skills: managing
@skill("forget memory", r"^(?:please\s+)?(?:forget|delete|remove|erase)\s+(?:the\s+)?(?:memory\s+)?(?:that\s+|about\s+|ki\s+)?(.*)$",
       r"^(?:bhool|bhul)\s+(?:ja|jao|jaa|jaana)\s*(?:ki\s+|ke\s+|ye\s+|about\s+)?(.*)$",
       r"^(.+?)\s+(?:bhool|bhul)\s+(?:ja|jao|jaa)$",
       category="memory", help=["forget that", "bhool ja ki meri bike ki service March mein hai"],
       tool="forget_memory", params={"topic": "which memory to forget"}, command="forget that {topic}", risky=True,
       confirms=True)
def forget_memory(ctx, command, match):
    topic = (match.group(1) or "").strip(" .?!")
    if re.search(r"\b(?:conversation|chat|history|everything we said)\b", topic):
        return None  # "forget our conversation": the AI's chat memory
    if re.match(r"^(?:delete|remove|erase)\b", command) and not re.search(r"\bmemory\b", command):
        return None  # "delete notes.txt": files
    data = memories()
    if not data:
        return persona.line("memory_empty")
    if not topic or re.fullmatch(r"(?:that|it|this|ye|yeh|woh|wo|that one)", topic):
        target = next((m for m in data if m["id"] == ctx.state.get("last_memory")), None)
        if target is None:
            return "Kaunsi baat bhoolun? Thoda bata." if persona.is_friend() else "Which memory should I forget?"
    else:
        ranked = [(s, m) for s, m in rank(topic, data, include_sensitive=True) if s >= MATCH]
        if not ranked:
            return persona.line("memory_none", topic=to_you(topic))
        target = ranked[0][1]
    if not ctx.confirm(persona.line("memory_confirm_forget", memory=spoken(target))):
        return persona.line("memory_kept")
    gone = delete(target["id"])
    ctx.state.pop("last_memory", None)
    actions.record(f"Forgot: {target['text']}", undo=lambda: restore(gone), undo_text="remember it again")
    return persona.line("memory_forgot")


def _do_update(ctx, old, new_text):
    before, after = replace(old["id"], new_text)
    ctx.state["last_memory"] = old["id"]
    actions.record(f"Updated memory: {before['text']} -> {new_text}",
                   undo=lambda: replace(old["id"], before["text"]), undo_text="put the old one back")
    reply = persona.line("memory_updated", old=spoken(before), new=spoken(after))
    if after.get("remind_on"):
        day = date.fromisoformat(after["remind_on"])
        reply += " " + persona.line("memory_will_remind", day=f"{day.day} {day:%B}",
                                    every=" har saal" if after.get("yearly") and persona.is_friend()
                                    else " every year" if after.get("yearly") else "")
    return reply


@skill("update memory", r"^(?:please\s+)?(?:update|correction|correct that|change that|update memory)\s*[:,-]?\s+(.+)$",
       r"^(?:actually|nahi|nahin|no),?\s+(.+?)\s+(?:hai|h),?\s+(?:update kar\w*|change kar\w*)$",
       category="memory", help=["update: Dhruv's birthday is 14 Feb"],
       tool="update_memory", params={"fact": "the corrected fact"}, command="update: {fact}")
def update_memory(ctx, command, match):
    original = ctx.state.get("original") or ""
    if original.lower() == command:
        match = match.re.search(original) or match
    fact = _clean_fact(match.group(1))
    if re.search(r"\b(?:apps|files|windows|wifi|jarvis|yourself|routine)\b", fact, re.IGNORECASE) and \
            len(fact.split()) <= 3:
        return None
    if is_secret(fact):
        return persona.line("memory_secret")
    ranked = [(s, m) for s, m in rank(fact, include_sensitive=True) if s >= MATCH]
    same = [m for s, m in ranked if _same_subject(fact, m["text"])] or [m for _, m in ranked[:1]]
    if not same:
        entry = add(fact)
        ctx.state["last_memory"] = entry["id"]
        actions.record(f"Remembered: {fact}", undo=lambda: delete(entry["id"]), undo_text="forget it again")
        return persona.line("memory_saved")
    return _do_update(ctx, same[0], fact)


@skill("list memories", r"\b(?:list|show|read|tell me)\s+(?:all\s+)?(?:my|your|the)\s+memor(?:y|ies)\b",
       r"\bwhat (?:all )?(?:do you remember|memories do you have)\b", r"\b(?:kitni|kya kya) baatein yaad (?:hain|hai)\b",
       r"^(?:my\s+)?memories$", category="memory", help=["list my memories"])
def list_memories(ctx, command, match):
    data = memories()
    if not data:
        return persona.line("memory_empty")
    latest = sorted(data, key=lambda m: (m.get("created", ""), int(str(m["id"]).lstrip("m") or 0)), reverse=True)
    log.info("\nEverything I remember (%d):\n%s\n", len(data),
             "\n".join(f"  {m['id']:>4}  [{m.get('category', 'other')}] {m['text']}"
                       + (f"  (remind {m['remind_on']}{', yearly' if m.get('yearly') else ''})" if m.get("remind_on") else "")
                       for m in latest))
    shown = [spoken(m) if not m.get("sensitive") else "one personal thing" for m in latest[:5]]
    intro = (f"Mujhe {len(data)} baatein yaad hain. Latest:" if persona.is_friend()
             else f"I remember {len(data)} things. The latest:")
    return f"{intro} " + "; ".join(shown) + "."


def export_path():
    from jarvis.skills import files

    documents = files.known_folders().get("Documents") or Path.home() / "Documents"
    return Path(documents) / "Jarvis memories.txt"


def write_export(path=None):
    path = Path(path or export_path())
    data = memories()
    lines = [f"What {config.ASSISTANT_NAME} remembers for {config.USER_NAME}",
             f"Exported {datetime.now():%d %B %Y, %I:%M %p}. {len(data)} memories.", ""]
    for category in CATEGORIES:
        group = [m for m in data if m.get("category", "other") == category]
        if not group:
            continue
        lines.append(category.capitalize())
        for m in group:
            extra = []
            if m.get("remind_on"):
                day = date.fromisoformat(m["remind_on"])
                extra.append(f"reminder {day.day} {day:%B}" + (" every year" if m.get("yearly") else f" {day.year}"))
            extra.append(f"saved {m.get('created', '')[:10]}")
            lines.append(f"  - {m['text']}  ({'; '.join(extra)})")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@skill("export memories", r"\b(?:export|save|download|backup|back up)\s+(?:all\s+)?(?:my|your|the)\s+memor(?:y|ies)\b",
       category="memory", help=["export my memories"])
def export_memories(ctx, command, match):
    if not memories():
        return persona.line("memory_empty")
    path = write_export()
    actions.record(f"Exported memories to {path}")
    return persona.line("memory_exported", folder=path.parent.name)


# ---------------------------------------------------------------- chat context
# Topics a friend would connect even without the same words: "kuch thanda peene ka mann hai" -> cold coffee
TOPICS = {
    "food": {"eat", "food", "khana", "khaana", "bhookh", "bhook", "hungry", "snack", "dinner", "lunch", "breakfast",
             "nashta", "drink", "peena", "peene", "piyu", "piyun", "pi", "thirsty", "thanda", "garam", "coffee",
             "chai", "tea", "juice", "pizza", "biryani", "maggi", "cook", "recipe", "order", "swiggy", "zomato",
             "momos", "sweet", "dessert", "icecream", "cream"},
    "music": {"music", "song", "songs", "gaana", "gaane", "playlist", "singer", "spotify", "listen", "sunna", "sunu"},
    "movies": {"movie", "movies", "film", "films", "series", "show", "netflix", "watch", "dekhna", "dekhu", "web"},
    "travel": {"trip", "travel", "vacation", "holiday", "ghoomna", "flight", "train", "goa", "manali", "beach"},
    "shopping": {"gift", "gifts", "buy", "kharidna", "shopping", "present", "tohfa"},
}


def _topics(text):
    found = set(words(text)) | set(re.findall(r"[a-z]+", (text or "").lower()))
    return {name for name, vocab in TOPICS.items() if found & vocab}


def context_for(question, count=CONTEXT_COUNT):
    """The memories worth telling the AI for this question (empty if none are relevant)."""
    try:
        ranked = [m for s, m in rank(question) if s >= CONTEXT_MATCH]
        asked_topics = _topics(question)
        if asked_topics and len(ranked) < count:  # same topic, different words (food, music, films...)
            for m in memories():
                if m not in ranked and not m.get("sensitive") and _topics(m["text"]) & asked_topics:
                    ranked.append(m)
        ranked = ranked[:count]
    except Exception:
        log.debug("memory context failed", exc_info=True)
        return ""
    if not ranked:
        return ""
    nick = persona.nickname()
    lines = "; ".join(m["text"] + (f" ({when_text(m)})" if when_text(m) else "") for m in ranked)
    return (f" Things {nick} told you before (the user's own words): {lines}. Use them only if they help with "
            "this exact message, naturally, like a friend who remembers; never force them in.")


# ---------------------------------------------------------------- date reminders
def due(memory, now):
    """'day' / 'before' if this memory's reminder is due at `now` (from 9 am), else None."""
    if not memory.get("remind_on") or now.hour < config.MEMORY_REMINDER_HOUR:
        return None
    today = now.date()
    day = next_occurrence(memory, today)
    if day is None:
        return None
    fired = memory.get("reminded", {})
    if day == today and fired.get("day") != today.isoformat():
        return "day"
    if memory.get("yearly") and day == today + timedelta(days=1) and fired.get("before") != today.isoformat():
        return "before"
    return None


def tick(ctx, now=None):
    """Say + toast every memory reminder that's due. Returns the (kind, text) pairs fired."""
    now = now or datetime.now()
    data = load()
    fired = []
    for memory in data["memories"]:
        kind = due(memory, now)
        if not kind:
            continue
        memory.setdefault("reminded", {})[kind] = now.date().isoformat()
        text = spoken(memory)
        line = persona.line("memory_today" if kind == "day" else "memory_tomorrow", memory=text)
        fired.append((kind, memory["text"]))
        notify.toast("Today" if kind == "day" else "Tomorrow", text)
        ctx.say(line, wait=False)
    if fired:
        save(data)
    return fired


_stop = threading.Event()


@on_startup
def _start_reminders(ctx):
    _stop.clear()

    def loop():
        while not _stop.is_set() and ctx.running:
            try:
                tick(ctx)
            except Exception:
                log.exception("[memory] reminder check failed")
            _stop.wait(60)

    threading.Thread(target=loop, name="memory-reminders", daemon=True).start()


def stop_reminders():
    _stop.set()

