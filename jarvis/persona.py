"""How Jarvis talks: PERSONALITY=friend (casual Delhi Hinglish, warm, a bit witty) or professional.

    persona.line("done")                      -> "Ho gaya!" / "Done, yaar" / "Lo, kar diya" (friend)
    persona.line("reminder", message="...")   -> "Oye Anjali! Yaad hai na, ..."
    persona.restyle("Okay, I've kept your notes.") -> "Theek hai, I've kept your notes." (friend; other replies)

Every phrase has 3-5 variants per personality, picked at random without saying the same one twice in a row.
The professional ones start with Jarvis's original wording. "talk professionally" / "friend mode" switch for
the session (set_mode); PERSONALITY and NICKNAME in .env set the default.

Hindi verbs about Jarvis itself follow the voice: a female voice (the default, en-IN-NeerjaNeural) says
"dekhti hoon", a male one "dekhta hoon". Placeholders: {ta} ta/ti, {ga} ga/gi, {a} a/i, {name} the nickname,
{assistant} Jarvis's name.
"""
import random
import re
from datetime import datetime

import config

FRIEND, PROFESSIONAL = "friend", "professional"
_session_mode = None        # "talk professionally" / "friend mode" for this session
_last = {}                  # key -> the variant said last time, so it doesn't repeat
choose = random.choice      # tests pin this to the first variant
AVOID_REPEAT = True         # tests turn this off
MALE_VOICES = ("prabhat", "madhur", "kunal", "rehaan", "aarav", "david", "mark", "ravi", "hemant", "guy", "davis")

