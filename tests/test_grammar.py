"""Hinglish gender agreement after the AI writes (jarvis/grammar.py): female forms for the user, male for Jarvis."""
import pytest
from helpers import make_ctx, openai_stream

import config
from jarvis import grammar, http
from jarvis.router import handle


# ---------------------------------------------------------------- female user, male Jarvis (the defaults)
@pytest.mark.parametrize("ai_wrote, spoken", [
    # talking to her: female forms
    ("tu kaisa hai?", "tu kaisi hai?"),
    ("Tu kaisa hai?", "Tu kaisi hai?"),
    ("Kaise ho tum?", "Kaisi ho tum?"),
    ("kaisa hai tu?", "kaisi hai tu?"),
    ("aap kaise hain?", "aap kaisi hain?"),
    ("tu chai piyega?", "tu chai piyegi?"),
    ("chai piyega?", "chai piyegi?"),
    ("khana khayega?", "khana khayegi?"),
    ("tu kya kar raha hai?", "tu kya kar rahi hai?"),
    ("tum kya kar rahe ho?", "tum kya kar rahi ho?"),
    ("thak gaya kya?", "thak gayi kya?"),
    ("tu thak gaya hoga", "tu thak gayi hogi"),
    ("tu karega na?", "tu karegi na?"),
    ("tum karoge?", "tum karogi?"),
    ("aap karenge?", "aap karengi?"),
    ("tu akela nahi hai yaar", "tu akeli nahi hai yaar"),
    ("tu kya sochta hai?", "tu kya sochti hai?"),
    ("tu kahan tha?", "tu kahan thi?"),
    ("arre tu aa gaya!", "arre tu aa gayi!"),
    ("kab aaya tu?", "kab aayi tu?"),
    ("tu ye kar sakta hai", "tu ye kar sakti hai"),
    ("bore ho gaya kya?", "bore ho gayi kya?"),
    ("tu bhookha hai kya?", "tu bhookhi hai kya?"),
    ("tu kaise karega ye?", "tu kaise karegi ye?"),            # "kaise" = how: untouched
    ("so gaya kya?", "so gayi kya?"),
    ("tu thaka hua lag raha hai", "tu thaki hui lag rahi hai"),
    ("tum aa rahe ho na?", "tum aa rahi ho na?"),
    # Jarvis about himself: male forms
    ("main kar dungi", "main kar dunga"),
    ("main aa gayi", "main aa gaya"),
    ("Samajh gayi main, bata dungi", "Samajh gaya main, bata dunga"),
    ("main sun rahi hoon", "main sun raha hoon"),
    ("sun rahi hoon, bol", "sun raha hoon, bol"),
    ("main tera wait karti hoon", "main tera wait karta hoon"),
    ("main bata sakti hoon", "main bata sakta hoon"),
    ("Abhi check karungi", "Abhi check karunga"),
    # both in one reply
    ("main chai bana dunga, tu piyega?", "main chai bana dunga, tu piyegi?"),
    ("tu aa gaya toh main chalti hoon", "tu aa gayi toh main chalta hoon"),
    ("main thak gaya hoon, tu bhi thak gayi hogi", "main thak gaya hoon, tu bhi thak gayi hogi"),
])
def test_female_user_male_jarvis(ai_wrote, spoken):
    assert grammar.fix(ai_wrote, user_female=True, me_female=False) == spoken


@pytest.mark.parametrize("text", [
    "tujhe pata hai?",                  # dative: agrees with the object
    "tune khana khaya?",                # ergative: agrees with "khana"
    "mujhe lagta hai tu sahi hai",
    "tujhe kya chahiye?",
    "kaam ho gaya",                     # impersonal
    "mausam kaisa hai aaj?",
    "match chalega?",
    "wo aa gaya",                       # third person
    "Rahul kya kar raha hai?",
    "khana ban gaya?",
    "The main reason is speed.",        # English
    "I will call you in five minutes.",
    "Pata nahi yaar, Google pe dekh lein?",
    "Godaan Munshi Premchand ne likha tha.",
])
def test_leaves_alone(text):
    assert grammar.fix(text, user_female=True, me_female=False) == text


# ---------------------------------------------------------------- the other settings
@pytest.mark.parametrize("ai_wrote, spoken", [
    ("tu kaisi hai?", "tu kaisa hai?"),
    ("tum kya kar rahi ho?", "tum kya kar rahe ho?"),
    ("thak gayi kya?", "thak gaya kya?"),
    ("aap karengi?", "aap karenge?"),
])
def test_male_user(ai_wrote, spoken):
    assert grammar.fix(ai_wrote, user_female=False, me_female=False) == spoken


@pytest.mark.parametrize("ai_wrote, spoken", [
    ("main kar dunga", "main kar dungi"),
    ("sun raha hoon", "sun rahi hoon"),
    ("main aa gaya, tu kaisa hai?", "main aa gayi, tu kaisi hai?"),
])
def test_female_voice(ai_wrote, spoken):
    assert grammar.fix(ai_wrote, user_female=True, me_female=True) == spoken


def test_defaults_follow_config(monkeypatch):
    monkeypatch.setattr(config, "USER_GENDER", "female")  # the voice is male in tests (en-IN-PrabhatNeural)
    assert grammar.fix("main kar dungi, tu kaisa hai?") == "main kar dunga, tu kaisi hai?"
    monkeypatch.setattr(config, "USER_GENDER", "male")
    assert grammar.fix("tu kaisi hai?") == "tu kaisa hai?"


def test_chat_answers_are_fixed_before_speaking(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    monkeypatch.setattr(http, "post_stream", lambda *a, **k: openai_stream("Main theek hoon yaar! Tu kaisa hai? "
                                                                          "Chai piyega?"))
    ctx = make_ctx()
    handle(ctx, "write me a two line hello")
    spoken = " ".join(ctx.voice.said)
    assert "Tu kaisi hai?" in spoken and "Chai piyegi?" in spoken and "kaisa" not in spoken
