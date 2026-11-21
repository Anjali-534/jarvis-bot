"""Hindi mode support: turn Devanagari speech into English command text the skills understand.

Google's hi-IN recognizer writes everything in Devanagari, including English loanwords
("क्रोम खोलो" for "open chrome"). normalize_command() runs before routing, only when the
text contains Devanagari, and:

1. maps Hindi/Hinglish command phrases to English ("आवाज़ बढ़ाओ" -> "volume up"),
2. maps loanwords back to English ("क्रोम" -> "chrome"), with a rule-based romanizer plus
   fuzzy matching against the app, website, contact and project names Jarvis knows,
3. fixes Hindi word order ("क्रोम खोलो" -> "open chrome", "दिल्ली का मौसम" -> "weather in दिल्ली").

Messages, notes and reminder texts are kept exactly as spoken. If the result doesn't match
any skill, the router sends the ORIGINAL text to the AI, so chatting in Hindi still works.
"""
import logging
import difflib
import re
import unicodedata

log = logging.getLogger(__name__)

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def has_devanagari(text):
    return bool(DEVANAGARI.search(text or ""))


def norm(text):
    """Spelling-insensitive form for matching: no nukta (ज़ -> ज), chandrabindu -> anusvara, ASCII digits."""
    text = unicodedata.normalize("NFD", text or "").replace("़", "")
    return unicodedata.normalize("NFC", text).replace("ँ", "ं").translate(_DIGITS)


def tokenize(text):
    text = re.sub(r"[।॥?!,;:\"“”‘’()]", " ", text or "")
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # keep "2.5"
    return text.split()


# ---------------------------------------------------------------- romanizer (rule-based fallback)
_VOWELS = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai",
           "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
_MATRAS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
           "ौ": "au", "ॉ": "o", "ॅ": "e"}
_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}
_VIRAMA = "्"


def romanize_word(word):
    """'ध्रुव' -> 'dhruv', 'जार्विस' -> 'jarvis', 'हूँ' -> 'hun'. Approximate, for matching and speech."""
    word = norm(word)
    out = []
    for i, ch in enumerate(word):
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if ch in _CONSONANTS:
            out.append(_CONSONANTS[ch])
            if nxt and nxt not in _MATRAS and nxt != _VIRAMA and nxt not in "ंः" and nxt in _CONSONANTS:
                out.append("a")  # inherent vowel between consonants; dropped at the end of a word
            elif nxt and nxt in "ंः":
                out.append("a")
        elif ch in _MATRAS:
            out.append(_MATRAS[ch])
        elif ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch == "ं":
            out.append("n")
        elif ch == "ः":
            out.append("h")
        elif ch == _VIRAMA:
            continue
        else:
            out.append(ch)
    return "".join(out)


def to_roman(text):
    """Romanize the Devanagari words in a sentence, leaving everything else alone."""
    return " ".join(romanize_word(w) if has_devanagari(w) else w for w in (text or "").split())


