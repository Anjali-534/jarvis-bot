"""Hinglish gender agreement, fixed after the AI writes: verbs addressed to the user follow USER_GENDER
("tu kaisi hai?", "chai piyegi?"), and Jarvis's own first-person verbs follow its voice ("main kar dunga").

The AI is told the rule (persona.grammar_rule) but still slips, especially with a female user. This pass only
touches words whose gender agrees with a NOMINATIVE subject in the same clause:
* first person (Jarvis): "main"/"mai", or the verb itself says so ("-unga", "... hoon")
* second person (the user): "tu", "tum", "aap", or the verb says so ("-oge", "... ho")
It never touches constructions where Hindi verbs agree with the OBJECT instead: "tujhe pata hai", "mujhe lagta
hai", "tune khana khaya". Impersonal ones are left alone too ("kaam ho gaya", "mausam kaisa hai", "chalega?").
English sentences pass through unchanged.
"""
import re

from jarvis import persona

# Pronouns. Dative and ergative ones end the current subject: the verb after them agrees with something else.
ME = {"main", "mai"}                    # not "mein": that's usually "in"
YOU_SINGULAR = {"tu", "too"}
YOU_PLURAL = {"tum", "aap", "ap"}       # respectful / plural: male forms end in -e ("tum kar rahe ho")
# Third-person subjects ("ye"/"yeh" are left out: in replies they're nearly always "this", an object)
THIRD = {"wo", "woh", "vo", "voh", "vah", "wahi", "sab", "log", "koi", "kaun", "jo"}
# Possessives (mera, tera...) are left out too: they describe a noun and don't change the subject.
OBLIQUE = {"mujhe", "mujhko", "maine", "tujhe", "tujhko", "tune", "tumhe", "tumhein", "tumko", "tumne", "aapko",
           "aapne", "usne", "use", "usko", "unhe", "unko", "inhe", "unhone", "isne"}
AUX_ME = {"hoon", "hun", "hu", "hoo"}
AUX_YOU = {"ho"}
AUX = AUX_ME | AUX_YOU | {"hai", "hain", "he", "tha", "thi", "the", "thhe", "hoga", "hogi", "hoge", "hongi",
                          "honge"}
# Words that look like "-ta" verbs but aren't
NOT_VERBS = {"pata", "sasta", "rasta", "raasta", "khata", "mata", "pita", "chhata", "bata", "ata", "beta", "seta",
             "data", "kata", "neta", "geeta", "sita", "gita", "ganta"}
# Subjectless questions that are clearly about the user ("thak gaya?", "chai piyega?", "bore ho gaya?")
PERSONAL_ROOTS = ("thak", "so", "uth", "aa", "pahunch", "pohoch", "jaag", "jag", "baith", "nahaa", "naha", "kha",
                  "pi", "piy", "kar", "sun", "dekh", "padh", "aay", "ja", "jaa", "soch", "khel", "seekh", "bana",
                  "le", "de", "ruk", "nikal", "ghoom")
PERSON_STATES = {"bore", "free", "ready", "busy", "late", "bimaar", "beemar", "pareshan", "naraz", "naaraz",
                 "udaas", "udas", "fit", "tired", "sick", "excited", "nervous", "khush", "upset"}
ADJECTIVES = {"akel": "akel", "bhookh": "bhookh", "bhukh": "bhukh", "pyaas": "pyaas", "pyas": "pyas",  # roots + a/i/e
              "thak": "thak", "baith": "baith", "khad": "khad", "soy": "soy"}
_WORD = re.compile(r"[A-Za-z]+|[^A-Za-z]+")
_BOUNDARY = re.compile(r"[.,!?;:\n]")
CONJUNCTIONS = {"aur", "lekin", "magar", "kyunki", "kyuki", "toh", "to", "jab", "agar", "warna", "ki", "but", "and",
                "phir", "fir", "taaki", "jabki"}  # not "so": "so gaya" is "fell asleep"
HINDI_MARKERS = {"hai", "hain", "hoon", "hun", "ho", "tha", "thi", "kya", "nahi", "nahin", "tu", "tum", "aap",
                 "mujhe", "tujhe", "raha", "rahi", "rahe", "gaya", "gayi", "kar", "toh", "bhi", "yaar", "na", "ka",
                 "ki", "ke", "ko", "se", "kaisa", "kaise", "kaisi", "kyun", "kab", "kahan", "dunga", "dungi",
                 "hoga", "hogi", "bata", "chal", "abhi", "kuch", "bahut", "acha", "accha", "theek", "main", "mai",
                 "mera", "meri", "tera", "teri", "tere", "mere", "ye", "yeh", "wo", "woh", "haan", "arre", "sakta",
                 "sakti", "rahe", "gaye", "pe", "par", "aaj", "kal", "matlab", "tune", "maine"}