PHRASES = {
    # ---------------------------------------------------------------- greetings and the main loop
    "online_morning": {
        PROFESSIONAL: ["Good morning, {name}. {assistant} is online."],
        FRIEND: ["Good morning {name}! Chai pi li?", "Morning {name}! Aaj ka kya plan hai?",
                 "Uth gayi? Good morning {name}!", "Good morning! Bata {name}, aaj kya karna hai?"],
    },
    "online_afternoon": {
        PROFESSIONAL: ["Good afternoon, {name}. {assistant} is online."],
        FRIEND: ["Arre aa gayi! Bata kya scene hai", "Hey {name}! Lunch hua?", "Haan {name}, bol kya chal raha hai?"],
    },
    "online_evening": {
        PROFESSIONAL: ["Good evening, {name}. {assistant} is online."],
        FRIEND: ["Good evening {name}! Din kaisa raha?", "Arre aa gayi! Bata kya scene hai",
                 "Hey {name}! Shaam ho gayi, chai time?"],
    },
    "online_night": {
        PROFESSIONAL: ["Good evening, {name}. {assistant} is online."],
        FRIEND: ["Itni raat ko? Bata {name}, kya chahiye", "Arre, abhi tak jaag rahi ho? Bolo {name}",
                 "Hey {name}! Late night mode on?"],
    },
    "hello": {
        PROFESSIONAL: ["Good {part_of_day}, {name}. How can I help?", "Hello, {name}. What can I do for you?",
                       "Hi {name}. How can I help?"],
        FRIEND: ["Haan {name}, bata!", "Hey! Kya scene hai?", "Haan bolo, kya karna hai?", "Arre {name}! Bol na"],
    },
    "yes": {
        PROFESSIONAL: ["Yes?", "I'm listening.", "Go ahead."],
        FRIEND: ["Haan, bolo?", "Haan bata", "Bolo bolo", "Haan ji?"],
    },
    "back": {
        PROFESSIONAL: ["I'm back. Yes?", "I'm awake. How can I help?", "Back again. What do you need?"],
        FRIEND: ["Aa gay{a} wapas! Bolo?", "Haan, jaag gay{a}. Bata", "Main hazir hoon! Kya karna hai?"],
    },
    "didnt_catch": {
        PROFESSIONAL: ["Sorry, I didn't catch that.", "Sorry, could you say that again?", "I didn't quite hear that."],
        FRIEND: ["Kya bola? Phir se bol na", "Sorry yaar, sunai nahi diya", "Ek baar aur bolna?",
                 "Hain? Phir se bolo na"],
    },
    "dont_know": {
        PROFESSIONAL: ["Sorry, I don't know how to do that yet.", "I can't do that yet, sorry.",
                       "That's not something I can do yet."],
        FRIEND: ["Yeh abhi mujhe nahi aata yaar. Kuch aur bol?", "Hmm, yeh toh nahi kar sak{ta} abhi.",
                 "Sorry yaar, yeh mere bas ka nahi hai abhi."],
    },
    "goodbye": {
        PROFESSIONAL: ["Goodbye, {name}. Stay safe!", "Goodbye, {name}. Take care.", "See you later, {name}."],
        FRIEND: ["Chal bye, take care!", "Milte hain, kuch chahiye toh bula lena", "Bye {name}! Apna khayal rakhna",
                 "Chalo, phir milte hain!"],
    },
    "goodbye_short": {
        PROFESSIONAL: ["Goodbye, {name}."],
        FRIEND: ["Chal bye, take care!", "Bye {name}!", "Milte hain!"],
    },
    "sleep": {
        PROFESSIONAL: ["Okay, going to sleep. Say {assistant} when you need me.",
                       "Going quiet. Say {assistant} when you need me."],
        FRIEND: ["Theek hai, so rah{a} hoon. {assistant} bolna jab zaroorat ho",
                 "Chal, thoda aaram. Bula lena!", "Okay, chup ho gay{a}. {assistant} bolke utha lena"],
    },
    "one_sec": {
        PROFESSIONAL: ["One sec."],
        FRIEND: ["Ek sec...", "Ruk, dekh{ta} hoon", "Haan, ek minute"],
    },
    # ---------------------------------------------------------------- doing things
    "done": {
        PROFESSIONAL: ["Done.", "All done.", "That's done."],
        FRIEND: ["Ho gaya!", "Done, yaar", "Lo, kar diya", "Ho gaya, sahi hai!"],
    },
    "okay": {  # replaces a leading "Okay, " in other replies
        PROFESSIONAL: ["Okay, "],
        FRIEND: ["Theek hai, ", "Chal, ", "Haan, ", "Okay, "],
    },
    "sorry": {
        PROFESSIONAL: ["Sorry, "],
        FRIEND: ["Sorry yaar, ", "Arre sorry, ", "Oops, "],
    },
    "never_mind": {
        PROFESSIONAL: ["Okay, never mind.", "Okay, cancelled.", "No problem, never mind."],
        FRIEND: ["Koi nahi, chhodo.", "Theek hai, rehne do.", "Chal, cancel."],
    },
    "confirm_action": {
        PROFESSIONAL: ["Just to check: {action}. Shall I go ahead?", "Before I do it: {action}. Okay?",
                       "{action}. Should I go ahead?"],
        FRIEND: ["Ek sec, {action}. Pakka kar doon?", "{action}, sahi? Haan bolo toh kar dun{ga}",
                 "Confirm kar do: {action}. Kar doon?"],
    },
    "not_done": {
        PROFESSIONAL: ["Okay, I didn't do it.", "Okay, I've left it.", "Alright, cancelled."],
        FRIEND: ["Theek hai, nahi kiya.", "Chal, rehne dete hain.", "Okay, chhod diya."],
    },
    "confirm_power": {
        PROFESSIONAL: ["Are you sure you want to {action} the laptop in {delay}?"],
        FRIEND: ["Pakka {action} kar doon? {delay} mein", "Sach mein laptop {action} kar doon, {delay} mein?",
                 "{delay} mein {action}, pakka?"],
    },
    "confirm_clear_notes": {
        PROFESSIONAL: ["Are you sure you want to delete all {count}?"],
        FRIEND: ["Pakka saare {count} uda doon?", "Sab {count} delete? Sure ho na?", "Saare {count} hata doon, pakka?"],
    },
    "error": {
        PROFESSIONAL: ["Sorry, something went wrong while doing that."],
        FRIEND: ["Oops, kuch gadbad ho gayi yaar. Ek baar phir try karein?", "Arre, kuch toh garbar hai. Phir se bolo?",
                 "Sorry yaar, yeh atak gaya. Dobara try karte hain?"],
    },
    "network_error": {
        PROFESSIONAL: ["I couldn't reach the online service right now. Please check your internet and try again."],
        FRIEND: ["Internet se connect nahi ho pa raha yaar. Wifi check kar le?",
                 "Net nahi chal raha lagta hai. Ek baar wifi dekh lo?",
                 "Online service tak pahunch nahi paya. Internet theek hai na?"],
    },
    # ---------------------------------------------------------------- notes, reminders, timers
    "noted": {
        PROFESSIONAL: ["Noted: {text}."],
        FRIEND: ["Likh liya: {text}", "Note kar liya: {text}", "Done, note mein daal diya: {text}"],
    },
    "reminder": {
        PROFESSIONAL: ["Reminder, {name}: {message}"],
        FRIEND: ["Oye {name}! Yaad hai na, {message}", "Arre {name}, {message} ka time ho gaya!",
                 "{name}, reminder: {message}", "Hello {name}! {message}, bhoolna mat"],
    },
    "timer_done": {
        PROFESSIONAL: ["Time's up, {name}! Your {message} timer is done."],
        FRIEND: ["Time up {name}! {message} ka timer khatam", "Ding ding! {message} timer ho gaya",
                 "Oye {name}, {message} ka time ho gaya!"],
    },
    "reminder_set": {
        PROFESSIONAL: ["Okay, I'll remind you {when}: {message}."],
        FRIEND: ["Done, {when} yaad dila dun{ga}: {message}", "Pakka, {when} bol dun{ga}: {message}",
                 "Chal, {when} remind kar dun{ga}: {message}"],
    },
    "timer_set": {
        PROFESSIONAL: ["Timer set for {duration}."],
        FRIEND: ["Chal, {duration} ka timer laga diya", "{duration} ka timer on!", "Lo, {duration} ka timer chalu"],
    },
    # ---------------------------------------------------------------- small talk
    "thanks": {
        PROFESSIONAL: ["You're welcome!", "Happy to help.", "Any time, {name}."],
        FRIEND: ["Arre koi baat nahi!", "Anytime yaar", "Mention not!", "Bas kar, sharma jaun{ga}"],
    },
    "how_are_you": {
        PROFESSIONAL: ["I'm running smoothly, thanks for asking, {name}. How about you?",
                       "All systems online and feeling great. What can I do for you?"],
        FRIEND: ["Ekdum mast! Tu bata?", "Badhiya yaar, tu suna?", "Main toh chill hoon. Tera kya haal?"],
    },
    "who_are_you": {
        PROFESSIONAL: ["I'm {assistant}, your personal voice assistant. I can check air quality and weather, open apps, "
                       "set reminders and much more. Say help to hear it all."],
        FRIEND: ["Main {assistant}, tera digital dost! Reminders, weather, music, web search, sab kar le{ta} hoon. "
                 "Help bol, list suna dun{ga}.",
                 "{assistant} hoon yaar, tera apna assistant. Kuch bhi bol, try kar{ta} hoon."],
    },
    "checking_online": {
        PROFESSIONAL: ["Checking online."],
        FRIEND: ["Ruk, online dekh{ta} hoon", "Ek sec, internet pe check kar{ta} hoon", "Dekh{ta} hoon online"],
    },
    "nothing_found": {
        PROFESSIONAL: ["I couldn't find anything online about that right now. Please try again in a bit."],
        FRIEND: ["Yaar, online kuch mila nahi abhi. Thodi der mein try karein?",
                 "Hmm, internet pe kuch nahi mila. Baad mein dekhte hain?",
                 "Abhi online kuch haath nahi laga yaar. Ek minute baad phir poochna?"],
    },
    # ---------------------------------------------------------------- how you're feeling
    "feel_tired": {
        PROFESSIONAL: ["Sounds like a long day. Maybe take a short break and drink some water.",
                       "You've earned a rest. A short break might help."],
        FRIEND: ["Arre, thak gayi? Thoda break le le yaar, pani pi.", "Chal 10 minute aankh band kar le, main hoon na.",
                 "Bas, ab laptop band karke aaram kar thoda. Music laga doon?"],
    },
    "feel_bored": {
        PROFESSIONAL: ["Want me to play some music or tell you a joke?", "How about some music, or a joke?"],
        FRIEND: ["Bore ho rahi ho? Chal gaana lagaun ya joke sunaun?", "Boring scene hai? Ek joke sun, mood ban jayega.",
                 "Arre, music laga doon? Bas bol 'play' aur gaane ka naam."],
    },
    "feel_stressed": {
        PROFESSIONAL: ["That sounds stressful. Take a few slow breaths; one thing at a time.",
                       "Try a short break and a few deep breaths. You've got this."],
        FRIEND: ["Arre, tension mat le yaar. Ek gehri saans le, ek ek kaam karte hain.",
                 "Chill kar, sab ho jayega. 5 minute ka break le le?",
                 "Itna load mat le. Bata kya pareshan kar raha hai, saath mein dekhte hain."],
    },
    "feel_sad": {
        PROFESSIONAL: ["I'm sorry you're feeling low. Want some music, or to talk about it?",
                       "That's tough. I'm here if you want to talk."],
        FRIEND: ["Arre kya hua yaar? Bata, main sun{ta} hoon.", "Udaas mat ho. Tera favourite gaana lagaun?",
                 "Hug bhej rah{a} hoon! Baat karni hai toh bol."],
    },
    "feel_happy": {
        PROFESSIONAL: ["That's great to hear!", "Glad to hear it. Enjoy!"],
        FRIEND: ["Waah, sahi hai! Party kab hai?", "Arre wah! Aise hi khush reh.", "Badhiya yaar! Kya hua, bata bata?"],
    },
    "feel_hungry": {
        PROFESSIONAL: ["Maybe time for a snack.", "Sounds like it's time to eat something."],
        FRIEND: ["Toh kuch kha le yaar! Maggi bana le?", "Pet pooja ka time! Kuch order karein?",
                 "Bhookh lagi? Chal kuch kha ke aa, main yahin hoon."],
    },
    "late_night": {
        PROFESSIONAL: ["It's past {time}. Don't forget to get some sleep.", "It's getting late. Maybe call it a night?"],
        FRIEND: ["Waise {time} baj gaye, so ja ab. Kal baat karte hain", "Raat ke {time} ho gaye! Neend nahi aa rahi kya?",
                 "Oye, {time} baj gaye. Thoda so le yaar"],
    },
    # ---------------------------------------------------------------- personality switch
    "mode_professional": {
        PROFESSIONAL: ["Okay, I'll keep it professional."],
        FRIEND: ["Okay, I'll keep it professional."],
    },
    "mode_friend": {
        PROFESSIONAL: ["Arre wah, dost mode wapas on! Bol kya scene hai"],
        FRIEND: ["Arre wah, dost mode wapas on! Bol kya scene hai", "Haan yaar, wapas aa gay{a}! Bata"],
    },
    "already_professional": {PROFESSIONAL: ["I'm already in professional mode."],
                             FRIEND: ["I'm already in professional mode."]},
    "already_friend": {PROFESSIONAL: ["Main toh already tera dost hoon yaar!"],
                       FRIEND: ["Main toh already tera dost hoon yaar!", "Dost hi toh hoon, aur kya chahiye?"]},
}

