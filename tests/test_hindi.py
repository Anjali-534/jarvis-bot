"""Hindi mode: Devanagari speech is rewritten into English commands before routing."""
import json
import urllib.parse

import pytest
from helpers import make_ctx

import config
from jarvis import http, translit
from jarvis.router import handle
from jarvis.skills import whatsapp
from jarvis.voice import speakable

HINDI_ROUTES = [
    # (what Google hi-IN returns, expected rewrite, expected skill)
    ("क्रोम खोलो", "open chrome", "open"),
    ("क्रोम और नोटपैड खोल दो", "open chrome and notepad", "open"),
    ("व्हाट्सएप ओपन करो", "open whatsapp", "open"),
    ("फिग्मा खोलो", "open figma", "open"),
    ("क्रोम बंद करो", "close chrome", "close"),
    ("अरिजीत सिंह के गाने बजाओ", "play अरिजीत सिंह के songs", "play youtube"),
    ("यूट्यूब पर लोफी म्यूज़िक सर्च करो", "search लोफी music on youtube", "youtube search"),
    ("गूगल पर बेस्ट लैपटॉप सर्च करो", "search बेस्ट laptop", "google"),
    ("आवाज़ बढ़ाओ", "volume up", "volume up"),
    ("आवाज कम करो", "volume down", "volume down"),       # without the nukta, as Google often writes it
    ("म्यूट करो", "mute", "mute"),
    ("अभी क्या टाइम है", "what time is it", "time"),
    ("समय क्या है", "what time is it", "time"),
    ("आज की तारीख क्या है", "what's the date", "date"),
    ("दिल्ली का मौसम कैसा है", "weather in दिल्ली", "weather"),
    ("कल बारिश होगी क्या", "will it rain tomorrow", "rain"),
    ("प्रदूषण कितना है", "aqi", "aqi"),
    ("रोहिणी में एक्यूआई क्या है", "aqi in रोहिणी", "aqi"),
    ("पीएम 2.5 कितना है", "pm 2.5", "pm2.5"),
    ("क्या बाहर जाना सुरक्षित है", "is it safe to go out", "safe outside"),
    ("10 मिनट में पानी पीने के लिए याद दिलाना", "remind me in 10 minutes to पानी पीने", "reminder"),
    ("शाम 6 बजे मम्मी को कॉल करने के लिए याद दिलाना", "remind me at 6 pm to मम्मी को कॉल करने", "reminder"),
    ("आधे घंटे बाद स्ट्रेच करने के लिए याद दिलाना", "remind me in half an hour to स्ट्रेच करने", "reminder"),
    ("नोट करो कि दूध लाना है", "take a note दूध लाना है", "take note"),
    ("माँ को व्हाट्सएप पर मैसेज भेजो कि मैं लेट हूँ", "send a whatsapp to mom saying मैं लेट हूँ", "whatsapp message"),
    ("माँ को बोलो कि खाना याद से खा लेना", "send a whatsapp to mom saying खाना याद से खा लेना", "whatsapp message"),
    ("माँ को व्हाट्सएप पर कॉल करो", "call mom on whatsapp", "whatsapp call"),
    ("स्क्रीनशॉट लो", "take a screenshot", "screenshot"),
    ("बैटरी कितनी है", "battery status", "battery"),
    ("लैपटॉप लॉक करो", "lock the laptop", "lock"),
    ("लैपटॉप बंद करो", "shut down the laptop", "power"),
    ("मदद", "help", "help"),
    ("सो जाओ", "go to sleep", "sleep"),
    ("स्विच टू इंग्लिश", "switch to english", "language"),
    ("जार्विस प्रोजेक्ट वीएस कोड में खोलो", "open jarvis project in vs code", "vscode open project"),
    ("जार्विस का गिट स्टेटस बताओ", "git status of jarvis", "git status"),
    ("क्रिकेट की खबरें सुनाओ", "news about cricket", "news"),
    ("एक चुटकुला सुनाओ", "tell me a joke", "joke"),
    ("मेरे प्रोजेक्ट्स दिखाओ", "list my projects", "vscode list projects"),
    ("नमस्ते", "hello", "greet"),
    ("धन्यवाद।", "thank you", "thanks"),                   # Hindi full stop is ignored
]


@pytest.mark.parametrize("hindi, english, skill_name", HINDI_ROUTES)
def test_hindi_rewrite(hindi, english, skill_name):
    assert translit.normalize_command(hindi) == english


@pytest.mark.parametrize("hindi, english, skill_name", HINDI_ROUTES)
def test_hindi_routing(recorded_skills, hindi, english, skill_name):
    assert handle(make_ctx(), hindi) == skill_name