# ---------------------------------------------------------------- dictionaries
# Whole commands. Longest phrase wins, so "लैपटॉप बंद करो" beats "बंद करो".
COMMANDS = {
    "what time is it": ["समय क्या है", "क्या समय है", "टाइम क्या है", "क्या टाइम है", "कितने बजे हैं", "कितने बजे है",
                        "कितना बजा है", "समय बताओ", "टाइम बताओ", "समय", "टाइम"],
    "what's the date": ["आज की तारीख क्या है", "आज क्या तारीख है", "तारीख क्या है", "आज की तारीख",
                        "आज कौन सा दिन है", "तारीख", "दिनांक"],
    "weather": ["मौसम कैसा है", "मौसम", "वेदर"],
    "temperature": ["तापमान", "टेंपरेचर", "टेम्परेचर"],
    "will it rain": ["बारिश होगी", "बारिश होगी क्या", "बारिश", "बरसात"],
    "do i need an umbrella": ["छाता चाहिए", "छाता ले जाऊं", "छाता"],
    "aqi": ["प्रदूषण", "एक्यूआई", "एक्यू आई", "ए क्यू आई", "वायु गुणवत्ता", "हवा की गुणवत्ता", "हवा की क्वालिटी",
            "एयर क्वालिटी", "पॉल्यूशन", "हवा कैसी है", "स्मॉग"],
    "is it safe to go out": ["बाहर जाना सुरक्षित है", "बाहर जाना ठीक है", "बाहर जाना सेफ है", "बाहर जा सकते हैं",
                             "सुरक्षित है", "सेफ है"],
    "do i need a mask": ["मास्क चाहिए", "मास्क पहनना चाहिए", "मास्क पहनूं", "मास्क लगाना चाहिए"],
    "red zones": ["रेड ज़ोन", "रेड ज़ोन्स", "सबसे प्रदूषित इलाके", "सबसे ज़्यादा प्रदूषण"],
    "take a screenshot": ["स्क्रीनशॉट लो", "स्क्रीनशॉट ले लो", "स्क्रीनशॉट लीजिए", "स्क्रीनशॉट", "स्क्रीन शॉट"],
    "battery status": ["बैटरी कितनी है", "बैटरी कितनी बची है", "बैटरी कितना है", "बैटरी स्टेटस", "बैटरी"],
    "lock the laptop": ["लैपटॉप लॉक करो", "लैपटॉप लॉक कर दो", "कंप्यूटर लॉक करो", "स्क्रीन लॉक करो", "लॉक करो",
                        "लॉक कर दो"],
    "shut down the laptop": ["लैपटॉप बंद करो", "लैपटॉप बंद कर दो", "कंप्यूटर बंद करो", "कंप्यूटर बंद कर दो",
                             "पीसी बंद करो", "लैपटॉप शटडाउन करो", "शटडाउन करो"],
    "restart the laptop": ["लैपटॉप रीस्टार्ट करो", "रीस्टार्ट करो", "रीस्टार्ट कर दो"],
    "cancel shutdown": ["शटडाउन कैंसिल करो", "शटडाउन रोको"],
    "go to sleep": ["सो जाओ", "चुप हो जाओ", "सुनना बंद करो", "स्लीप मोड"],
    "help": ["मदद करो", "मदद चाहिए", "मदद", "हेल्प"],
    "volume up": ["आवाज़ बढ़ाओ", "आवाज़ बढ़ा दो", "आवाज़ तेज़ करो", "आवाज़ तेज़ कर दो", "आवाज़ ज़्यादा करो",
                  "वॉल्यूम बढ़ाओ", "वॉल्यूम बढ़ा दो", "वॉल्यूम अप", "वॉल्यूम तेज़ करो"],
    "volume down": ["आवाज़ कम करो", "आवाज़ कम कर दो", "आवाज़ धीमी करो", "आवाज़ धीरे करो", "आवाज़ घटाओ",
                    "वॉल्यूम कम करो", "वॉल्यूम कम कर दो", "वॉल्यूम डाउन", "वॉल्यूम घटाओ"],
    "mute": ["म्यूट करो", "म्यूट कर दो", "म्यूट", "आवाज़ बंद करो", "आवाज़ बंद कर दो"],
    "pause the music": ["गाना रोको", "गाना बंद करो", "गाना पॉज़ करो", "पॉज़ करो", "पॉज़", "म्यूज़िक बंद करो",
                        "म्यूज़िक रोको"],
    "next song": ["अगला गाना चलाओ", "अगला गाना लगाओ", "अगला गाना", "नेक्स्ट गाना", "नेक्स्ट सॉन्ग"],
    "previous song": ["पिछला गाना चलाओ", "पिछला गाना लगाओ", "पिछला गाना"],
    "brightness up": ["ब्राइटनेस बढ़ाओ", "ब्राइटनेस बढ़ा दो", "स्क्रीन की रोशनी बढ़ाओ", "रोशनी बढ़ाओ"],
    "brightness down": ["ब्राइटनेस कम करो", "ब्राइटनेस कम कर दो", "स्क्रीन की रोशनी कम करो", "रोशनी कम करो"],
    "tell me a joke": ["एक चुटकुला सुनाओ", "चुटकुला सुनाओ", "कोई जोक सुनाओ", "जोक सुनाओ", "चुटकुला", "जोक"],
    "news": ["ताज़ा खबरें", "खबरें सुनाओ", "न्यूज़ सुनाओ", "खबरें", "खबर", "समाचार", "न्यूज़"],
    "git status": ["गिट स्टेटस"],
    "list my projects": ["मेरे प्रोजेक्ट्स दिखाओ", "मेरे प्रोजेक्ट्स बताओ", "प्रोजेक्ट्स दिखाओ", "प्रोजेक्ट्स बताओ",
                         "प्रोजेक्ट्स की लिस्ट", "मेरे प्रोजेक्ट्स"],
    "read my notes": ["मेरे नोट्स पढ़ो", "नोट्स पढ़ो", "नोट्स सुनाओ", "नोट्स दिखाओ", "मेरे नोट्स"],
    "what are my reminders": ["मेरे रिमाइंडर्स", "रिमाइंडर्स बताओ", "रिमाइंडर्स दिखाओ"],
    "system status": ["सिस्टम स्टेटस", "सिस्टम कैसा है"],
    "goodbye": ["अलविदा", "बाय बाय", "बाय", "बंद हो जाओ"],
    "thank you": ["धन्यवाद", "शुक्रिया", "थैंक यू", "थैंक्स"],
    "hello": ["नमस्ते", "नमस्कार", "हेलो", "हैलो", "हाय"],
    "who are you": ["तुम कौन हो", "आप कौन हो", "तुम्हारा नाम क्या है", "आपका नाम क्या है"],
    "how are you": ["आप कैसे हैं", "आप कैसी हैं", "तुम कैसे हो", "तुम कैसी हो", "कैसे हो", "कैसी हो", "क्या हाल है",
                    "क्या हाल चाल है"],
    "switch to english": ["स्विच टू इंग्लिश", "इंग्लिश में बात करो", "अंग्रेज़ी में बात करो", "इंग्लिश मोड"],
    "switch to hindi": ["स्विच टू हिंदी", "हिंदी मोड"],
}