# AQI by category (friend mode; professional keeps the original advice text)
AQI_FRIEND = {
    "Good": ["{place} mein AQI {aqi} hai, ekdum fresh hawa! Bahar ja sakti ho.",
             "AQI {aqi} in {place}. Hawa mast hai aaj, walk pe nikal ja!"],
    "Satisfactory": ["{place} mein AQI {aqi} hai, theek-thaak. Bahar jaana chalega.",
                     "AQI {aqi}, {place}. Thik hai, bas zyada der bahar mat rukna agar asthma hai."],
    "Moderate": ["{place} mein AQI {aqi} hai, moderate. Bachche ya asthma wale mask pehen lein.",
                 "AQI {aqi} in {place}. Itna bura nahi, par mask saath rakh lo."],
    "Poor": ["Bahar mat ja yaar, AQI {aqi} hai. Mask pehen ke hi nikalna",
             "{place} mein AQI {aqi}, poor hai. N95 pehen ke jaana, exercise ghar pe kar le."],
    "Very Poor": ["Bahar mat ja yaar, AQI {aqi} hai. Mask pehen ke hi nikalna",
                  "AQI {aqi} in {place}, bahut kharab. Khidkiyan band rakh aur N95 ke bina mat nikalna."],
    "Severe": ["AQI {aqi} hai {place} mein, severe! Ghar pe hi reh yaar, purifier chala de.",
               "Bilkul bahar mat jaana, AQI {aqi} hai. Nikalna pade toh N95 pakka."],
}