@pytest.mark.parametrize("heard, expected", [
    ("जार्विस क्रोम खोलो", (True, "क्रोम खोलो")),
    ("हे जार्विस अभी क्या टाइम है", (True, "अभी क्या टाइम है")),
    ("जार्विस का गिट स्टेटस बताओ", (True, "जार्विस का गिट स्टेटस बताओ")),  # project named jarvis
    ("जार्विस प्रोजेक्ट वीएस कोड में खोलो", (True, "जार्विस प्रोजेक्ट वीएस कोड में खोलो")),
    ("jarvis project in vs code", (True, "jarvis project in vs code")),
])
def test_hindi_wake_word(heard, expected):
    from main import strip_wake_word

    assert strip_wake_word(heard) == expected


def test_understood_as_is_logged(recorded_skills, caplog):
    caplog.set_level("INFO")
    handle(make_ctx(), "क्रोम खोलो")
    assert "(understood as: open chrome)" in caplog.text


def test_english_text_is_untouched():
    for text in ("open chrome", "what's the aqi in rohini", "send a whatsapp to mom saying hi"):
        assert translit.normalize_command(text) == text


@pytest.mark.parametrize("chat", [
    "तुम्हें हिंदी आती है क्या",
    "मेरे पास समय नहीं है क्या करूं",       # contains "समय" (time) but is conversation
    "दिल्ली में सर्दियों के बारे में दो लाइन की कविता लिखो",
])
def test_hindi_chat_goes_to_ai_with_original_text(monkeypatch, chat):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    sent = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "Haan, bilkul!"}}]}

    monkeypatch.setattr(http, "post_json", lambda url, payload, **k: sent.append(payload) or Response())
    ctx = make_ctx()
    assert handle(ctx, chat) == "ai"
    assert sent[0]["messages"][-1]["content"] == chat


def test_whatsapp_message_text_is_sent_exactly_as_spoken(monkeypatch):
    config.CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CONTACTS_FILE.write_text(json.dumps({"mom": "919876543210", "dhruv": "919812345678"}), encoding="utf-8")
    opened = []
    monkeypatch.setattr(whatsapp, "whatsapp_installed", lambda: True)
    monkeypatch.setattr(whatsapp, "open_uri", opened.append)
    monkeypatch.setattr(whatsapp, "press_enter", lambda: opened.append("ENTER"))
    ctx = make_ctx(["हाँ"])
    handle(ctx, "माँ को व्हाट्सएप पर मैसेज भेजो कि मैं लेट हूँ")
    assert ctx.voice.said[0] == "Send 'मैं लेट हूँ' to Mom?"
    assert opened == [f"whatsapp://send?phone=919876543210&text={urllib.parse.quote('मैं लेट हूँ')}", "ENTER"]
    assert ctx.voice.last == "Sent to Mom."


def test_whatsapp_contact_fuzzy_from_devanagari(monkeypatch):
    config.CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CONTACTS_FILE.write_text(json.dumps({"dhruv": "919812345678"}), encoding="utf-8")
    assert translit.normalize_command("ध्रुव को मैसेज भेजो कि डेमो 5 बजे है") == \
        "send a whatsapp to dhruv saying डेमो 5 बजे है"
    ctx = make_ctx(["नहीं"])
    handle(ctx, "ध्रुव को मैसेज भेजो कि डेमो 5 बजे है")
    assert ctx.voice.last == "Okay, I didn't send it."   # "नहीं" is a no


def test_hindi_reminder_keeps_message_and_fires():
    ctx = make_ctx()
    assert handle(ctx, "1 सेकंड में स्ट्रेच करने के लिए याद दिलाना") == "reminder"
    assert ctx.state["reminders"][0]["text"] == "स्ट्रेच करने"


@pytest.mark.parametrize("answer, yes", [
    ("हाँ", True), ("हां", True), ("हाँ जी", True), ("ठीक है", True), ("हाँ भेज दो", True),
    ("नहीं", False), ("नहीं मत भेजो", False), ("रहने दो", False), ("", False),
])
def test_hindi_confirmations(answer, yes):
    from jarvis.router import is_yes

    assert is_yes(answer) is yes


def test_romanizer():
    assert translit.to_roman("मैं लेट हूँ") == "main let hun"
    assert translit.to_roman("ध्रुव जार्विस रिया") == "dhruv jarvis riya"
    assert translit.to_roman("open क्रोम") == "open krom"


def test_spoken_text_is_romanized():
    assert speakable("Send 'मैं लेट हूँ' to Mom?") == "Send 'main let hun' to Mom?"
    assert speakable("Plain English stays.") == "Plain English stays."