# Verbs that come AFTER their object in Hindi: "क्रोम खोलो" -> "open chrome".
VERBS = {
    "open": ["खोलो", "खोल दो", "खोलिए", "खोल दीजिए", "खोलना", "ओपन करो", "ओपन कर दो", "ओपन"],
    "close": ["बंद करो", "बंद कर दो", "बंद कीजिए", "क्लोज़ करो", "क्लोज़ कर दो", "क्लोज़"],
    "play": ["चलाओ", "चला दो", "बजाओ", "बजा दो", "प्ले करो", "प्ले कर दो", "प्ले", "लगाओ", "लगा दो"],
    "search": ["सर्च करो", "सर्च कर दो", "सर्च", "ढूंढो", "ढूंढ दो", "खोजो", "खोज दो", "गूगल करो"],
    "run": ["रन करो", "रन कर दो", "स्टार्ट करो"],
    "pull": ["पुल करो", "पुल कर दो"],
}

# English loanwords written in Devanagari.
LOANWORDS = {
    "chrome": ["क्रोम"], "whatsapp": ["व्हाट्सएप", "व्हाट्सऐप", "वॉट्सऐप", "वाट्सएप", "व्हाट्सअप", "वॉट्सएप"],
    "youtube": ["यूट्यूब", "यूटियूब", "यू ट्यूब"], "notepad": ["नोटपैड"],
    "vs code": ["वीएस कोड", "वी एस कोड", "विजुअल स्टूडियो कोड", "वीएस कोड़"], "code": ["कोड"],
    "google": ["गूगल"], "gmail": ["जीमेल"], "github": ["गिटहब", "गिट हब"], "git": ["गिट"],
    "linkedin": ["लिंक्डइन"], "instagram": ["इंस्टाग्राम"], "chatgpt": ["चैटजीपीटी", "चैट जीपीटी"],
    "claude": ["क्लॉड"], "spotify": ["स्पॉटिफाई"], "edge": ["एज"], "firefox": ["फायरफॉक्स", "फ़ायरफ़ॉक्स"],
    "calculator": ["कैलकुलेटर"], "paint": ["पेंट"], "excel": ["एक्सेल"], "word": ["वर्ड"],
    "powerpoint": ["पावरपॉइंट"], "outlook": ["आउटलुक"], "teams": ["टीम्स"], "zoom": ["ज़ूम"],
    "telegram": ["टेलीग्राम"], "discord": ["डिस्कॉर्ड"], "settings": ["सेटिंग्स", "सेटिंग"], "camera": ["कैमरा"],
    "terminal": ["टर्मिनल"], "file explorer": ["फाइल एक्सप्लोरर"], "pycharm": ["पायचार्म"],
    "amazon": ["अमेज़न", "अमेज़ॅन"], "flipkart": ["फ्लिपकार्ट"], "maps": ["मैप्स"],
    "stack overflow": ["स्टैक ओवरफ्लो"], "task manager": ["टास्क मैनेजर"],
    "volume": ["वॉल्यूम"], "brightness": ["ब्राइटनेस"], "pm": ["पीएम"], "no2": ["एनओ2", "एनओटू", "एन ओ 2"],
    "nitrogen dioxide": ["नाइट्रोजन डाइऑक्साइड"],
    "laptop": ["लैपटॉप"], "computer": ["कंप्यूटर"], "screen": ["स्क्रीन"], "timer": ["टाइमर"],
    "reminder": ["रिमाइंडर"], "notes": ["नोट्स"], "project": ["प्रोजेक्ट"], "projects": ["प्रोजेक्ट्स"],
    "file": ["फाइल", "फ़ाइल"], "status": ["स्टेटस"], "music": ["म्यूज़िक"], "songs": ["गाने"],
    "song": ["गाना", "सॉन्ग"], "video": ["वीडियो"], "cricket": ["क्रिकेट"], "tech": ["टेक"],
    "today": ["आज"], "tomorrow": ["कल"], "and": ["और", "एंड"], "to": ["टू"], "english": ["इंग्लिश"],
    "hindi": ["हिंदी", "हिन्दी"], "message": ["मैसेज", "मेसेज"], "call": ["कॉल"], "safe": ["सेफ"], "mask": ["मास्क"],
    "app": ["ऐप"], "apps": ["ऐप्स"], "website": ["वेबसाइट"], "dot": ["डॉट"], "com": ["कॉम"],
    "backend": ["बैकएंड"], "frontend": ["फ्रंटएंड"], "admin": ["एडमिन"], "panel": ["पैनल"],
}