_FEELINGS = {
    "tired": r"\b(?:i'?m|i am|feeling|feel|so|very)\s+(?:so\s+|very\s+|really\s+|a bit\s+|kinda\s+)?(?:tired|exhausted|sleepy|drained)\b|"
             r"\bthak (?:gayi|gaya|gai|gya)\b|\bthaki (?:hui|hu)\b|\bneend aa rahi\b",
    "bored": r"\b(?:i'?m|i am|feeling|feel|so)\s+(?:so\s+|very\s+|really\s+|a bit\s+|kinda\s+)?bored\b|\bbore ho (?:rahi|raha|rhi|rha)\b|"
             r"\bboring (?:lag|ho) raha\b",
    "stressed": r"\b(?:i'?m|i am|feeling|feel|so)\s+(?:so\s+|very\s+|really\s+|a bit\s+|kinda\s+)?(?:stressed|anxious|tense|overwhelmed|"
                r"worried)\b|\btension (?:ho rahi|hai)\b|\bstress (?:ho raha|hai)\b",
    "sad": r"\b(?:i'?m|i am|feeling|feel)\s+(?:so\s+|very\s+|really\s+|a bit\s+|kinda\s+)?(?:sad|low|down|upset|lonely)\b|\budaas\b|"
           r"\bmood (?:off|kharab)\b",
    "happy": r"\b(?:i'?m|i am|feeling|feel)\s+(?:so\s+|very\s+|really\s+|a bit\s+|kinda\s+)?(?:happy|great|awesome|excited|amazing)\b|"
             r"\bbahut khush\b|\bmaza aa (?:gaya|raha)\b",
    "hungry": r"\b(?:i'?m|i am|feeling)\s+(?:so\s+|very\s+|really\s+|a bit\s+|kinda\s+)?(?:hungry|starving)\b|\bbhook?h lagi\b",
}
FEELINGS = {name: re.compile(pattern) for name, pattern in _FEELINGS.items()}