def _is_hinglish(words):
    found = {w for w in words if w in HINDI_MARKERS}
    if len(found) >= 2 or (len(found) == 1 and len(words) <= 3):
        return True
    return len(words) <= 4 and any(_future(w) for w in words)  # "chai piyega?"


# ---------------------------------------------------------------- the forms
def _form(word, female, plural):
    """The word in the wanted gender, or None if it isn't a gendered verb/adjective form."""
    w = word.lower()
    end_f, end_m = "i", ("e" if plural else "a")
    # future: karega/karegi/karenge/karengi, karoge/karogi, karunga/karungi, hoga/hogi
    found = re.fullmatch(r"(\w+?)(unga|ungi|oonga|oongi)", w)
    if found:
        stem, suffix = found.groups()
        return stem + (suffix[:-1] + ("i" if female else "a"))
    found = re.fullmatch(r"(\w+?e)(ga|gi|nge|ngi)", w)
    if found and len(w) >= 4 and w not in {"omega", "mega", "vega", "bega"}:
        stem, suffix = found.groups()
        if suffix in ("nge", "ngi"):
            return stem + ("ngi" if female else "nge")
        return stem + ("gi" if female else "ga")
    found = re.fullmatch(r"(\w+?o)(ge|gi)", w)
    if found and w not in {"yoge"} and len(w) >= 4:
        return found.group(1) + ("gi" if female else "ge")
    if w in ("hoga", "hogi", "hoge", "hongi", "honge"):
        return ("hongi" if female else "honge") if plural else ("hogi" if female else "hoga")
    # progressive and perfective helpers
    if w in ("raha", "rahi", "rahe"):
        return "rah" + (end_f if female else end_m)
    if w in ("gaya", "gayi", "gaye", "gai"):
        return "gay" + (end_f if female else end_m)
    if w in ("aaya", "aayi", "aaye", "aai", "aya", "ayi", "aye"):
        base = "aay" if w.startswith("aa") else "ay"
        return base + (end_f if female else end_m)
    if w in ("tha", "thi"):  # "the" is skipped: it's also English
        return "thi" if female else "tha"
    if w in ("kaisa", "kaise", "kaisi"):
        return "kais" + (end_f if female else end_m)
    if w in ("hua", "hui", "hue"):
        return "hui" if female else ("hue" if plural else "hua")
    for root in ADJECTIVES:
        if re.fullmatch(rf"{root}(?:a|i|e)", w):
            return root + (end_f if female else end_m)
    # habitual -ta/-ti/-te (karta, sochti, sakte, chahta): only before an auxiliary, checked by the caller
    found = re.fullmatch(r"(\w{2,}t)(a|i|e)", w)
    if found and w not in NOT_VERBS:
        return found.group(1) + (end_f if female else end_m)
    return None


def _habitual(word):
    return bool(re.fullmatch(r"\w{2,}t[aie]", word.lower())) and word.lower() not in NOT_VERBS


def _future(word):
    w = word.lower()
    return bool(re.fullmatch(r"\w+?(?:unga|ungi|oonga|oongi|ega|egi|enge|engi|oge|ogi)", w)) or \
        w in ("hoga", "hogi", "hoge", "hongi", "honge")