# Contact words people use instead of names.
RELATIONS = {"mom": ["माँ", "मां", "मम्मी", "मम्मा", "मॉम", "माता जी"], "dad": ["पापा", "डैडी", "डैड", "पिताजी"],
             "bhaiya": ["भैया", "भाई"], "didi": ["दीदी"]}

# Little words that carry no meaning for commands.
FILLERS = set(map(norm, """है हैं हो था थी क्या कैसा कैसी कैसे कितना कितनी कितने बताओ बताइए बता बताना ज़रा प्लीज़ please
    मुझे मुझको मेरे मेरा मेरी लिए भी अभी तो ना यार जी सा करो कर दो दीजिए कीजिए लो दें दिखाओ सुनाओ""".split()))
# Grammar words: never fuzzy-matched to names.
FUNCTION_WORDS = FILLERS | set(map(norm, "को पर पे में मे का की के से तक और कि ने यह ये वो वह एक".split()))

VERB_MARK = "§"
_TIME_WORDS = ("today", "tomorrow")


def _build(table, kind):
    entries = []
    for english, phrases in table.items():
        for phrase in phrases:
            entries.append((norm(phrase), english, kind))
    return entries


_ENTRIES = _build(COMMANDS, "cmd") + _build(VERBS, "verb") + _build(LOANWORDS, "word")
_ENTRY_MAP = {}
for _hindi, _english, _kind in _ENTRIES:
    _ENTRY_MAP.setdefault(_hindi, (_english, _kind))
_PHRASE_RE = re.compile(
    r"(?<!\S)(" + "|".join(re.escape(h) for h in sorted(_ENTRY_MAP, key=len, reverse=True)) + r")(?!\S)"
)
_RELATIONS = {norm(h): english for english, words in RELATIONS.items() for h in words}
_SINGLE_WORD_CMDS = {h for h, (e, k) in _ENTRY_MAP.items() if k == "cmd" and " " not in h}


# ---------------------------------------------------------------- helpers
def _known_names():
    """App, website, contact and project names Jarvis already knows (for fuzzy matching)."""
    names = set(LOANWORDS)
    try:
        from jarvis.skills import apps, vscode, whatsapp

        names |= set(apps.APPS) | set(apps.WEBSITES) | set(whatsapp.load_contacts()) | set(vscode.get_projects())
    except Exception as exc:  # never let lookups break routing
        log.warning(f"[translit] name lookup failed: {exc}")
    return names