# ---------------------------------------------------------------- settings
def mode():
    chosen = _session_mode or (config.PERSONALITY or FRIEND).lower()
    return PROFESSIONAL if chosen.startswith("pro") or chosen == "formal" else FRIEND


def set_mode(new_mode):
    global _session_mode
    _session_mode = new_mode


def is_friend():
    return mode() == FRIEND


def nickname():
    return (config.NICKNAME or config.USER_NAME) if is_friend() else config.USER_NAME


def voice_is_female():
    voice = (config.TTS_VOICE if config.TTS_ENGINE == "edge" else config.VOICE_NAME) or ""
    return not any(name in voice.lower() for name in MALE_VOICES)


def _verb_forms():
    female = voice_is_female()
    return {"ta": "ti" if female else "ta", "ga": "gi" if female else "ga", "a": "i" if female else "a"}


def part_of_day(now=None):
    hour = (now or datetime.now()).hour
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening"


# ---------------------------------------------------------------- picking a line
def _pick(key, options):
    if AVOID_REPEAT and len(options) > 1 and _last.get(key) in options:
        options = [o for o in options if o != _last[key]]
    picked = choose(options)
    _last[key] = picked
    return picked


def variants(key, which=None):
    entry = PHRASES[key]
    return entry.get(which or mode()) or entry[PROFESSIONAL]


def line(key, **values):
    """A random variant of phrase `key` in the current personality, filled in."""
    template = _pick(key, variants(key))
    fields = {"name": nickname(), "assistant": config.ASSISTANT_NAME, "part_of_day": part_of_day(), **_verb_forms(),
              **values}
    return template.format(**fields)


