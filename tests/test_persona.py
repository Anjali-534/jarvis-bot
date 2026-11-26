"""Friend / professional personality, phrase variants, and the neural voice with its offline fallback."""
import random
import threading
import time
from datetime import datetime

import pytest
from helpers import make_ctx

import config
from jarvis import persona, tts, voice
from jarvis.router import handle


@pytest.fixture
def friend(monkeypatch):
    monkeypatch.setattr(config, "PERSONALITY", "friend")
    return persona


@pytest.fixture
def varied(monkeypatch):
    """Real random choice, without repeats."""
    monkeypatch.setattr(persona, "choose", random.choice)
    monkeypatch.setattr(persona, "AVOID_REPEAT", True)
    monkeypatch.setattr(persona, "_last", {})


def friend_lines(key, **values):
    fields = {"name": persona.nickname(), "assistant": config.ASSISTANT_NAME, "part_of_day": persona.part_of_day(),
              **persona._verb_forms(), **values}
    return {t.format(**fields) for t in persona.PHRASES[key][persona.FRIEND]}


# ---------------------------------------------------------------- variants
MAIN_KEYS = ["hello", "yes", "back", "didnt_catch", "dont_know", "goodbye", "sleep", "done", "never_mind",
             "confirm_action", "not_done", "confirm_power", "confirm_clear_notes", "error", "network_error", "noted",
             "reminder", "timer_done", "reminder_set", "timer_set", "thanks", "how_are_you", "checking_online",
             "nothing_found", "feel_tired", "feel_bored", "feel_stressed", "feel_sad", "feel_happy", "feel_hungry",
             "late_night", "online_morning", "online_afternoon", "online_evening", "online_night"]


@pytest.mark.parametrize("key", MAIN_KEYS)
def test_every_friend_phrase_has_3_to_5_variants(key):
    assert 3 <= len(persona.PHRASES[key][persona.FRIEND]) <= 5
    assert persona.PHRASES[key][persona.PROFESSIONAL]


@pytest.mark.parametrize("tts_voice, wanted, unwanted", [("hi-IN-SwaraNeural", "dungi", "dunga"),
                                                         ("en-IN-PrabhatNeural", "dunga", "dungi")])
def test_every_phrase_renders_for_both_voice_genders(monkeypatch, tts_voice, wanted, unwanted):
    import re as regex

    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    monkeypatch.setattr(config, "TTS_VOICE", tts_voice)
    values = dict(name="Meera", assistant="Jarvis", part_of_day="morning", when="in 10 minutes", message="m",
                  action="restart", delay="15 seconds", duration="5 minutes", text="t", count="2 notes", time="2",
                  alternative="Google pe dhoondh doon", day="12 February", every="", memory="m", topic="bike", old="o", new="n", folder="Documents", 
                  **persona._verb_forms())
    rendered = " ".join(t.format(**values) for entry in persona.PHRASES.values() for options in entry.values()
                        for t in options)
    assert "{" not in rendered and wanted in rendered and unwanted not in rendered
    assert not regex.search(r"\bd(?:ga|gi)\b", rendered)


def test_professional_first_variants_are_the_original_wording():
    assert persona.PHRASES["didnt_catch"][persona.PROFESSIONAL][0] == "Sorry, I didn't catch that."
    assert persona.PHRASES["goodbye"][persona.PROFESSIONAL][0] == "Goodbye, {name}. Stay safe!"
    assert persona.PHRASES["checking_online"][persona.PROFESSIONAL][0] == "Checking online."


def test_variants_dont_repeat_back_to_back(friend, varied):
    said = [persona.line("done") for _ in range(30)]
    assert all(a != b for a, b in zip(said, said[1:]))
    assert len(set(said)) >= 3  # it really varies
    assert set(said) <= friend_lines("done")


def test_both_personalities_and_the_nickname(monkeypatch):
    monkeypatch.setattr(config, "NICKNAME", "Anju")
    assert persona.online_greeting(datetime(2026, 11, 25, 8)) == "Good morning, Meera. Jarvis is online."
    monkeypatch.setattr(config, "PERSONALITY", "friend")
    morning = persona.online_greeting(datetime(2026, 11, 25, 8))
    assert morning in friend_lines("online_morning") and morning == "Good morning Anju! Chai pi li?"
    assert persona.online_greeting(datetime(2026, 11, 25, 15)) == "Arre aa gayi! Bata kya scene hai"
    assert persona.online_greeting(datetime(2026, 11, 25, 23, 30)) in friend_lines("online_night")