def _match_case(original, new):
    if original.isupper() and len(original) > 1:
        return new.upper()
    if original[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


# ---------------------------------------------------------------- deciding who each word is about
def _clauses(tokens):
    """Split token indexes into clauses at punctuation and conjunctions."""
    clauses, current = [], []
    for i, tok in enumerate(tokens):
        if not tok[0].isalpha():
            if _BOUNDARY.search(tok):
                if current:
                    clauses.append(current)
                current = []
            continue
        if tok.lower() in CONJUNCTIONS and current:
            clauses.append(current)
            current = []
            continue
        current.append(i)
    if current:
        clauses.append(current)
    return clauses


def _who_by_morphology(word, nxt):
    """'me' / 'you' when the verb form itself says who: -unga is always me, -oge always you; 'hoon' me, 'ho' you."""
    w = word.lower()
    if re.fullmatch(r"\w+?(?:unga|ungi|oonga|oongi)", w):
        return "me", False
    if re.fullmatch(r"\w+?o(?:ge|gi)", w) and len(w) >= 4:
        return "you", True
    for aux in nxt:
        if aux in AUX_ME:
            return "me", False
        if aux in AUX_YOU:
            return "you", True
        if aux in AUX or aux in ("na", "kya", "nahi"):
            continue
        break
    return None, None


def fix(text, user_female=None, me_female=None):
    """Fix gender agreement in Hinglish text. Defaults come from USER_GENDER and the voice."""
    if not text:
        return text
    neutral_user = user_female is None and persona.user_is_neutral()  # neutral: leave words about the user alone
    user_female = persona.user_is_female() if user_female is None else user_female
    me_female = persona.voice_is_female() if me_female is None else me_female
    tokens = _WORD.findall(text)
    words = [t.lower() for t in tokens if t[0].isalpha()]
    if not _is_hinglish(words):
        return text
    changed = list(tokens)
    for clause in _clauses(tokens):
        lowered = [tokens[i].lower() for i in clause]
        if not _is_hinglish(lowered) and len(clause) > 3:
            continue
        is_question = "?" in "".join(tokens[clause[-1] + 1:clause[-1] + 2]) if clause else False
        # the nominative subject in force at each position (None after a dative/ergative/third-person word)
        subjects, blocked, current, stop = [], [], None, False
        for w in lowered:
            if w in OBLIQUE or w in THIRD:
                stop = True  # after "mujhe"/"tune"/"wo", a later "tu" isn't this verb's subject
            elif w in ME | YOU_SINGULAR | YOU_PLURAL:
                stop = False
            blocked.append(stop)
            if w in ME:
                current = ("me", False)
            elif w in YOU_SINGULAR:
                current = ("you", False)
            elif w in YOU_PLURAL:
                current = ("you", True)
            elif w in OBLIQUE or w in THIRD:
                current = None
            subjects.append(current)
        explicit = [s for s in subjects if s]
        later = {}  # a subject right after the verb ("kab aaya tu?", "kaisi hai tu", "samajh gaya main")
        for pos in range(len(lowered)):
            for w in lowered[pos + 1:pos + 3]:
                if w in ME:
                    later[pos] = ("me", False)
                elif w in YOU_SINGULAR:
                    later[pos] = ("you", False)
                elif w in YOU_PLURAL:
                    later[pos] = ("you", True)
                if pos in later or w not in AUX:
                    break
        for pos, index in enumerate(clause):
            word = lowered[pos]
            if word in ME | YOU_SINGULAR | YOU_PLURAL | AUX_ME | AUX_YOU or word == "the":
                continue
            nxt = lowered[pos + 1:pos + 4]
            prev = lowered[pos - 1] if pos else ""
            candidate = _form(word, True, False)
            if candidate is None:
                continue
            # what kind of word it is decides how sure we must be
            if word in ("kaisa", "kaise", "kaisi") and not (nxt and nxt[0] in AUX):
                continue  # "tu kaise karega": "kaise" means how
            if _habitual(word) and not _future(word) and not any(a in AUX for a in nxt[:2]):
                continue  # "-ta" words count only as verbs before hai/ho/hoon/tha ("pata", "sasta" never)
            if word in ("tha", "thi") and prev in ("pata",):
                continue
            if word in ("gaya", "gayi", "gaye", "gai", "raha", "rahi", "rahe") and prev == "ho" and \
                    (pos < 2 or lowered[pos - 2] not in PERSON_STATES):
                continue  # "kaam ho gaya" is impersonal; "bore ho gaya" is about a person
            if word in ("hua", "hui", "hue") and not (prev and _form(prev, True, False)):
                continue  # only "thaki hui" style, after an adjective
            who, plural = _who_by_morphology(word, nxt)
            if who is None:
                subject = subjects[pos] or (None if blocked[pos] else later.get(pos))
                if subject:
                    who, plural = subject
                elif is_question and not explicit and _about_user(word, prev, lowered, pos):
                    who, plural = "you", False
            if who is None or (who == "you" and neutral_user):
                continue
            female = user_female if who == "you" else me_female
            new = _form(word, female, plural)
            if new and new != word:
                changed[index] = _match_case(tokens[index], new)
    return "".join(changed)


def _about_user(word, prev, lowered, pos):
    """A subjectless question: is this verb about the person being asked? ("thak gaya?", "chai piyega?")"""
    if _future(word):
        stem = re.sub(r"(?:ega|egi|enge|engi)$", "", word)
        return any(stem == root or (stem.startswith(root) and len(root) >= 3) for root in PERSONAL_ROOTS)
    if word in ("gaya", "gayi", "aaya", "aayi", "aya", "ayi"):  # not "raha": "Rahul kya kar raha hai?"
        if prev == "ho":
            return pos >= 2 and lowered[pos - 2] in PERSON_STATES
        return prev in PERSONAL_ROOTS or word in ("aaya", "aayi", "aya", "ayi")
    return any(re.fullmatch(rf"{root}[aie]", word) for root in ADJECTIVES)