def _fuzzy_name(roman, names, cutoff=0.8):
    best, best_score = None, 0.0
    for name in names:
        score = max(difflib.SequenceMatcher(None, roman, name).ratio(),
                    difflib.SequenceMatcher(None, roman.replace(" ", ""), name.replace(" ", "")).ratio())
        if score > best_score:
            best, best_score = name, score
    return best if best_score >= cutoff else None


def _span_tokens(s, start, end):
    """Token indexes covered by s[start:end] in a single-space-joined string."""
    return set(range(s[:start].count(" "), s[:end].rstrip().count(" ") + 1))


def to_contact(text):
    """'माँ' -> 'mom', 'ध्रुव' -> 'dhruv' (fuzzy against saved contacts)."""
    text = re.sub(r"\s+जी$", "", norm(text).strip())
    if text in _RELATIONS:
        return _RELATIONS[text]
    roman = to_roman(text)
    try:
        from jarvis.skills import whatsapp

        match = _fuzzy_name(roman, list(whatsapp.load_contacts()), cutoff=0.7)
    except Exception:
        match = None
    return match or roman


# ---------------------------------------------------------------- special sentence shapes
_WA = r"(?:व्हाट्सएप|व्हाट्सऐप|वॉट्सऐप|वाट्सएप|व्हाट्सअप|वॉट्सएप|whatsapp)"
_MSG = r"(?:मैसेज|मेसेज|संदेश|टेक्स्ट|message)"
_SEND = r"(?:भेजो|भेज दो|भेजना|भेज देना|करो|कर दो|बोलो|बोल दो|कहो|कह दो|बता दो|बताओ|लिखो)"
_SAY_VERBS = {norm(v) for v in "बोलो बोल दो कहो कह दो बता दो बताओ भेजो भेज दो".split()}
_WA_SEND_RE = re.compile(norm(
    rf"^(?:{_WA} (?:पर|पे) )?(?P<c>.+?) को (?:{_WA} (?:पर|पे) )?(?:(?P<m>{_MSG}) |(?P<w>{_WA}) )?{_SEND}(?: (?P<rest>.+))?$"
))
_WA_CALL_RE = re.compile(norm(
    rf"^(?:{_WA} (?:पर|पे) )?(?P<c>.+?) को (?:{_WA} (?:पर|पे) )?(?P<v>वीडियो )?(?:कॉल|फ़ोन) (?:करो|कर दो|लगाओ|मिलाओ|करना)"
    rf"(?: {_WA} (?:पर|पे))?$"
))
_NOT_CONTACTS = {norm(w) for w in "मुझ मुझे मैं हम हमें इस उस".split()}


def _whatsapp(orig, toks):
    s = " ".join(toks)
    # message after "कि" (or "की" right after a say-verb, a common recognition slip)
    split = next((i for i, t in enumerate(toks) if t == "कि" or (t == "की" and i and toks[i - 1] in _SAY_VERBS)), None)
    head = toks[:split] if split is not None else toks
    head_s = " ".join(head)
    call = _WA_CALL_RE.match(s)
    if call and norm(call.group("c")) not in _NOT_CONTACTS:
        return f"{'video ' if call.group('v') else ''}call {to_contact(call.group('c'))} on whatsapp"
    match = _WA_SEND_RE.match(head_s)
    if not match or match.group("c") in _NOT_CONTACTS:
        return None
    mentions_whatsapp = bool(match.group("m") or match.group("w") or re.search(norm(_WA), head_s))
    tell_that = split is not None and bool(head) and head[-1] in _SAY_VERBS  # "माँ को बोलो कि ..."
    if not (mentions_whatsapp or tell_that):
        return None
    contact = to_contact(match.group("c"))
    if split is not None:
        message = " ".join(orig[split + 1:])
    elif match.group("rest"):
        message = " ".join(orig[min(_span_tokens(head_s, match.start("rest"), match.end("rest"))):])
    else:
        message = ""
    return f"send a whatsapp to {contact} saying {message}" if message else f"send a whatsapp to {contact}"


_NUM_WORDS = {norm(k): v for k, v in {"एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "छह": 6, "छः": 6, "सात": 7,
                                       "आठ": 8, "नौ": 9, "दस": 10, "पंद्रह": 15, "बीस": 20, "पच्चीस": 25, "तीस": 30,
                                       "चालीस": 40, "पैंतालीस": 45, "पचास": 50}.items()}