def test_verb_forms_follow_the_voice(friend, monkeypatch):
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    monkeypatch.setattr(config, "TTS_VOICE", "en-IN-NeerjaNeural")
    assert persona.line("checking_online") == "Ruk, online dekhti hoon"
    monkeypatch.setattr(config, "TTS_VOICE", "en-IN-PrabhatNeural")
    assert persona.line("checking_online") == "Ruk, online dekhta hoon"
    assert "'main kar dunga', 'main aa gaya'" in persona.ai_style()


def test_restyle_gives_other_replies_a_friends_opener(friend):
    assert persona.restyle("Okay, I've kept your notes.") == "Theek hai, I've kept your notes."
    assert persona.restyle("Okay. The laptop will restart in 15 seconds.") == \
        "Theek hai, the laptop will restart in 15 seconds."
    assert persona.restyle("Done. I deleted 3 notes.") == "Ho gaya! I deleted 3 notes."
    assert persona.restyle("Sorry, I couldn't find Rohini.") == "Sorry yaar, I couldn't find Rohini."
    assert persona.restyle("Battery is at 80 percent.") == "Battery is at 80 percent."
    persona.set_mode(persona.PROFESSIONAL)
    try:
        assert persona.restyle("Okay, I've kept your notes.") == "Okay, I've kept your notes."
    finally:
        persona.set_mode(None)


# ---------------------------------------------------------------- persona in skill replies
def test_friend_mode_in_skill_replies(friend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "NOTES_FILE", tmp_path / "notes.txt")
    ctx = make_ctx()
    handle(ctx, "thank you")
    assert ctx.voice.last == "Arre koi baat nahi!"
    handle(ctx, "take a note buy milk")
    assert ctx.voice.last == "Likh liya: buy milk"
    handle(ctx, "hello")
    assert ctx.voice.last == "Haan Meera, bata!"
    handle(ctx, "goodbye")
    assert ctx.voice.last == "Chal bye, take care!"