def is_line(key, text):
    """True if `text` is one of phrase `key`'s variants (in either personality)."""
    fields = {"assistant": config.ASSISTANT_NAME, "part_of_day": part_of_day(), **_verb_forms()}
    return any(text == t.format(name=name, **fields) for options in PHRASES[key].values() for t in options
               for name in {config.USER_NAME, nickname()})


def online_greeting(now=None):
    now = now or datetime.now()
    if is_friend() and (now.hour >= 22 or now.hour < 5):
        return line("online_night")
    return line("online_" + part_of_day(now))


def aqi_line(place, aqi, category_name, advice):
    """The AQI reply: the original summary and advice, or a friend's take on it."""
    if not is_friend():
        return None
    options = AQI_FRIEND.get(category_name)
    if not options:
        return None
    return _pick("aqi_" + category_name, options).format(place=place, aqi=aqi)


def feeling(text):
    """'tired', 'bored', ... if the user says how they feel, else None."""
    for name, pattern in FEELINGS.items():
        if pattern.search(text or ""):
            return name
    return None


# ---------------------------------------------------------------- other replies, friend style
_OKAY = re.compile(r"^(?:okay|ok)[,.]\s+", re.IGNORECASE)
_DONE = re.compile(r"^done\.\s*", re.IGNORECASE)
_SORRY = re.compile(r"^sorry,\s+", re.IGNORECASE)


def restyle(text):
    """Give a fixed skill reply a friend's opener ("Okay, ..." -> "Chal, ...", "Done. X" -> "Ho gaya! X").
    Professional mode, or a reply without one of those openers, is returned unchanged."""
    if not text or not is_friend():
        return text
    if _DONE.match(text):
        rest = _DONE.sub("", text, count=1)
        return line("done") + (" " + rest if rest else "")
    if _OKAY.match(text):
        rest = _OKAY.sub("", text, count=1)
        if not re.match(r"I\b|[A-Z]{2}|[A-Z][a-z]*[A-Z]", rest):  # keep "I", "AQI", "WhatsApp" as they are
            rest = rest[:1].lower() + rest[1:]
        return line("okay") + rest
    if _SORRY.match(text):
        rest = _SORRY.sub("", text, count=1)
        return line("sorry") + rest
    return text


# ---------------------------------------------------------------- the AI's style
def ai_style():
    """Added to the AI's system prompts."""
    if not is_friend():
        return ("Tone: polite, clear and professional, like a capable personal assistant. ")
    me = "female" if voice_is_female() else "male"
    verbs = "'main dekhti hoon', 'bata dungi'" if me == "female" else "'main dekhta hoon', 'bata dunga'"
    return (
        f"Tone: talk like {nickname()}'s close friend from Delhi, not a formal assistant. Casual and warm, a bit witty; "
        "light teasing is fine, never rude. Short replies, like a friend talking, not a manual. "
        "Mix English and Hindi naturally in Roman letters, the way Delhi friends talk (arre, yaar, chal, haan bata, "
        "sahi hai, done!), but don't overdo the slang and keep facts in plain words. "
        f"You are {me}: use {me} Hindi verb forms for yourself, e.g. {verbs}. "
        "Still be honest: say so when the user is wrong or when you don't know, and never make up facts. "
    )


# ---------------------------------------------------------------- late at night
LATE_FROM, LATE_TO = 1, 5   # after 1 am, before 5 am
LATE_EVERY = 45 * 60        # at most once every 45 minutes
LATE_CHANCE = 0.35


def late_night_nudge(state, now=None, chance=None):
    """Sometimes, after 1 am, a gentle 'go to sleep'. Returns the line or None."""
    import time

    now = now or datetime.now()
    if not (LATE_FROM <= now.hour < LATE_TO):
        return None
    last = state.get("late_nudge_at", 0)
    if time.time() - last < LATE_EVERY:
        return None
    if random.random() >= (LATE_CHANCE if chance is None else chance):
        return None
    state["late_nudge_at"] = time.time()
    return line("late_night", time=f"{now.hour % 12 or 12}")