_NUM = "|".join([r"\d+(?:\.\d+)?"] + sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True))
_UNITS = {norm(k): v for k, v in {"मिनट": "minutes", "मिनिट": "minutes", "मिनटों": "minutes", "घंटे": "hours",
                                   "घंटा": "hours", "घंटों": "hours", "सेकंड": "seconds", "सेकेंड": "seconds"}.items()}
_UNIT = "|".join(map(re.escape, _UNITS))
_DURATION_RE = re.compile(norm(rf"(?<!\S)(?:(?P<half>आधे|आधा) घंटे|(?P<deDh>डेढ़) घंटे|(?P<n>{_NUM}) (?P<u>{_UNIT}))"
                               r"(?: (?:में|बाद|के बाद))?(?!\S)"))
_CLOCK_RE = re.compile(norm(r"(?<!\S)(?:(?P<part>सुबह|शाम|रात|दोपहर)(?: को)? )?(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))? बजे"
                            r"(?: (?:को|तक))?(?!\S)"))
_REMIND_RE = re.compile(norm(r"(?<!\S)(?:मुझे |मुझको )?(?:याद (?:दिलाना|दिला देना|दिला दो|दिलाओ|दिलाइए|दिला दीजिए|करवाना)"
                             r"|रिमाइंड (?:करना|करो|कर देना|कर दो))(?!\S)"))
_NOTE_RE = re.compile(norm(r"(?<!\S)(?:नोट (?:करो|कर लो|कर दो|लिखो|लिख लो|बनाओ)|लिख लो|याद रखो|नोट)(?!\S)"))
_LEAD_STRIP = {norm(w) for w in "कि मुझे मुझको यह ये".split()}
_TRAIL_STRIP = [["के", "लिए"], ["का"], ["की"], ["के"]]


def _trim(words):
    while words and norm(words[0]) in _LEAD_STRIP:
        words = words[1:]
    changed = True
    while changed and words:
        changed = False
        for tail in _TRAIL_STRIP:
            if len(words) > len(tail) and [norm(w) for w in words[-len(tail):]] == tail:
                words, changed = words[:-len(tail)], True
    return words


def _reminder(orig, toks):
    s = " ".join(toks)
    verb = _REMIND_RE.search(s)
    if not verb:
        return None
    used = _span_tokens(s, verb.start(), verb.end())
    when = ""
    duration = _DURATION_RE.search(s)
    clock = _CLOCK_RE.search(s)
    if duration:
        used |= _span_tokens(s, duration.start(), duration.end())
        if duration.group("half"):
            when = "in half an hour"
        elif duration.group("deDh"):
            when = "in an hour and a half"
        else:
            n = duration.group("n")
            n = _NUM_WORDS.get(n, n)
            when = f"in {n} {_UNITS[duration.group('u')]}"
    elif clock:
        used |= _span_tokens(s, clock.start(), clock.end())
        hour, minute, part = int(clock.group("h")), clock.group("m"), clock.group("part")
        suffix = {"सुबह": " am", "शाम": " pm", "रात": " pm", "दोपहर": " pm"}.get(part, "")
        when = f"at {hour}{':' + minute if minute else ''}{suffix}"
    message = " ".join(_trim([w for i, w in enumerate(orig) if i not in used]))
    parts = ["remind me", when, f"to {message}" if message else ""]
    return " ".join(p for p in parts if p)


def _note(orig, toks):
    s = " ".join(toks)
    verb = _NOTE_RE.search(s)
    if not verb:
        return None
    used = _span_tokens(s, verb.start(), verb.end())
    message = " ".join(_trim([w for i, w in enumerate(orig) if i not in used]))
    return f"take a note {message}".strip()


# ---------------------------------------------------------------- generic path
_TOPIC_JOIN = {"weather": "in", "temperature": "in", "will it rain": "in", "do i need an umbrella": "in", "aqi": "in",
               "pm": "in", "no2": "in", "is it safe to go out": "in", "do i need a mask": "in", "red zones": "in",
               "git status": "of", "news": "about"}
_POSTPOSITION_RE = re.compile(norm(r"^(?P<x>.+?) (?:का|की|के|में|मे) (?P<rest>.+)$"))
_CHAT_SIGNS = {norm(w) for w in "नहीं मत क्यों चाहता चाहती सोचो सोचते लगता समझाओ".split()}