def test_professional_mode_in_skill_replies(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOTES_FILE", tmp_path / "notes.txt")
    ctx = make_ctx()
    handle(ctx, "take a note buy milk")
    assert ctx.voice.last == "Noted: buy milk."
    handle(ctx, "goodbye")
    assert ctx.voice.last == "Goodbye, Meera. Stay safe!"


def test_friendly_reminder_and_risky_confirmation(friend, monkeypatch):
    from jarvis.skills import notes

    ctx = make_ctx()
    entry = notes.schedule(ctx, 0.01, "pani peena hai")
    time.sleep(0.2)
    assert ctx.voice.said[-1] == "Oye Meera! Yaad hai na, pani peena hai"
    assert persona.line("confirm_action", action="shut down the laptop") == \
        "Ek sec, shut down the laptop. Pakka kar doon?"
    assert persona.line("confirm_power", action="shut down", delay="15 seconds") == \
        "Pakka shut down kar doon? 15 seconds mein"
    del entry


def test_aqi_red_zone_like_a_friend(friend):
    from jarvis.geo import Place
    from jarvis.skills import aqi

    reading = aqi.AirReading(Place("Anand Vihar", 28.6, 77.3), current={}, average={}, aqi=312, dominant="pm2_5")
    assert aqi.friendly(reading) == "Bahar mat ja yaar, AQI 312 hai. Mask pehen ke hi nikalna"
    persona.set_mode(persona.PROFESSIONAL)
    try:
        assert aqi.friendly(reading) is None  # professional: the original summary and advice
    finally:
        persona.set_mode(None)


def test_the_ai_talks_like_a_friend_too(friend):
    from jarvis.skills import ai

    prompt = ai.system_prompt()
    assert "close friend from New Delhi" in prompt and "never make up facts" in prompt
    assert "arre, yaar" in prompt and "use male verb forms for yourself" in prompt
    persona.set_mode(persona.PROFESSIONAL)
    try:
        assert "professional" in ai.system_prompt() and "close friend" not in ai.system_prompt()
    finally:
        persona.set_mode(None)


# ---------------------------------------------------------------- how you're feeling
@pytest.mark.parametrize("said, mood", [
    ("i'm so tired", "tired"), ("i am bored", "bored"), ("bore ho rahi hoon", "bored"), ("i'm stressed", "stressed"),
    ("feeling a bit low", "sad"), ("i'm so happy", "happy"), ("bhookh lagi hai", "hungry"), ("thak gayi yaar", "tired"),
])
def test_small_talk_about_feelings(friend, said, mood):
    ctx = make_ctx()
    assert handle(ctx, said) == "feeling"
    assert ctx.voice.last in friend_lines("feel_" + mood)


@pytest.mark.parametrize("said", ["i'm low on battery", "i'm down to 10 percent", "are you tired?",
                                  "i'm great at maths, what is 2 plus 2"])
def test_not_every_feeling_word_is_small_talk(said):
    assert handle(make_ctx(), said) != "feeling"


def test_late_night_nudge_is_occasional():
    state = {}
    night, day = datetime(2026, 11, 26, 2, 10), datetime(2026, 11, 26, 15, 0)
    assert persona.late_night_nudge(state, day, chance=1) is None
    assert persona.late_night_nudge(state, night, chance=0) is None
    nudge = persona.late_night_nudge(state, night, chance=1)
    assert nudge == "It's past 2. Don't forget to get some sleep."
    assert persona.late_night_nudge(state, night, chance=1) is None  # not again for 45 minutes


# ---------------------------------------------------------------- switching personality by voice
def test_switching_personality_for_the_session(friend):
    ctx = make_ctx()
    assert handle(ctx, "talk professionally") == "professional mode"
    assert ctx.voice.last == "Okay, I'll keep it professional."
    handle(ctx, "thank you")
    assert ctx.voice.last == "You're welcome!"
    assert handle(ctx, "formal mode") == "professional mode"
    assert ctx.voice.last == "I'm already in professional mode."
    assert handle(ctx, "be my friend again") == "friend mode"
    handle(ctx, "thank you")
    assert ctx.voice.last == "Arre koi baat nahi!"
    assert handle(ctx, "friend mode") == "friend mode"
    assert ctx.voice.last == "Main toh already tera dost hoon yaar!"


# ---------------------------------------------------------------- neural voice
class FakeSapi:
    def __init__(self):
        self.spoken = []

    def Speak(self, text, flags):
        if text:
            self.spoken.append(text)

    def WaitUntilDone(self, ms):
        return True


class FakeNeural:
    voice = "en-IN-NeerjaNeural"

    def __init__(self, audio=b"mp3", fail=False):
        self.audio, self.fail, self.down, self.calls = audio, fail, [], []
        self.available = True

    def synth(self, text):
        self.calls.append(text)
        if self.fail:
            raise ConnectionError("offline")
        return self.audio

    def mark_down(self, reason):
        self.down.append(reason)
        self.available = False


class FakePlayer:
    def __init__(self):
        self.played = []

    def play(self, mp3, stop):
        self.played.append(mp3)
        return True


def start_voice(monkeypatch, neural):
    from concurrent.futures import ThreadPoolExecutor

    sapi, player = FakeSapi(), FakePlayer()

    def start_neural(self):
        self._neural, self._player = neural, player
        self._synth_pool = ThreadPoolExecutor(max_workers=2)

    monkeypatch.setattr(voice.Voice, "_start_neural", start_neural)
    monkeypatch.setattr(voice.Voice, "_start_sapi", staticmethod(lambda: (object(), sapi)))
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    v = voice.Voice(text_mode=True)
    deadline = time.monotonic() + 2
    while v._neural is None and time.monotonic() < deadline:  # loads in the background
        time.sleep(0.01)
    return v, sapi, player


def test_windows_voice_speaks_until_the_neural_voice_has_loaded(monkeypatch):
    sapi, loaded = FakeSapi(), threading.Event()

    def slow_neural(self):
        loaded.wait(2)  # importing edge-tts takes seconds
        self._neural, self._player = FakeNeural(), FakePlayer()

    monkeypatch.setattr(voice.Voice, "_start_neural", slow_neural)
    monkeypatch.setattr(voice.Voice, "_start_sapi", staticmethod(lambda: (object(), sapi)))
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    started = time.monotonic()
    v = voice.Voice(text_mode=True)
    assert time.monotonic() - started < 1  # startup doesn't wait for it
    v.speak("Good morning!")
    assert sapi.spoken == ["Good morning!"]
    loaded.set()
    v.close()


def test_background_caching_waits_while_speaking():
    engine = FakeNeural()
    engine.rate = "+5%"
    busy = iter([True, True, False] + [False] * 100)
    started = time.monotonic()
    tts.pregenerate(engine, ["a", "b"], busy=lambda: next(busy))
    assert engine.calls == ["a", "b"] and time.monotonic() - started >= 0.35


def test_neural_voice_plays_each_sentence(monkeypatch):
    neural = FakeNeural()
    v, sapi, player = start_voice(monkeypatch, neural)
    v.speak("Ho gaya!")
    v.speak("Second sentence.")
    assert player.played == [b"mp3", b"mp3"] and sapi.spoken == []
    assert neural.calls == ["Ho gaya!", "Second sentence."]
    v.close()


def test_falls_back_to_pyttsx3_when_edge_tts_fails(monkeypatch):
    neural = FakeNeural(fail=True)
    v, sapi, player = start_voice(monkeypatch, neural)
    v.speak("Internet nahi hai.")
    assert sapi.spoken == ["Internet nahi hai."] and player.played == []
    assert neural.down  # marked down: the next sentences go straight to the offline voice
    v.speak("Next one.")
    assert sapi.spoken[-1] == "Next one." and neural.calls == ["Internet nahi hai."]
    v.close()


def test_sentences_are_synthesised_ahead(monkeypatch):
    """The second sentence downloads while the first one plays."""
    started = []

    class SlowNeural(FakeNeural):
        def synth(self, text):
            started.append((text, time.monotonic()))
            time.sleep(0.2)
            return b"mp3"

    v, sapi, player = start_voice(monkeypatch, SlowNeural())
    v.speak("One.", wait=False)
    v.speak("Two.", wait=False)
    v.wait_idle()
    assert len(started) == 2 and started[1][1] - started[0][1] < 0.1  # both started together
    v.close()


def test_edge_tts_caches_short_phrases_and_stays_down_after_failing(monkeypatch):
    engine = tts.EdgeTTS.__new__(tts.EdgeTTS)
    engine.voice, engine.rate, engine._down_until, engine._lock = "en-IN-NeerjaNeural", "+5%", 0.0, threading.Lock()
    downloads = []
    monkeypatch.setattr(engine, "_download", lambda text, timeout: downloads.append(text) or b"ID3audio")
    assert engine.synth("Ho gaya!") == b"ID3audio"
    assert engine.synth("Ho gaya!") == b"ID3audio" and downloads == ["Ho gaya!"]  # second time from data/tts_cache
    assert tts.cache_path("Ho gaya!", engine.voice, engine.rate).exists()
    long_text = "This sentence is far too long to be worth caching on disk for later use, really."
    engine.synth(long_text)
    engine.synth(long_text)
    assert downloads.count(long_text) == 2

    def offline(text, timeout):
        raise OSError("no internet")

    monkeypatch.setattr(engine, "_download", offline)
    with pytest.raises(OSError):
        engine.synth("New phrase")
    assert not engine.available
    with pytest.raises(RuntimeError):
        engine.synth("Another phrase")  # doesn't even try for a minute
    assert engine.synth("Ho gaya!") == b"ID3audio"  # cached phrases still play offline


def test_pregenerate_common_phrases(friend, monkeypatch):
    engine = FakeNeural()
    engine.rate = "+5%"

    def synth(text):
        engine.calls.append(text)
        tts.cache_path(text, engine.voice, engine.rate).parent.mkdir(parents=True, exist_ok=True)
        tts.cache_path(text, engine.voice, engine.rate).write_bytes(b"x")
        return b"x"

    engine.synth = synth
    made = tts.pregenerate(engine)
    assert made == len(engine.calls) > 10 and "Ho gaya!" in engine.calls and "Haan, bolo?" in engine.calls
    assert tts.pregenerate(engine) == 0  # already cached


def test_barge_in_stops_neural_playback():
    class Music:
        def __init__(self):
            self.stopped = False

        def load(self, data, kind):
            pass

        def play(self):
            pass

        def get_busy(self):
            return not self.stopped

        def stop(self):
            self.stopped = True

        def unload(self):
            pass

    player = tts.Player.__new__(tts.Player)
    player._music = Music()
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    started = time.monotonic()
    assert player.play(b"mp3", stop) is False
    assert player._music.stopped and time.monotonic() - started < 1


def test_hindi_voice_reads_devanagari():
    assert voice.speakable("मैं ठीक हूँ", hindi_voice=True) == "मैं ठीक हूँ"
    assert voice.speakable("मैं ठीक हूँ") != "मैं ठीक हूँ"  # Roman letters for English voices