def _translate_words(toks):
    """Replace command phrases, verbs and loanwords. Returns (tokens, verb, used_single_word_cmd)."""
    s = " ".join(toks)
    verb, weak = None, False

    def replace(match):
        nonlocal verb, weak
        english, kind = _ENTRY_MAP[match.group(1)]
        if kind == "verb":
            verb = verb or english
            return VERB_MARK
        if kind == "cmd" and match.group(1) in _SINGLE_WORD_CMDS:
            weak = True
        return english

    return _PHRASE_RE.sub(replace, s).split(), verb, weak


def _match_names(tokens):
    """Fuzzy-match leftover Devanagari words (and word pairs) to names Jarvis knows."""
    names = None
    out, i = [], 0
    while i < len(tokens):
        replaced = False
        for size in (2, 1):
            chunk = tokens[i:i + size]
            if len(chunk) < size or not all(has_devanagari(t) and t not in FUNCTION_WORDS for t in chunk):
                continue
            roman = to_roman(" ".join(chunk))
            if len(roman.replace(" ", "")) < 3:
                continue
            names = names if names is not None else _known_names()
            name = _fuzzy_name(roman, names)
            if name:
                out.append(name)
                i += size
                replaced = True
                break
        if not replaced:
            out.append(tokens[i])
            i += 1
    return out


def _roman_object(text):
    words = [w for w in text.split() if w not in FUNCTION_WORDS]
    return " ".join(romanize_word(w) if has_devanagari(w) else w for w in words)


def _generic(toks):
    tokens, verb, weak = _translate_words(toks)
    tokens = _match_names(tokens)
    content = [t for t in tokens if has_devanagari(t) and t not in FUNCTION_WORDS]
    if any(t in _CHAT_SIGNS for t in content) or len(content) > 3 or (weak and len(content) > 2):
        return None  # sounds like conversation, not a command: let the AI have the original
    times = [t for t in tokens if t in _TIME_WORDS]
    tokens = [t for t in tokens if t not in FILLERS and t not in _TIME_WORDS]
    s = " ".join(tokens).strip()

    if verb:
        obj = " ".join(t for t in s.split() if t != VERB_MARK).strip()
        obj = re.sub(norm(r"^(?:को|के|का|की) |(?: को)$"), "", obj).strip()
        site = re.match(norm(r"^(youtube|google) (?:पर|पे|में) (.+)$"), obj)
        in_vscode = re.match(norm(r"^(.+?) (?:vs code|code) (?:में|मे)$"), obj)
        if verb in ("search", "play") and site:
            where, query = site.groups()
            result = f"{verb} {query} on youtube" if where == "youtube" else f"search {query}"
        elif verb == "open" and in_vscode:
            result = f"open {_roman_object(in_vscode.group(1))} in vs code"
        elif verb == "play" and "project" in obj.split():
            result = f"run {_roman_object(obj)}"  # "जार्विस प्रोजेक्ट चलाओ" means run, not YouTube
        elif verb in ("open", "close", "run", "pull"):
            result = f"{verb} {_roman_object(obj)}"  # app names must be typeable ASCII
        else:
            result = f"{verb} {obj}"  # play/search: YouTube and Google understand Devanagari queries
    else:
        result = s
        post = _POSTPOSITION_RE.match(s)
        if post:
            rest = post.group("rest").strip()
            topic = next((t for t in sorted(_TOPIC_JOIN, key=len, reverse=True) if rest.startswith(t)), None)
            if topic:
                place = post.group("x").strip()
                if _TOPIC_JOIN[topic] == "of":
                    place = _roman_object(place)
                result = f"{rest} {_TOPIC_JOIN[topic]} {place}"
    result = " ".join([result, *times]).strip()
    return re.sub(r"\s+", " ", result) or None


def normalize_command(text):
    """Rewrite a Devanagari command into English for the skills. Non-Devanagari text is returned unchanged."""
    if not has_devanagari(text):
        return text
    orig = tokenize(text)
    toks = [norm(t) for t in orig]
    if not toks:
        return text
    for rule in (_whatsapp, _reminder, _note):  # WhatsApp first: its message may contain "याद दिलाना" or "नोट"
        result = rule(orig, toks)
        if result:
            return result
    return _generic(toks) or text
