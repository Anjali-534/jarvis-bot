"""Speed work: timing, disk cache, VAD recording, offline wake word, STT engines, barge-in, "One sec".

Audio, webrtcvad, openWakeWord and Whisper are all faked: no microphone, model or network is used.
"""
import logging
import queue
import sys
import threading
import time
import types
import wave

import pytest
from helpers import FakeVoice, make_ctx

import config
from jarvis import audio, cache, http, router, stt, timing, voice
from jarvis.assistant import Assistant
from jarvis.router import handle, skill

REAL_GET_JSON = http.get_json  # before conftest replaces it with a "no network" stub

SPEECH = b"S" * audio.FRAME_BYTES   # fake frames: the detector below calls S speech and Q quiet
QUIET = b"Q" * audio.FRAME_BYTES


class FakeDetector:
    threshold = 300

    def __init__(self):
        self.recalibrated = 0

    def is_speech(self, frame):
        return frame[:1] == b"S"

    def maybe_recalibrate(self):
        self.recalibrated += 1

    def calibrate(self, frames):
        return 321.0


def source(frames):
    frames = list(frames)
    return lambda: frames.pop(0) if frames else QUIET


@pytest.fixture(autouse=True)
def reset_timing():
    timing.finish()
    timing.ENABLED = False
    yield
    timing.finish()
    timing.ENABLED = False


# ---------------------------------------------------------------- timing
def test_timing_report_and_levels(caplog):
    turn = timing.begin()
    turn.marks.update({"wake": 0.0, "speech": 0.4, "end": 1.6})
    timing.add("stt", 0.5)
    timing.add("skill", 0.02)
    timing.add("tts", 1.25)
    with caplog.at_level(logging.DEBUG, logger="jarvis.timing"):
        line = timing.finish()
    assert line.startswith("[timing] wake word at 0.00s | speech detected at 0.40s | end of phrase at 1.60s | "
                           "speech-to-text 0.50s | routing + skill 0.02s | text-to-speech 1.25s | total ")
    assert caplog.records[-1].levelno == logging.DEBUG
    timing.ENABLED = True
    timing.begin()
    with caplog.at_level(logging.DEBUG, logger="jarvis.timing"):
        timing.finish()
    assert caplog.records[-1].levelno == logging.INFO
    assert timing.finish() is None  # nothing pending


def test_assistant_times_skill_separately_from_speech(monkeypatch):
    class SlowSpeaker(FakeVoice):
        def speak(self, text, wait=True):
            super().speak(text, wait)
            timing.add("tts", 5.0)  # pretend speaking took 5 s

    assistant = Assistant(SlowSpeaker(), wake_enabled=False)
    timing.begin()
    lines = []
    monkeypatch.setattr(timing, "finish", lambda: lines.append(timing.current().line()))
    assistant.step("what time is it")
    assert "text-to-speech 5.00s" in lines[0]
    skill_time = float(lines[0].split("routing + skill ")[1].split("s")[0])
    assert skill_time < 1.0


# ---------------------------------------------------------------- cache
def test_cache_ttl(monkeypatch):
    cache.put("k", {"v": 1})
    assert cache.get("k", 60) == {"v": 1}
    monkeypatch.setattr(time, "time", lambda: 10 ** 12)
    assert cache.get("k", 60) is None


def test_get_json_uses_disk_cache_and_hides_secrets(monkeypatch):
    module = http  # conftest stubs http.get_json; REAL_GET_JSON (captured at import) is the real one
    calls = []

    class Session:
        def get(self, url, params=None, timeout=None, **kwargs):
            calls.append((url, params, timeout))
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"temp": 23})

    monkeypatch.setattr(module, "_SESSION", Session())
    params = {"lat": 28.6, "token": "SECRET"}
    assert REAL_GET_JSON("https://api.example/x", params, cache_ttl=600) == {"temp": 23}
    assert REAL_GET_JSON("https://api.example/x", params, cache_ttl=600) == {"temp": 23}
    assert len(calls) == 1 and calls[0][2] == 6          # second call came from data/cache.json; 6 s timeout
    assert "SECRET" not in config.CACHE_FILE.read_text(encoding="utf-8")
    REAL_GET_JSON("https://api.example/x", params)       # no cache_ttl: always live
    assert len(calls) == 2


def test_requests_is_not_imported_at_startup():
    import subprocess

    code = "import sys, main; print('requests' in sys.modules, 'pyautogui' in sys.modules, " \
           "'faster_whisper' in sys.modules, 'openwakeword' in sys.modules, 'pystray' in sys.modules)"
    out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True,
                         cwd=str(config.BASE_DIR), timeout=60).stdout.strip()
    assert out == "False False False False False"


# ---------------------------------------------------------------- recording with VAD
def test_record_phrase_stops_after_silence():
    frames = [QUIET] * 10 + [SPEECH] * 50 + [QUIET] * 100
    timing.begin()
    pcm = audio.record_phrase(source(frames), FakeDetector(), timeout=5, phrase_limit=7, silence_ms=600,
                              preroll_ms=100)
    recorded = len(pcm) // audio.FRAME_BYTES
    # 5 pre-roll frames (incl. the first 2 speech frames) + rest of the speech + a little trailing silence
    assert 50 <= recorded <= 50 + 5 + 10
    assert pcm.count(SPEECH) == 50
    assert {"speech", "end"} <= set(timing.current().marks)


def test_record_phrase_timeout_and_limit():
    assert audio.record_phrase(source([QUIET] * 400), FakeDetector(), timeout=5) == b""
    long = audio.record_phrase(source([SPEECH] * 1000), FakeDetector(), timeout=5, phrase_limit=7)
    assert len(long) // audio.FRAME_BYTES == 350  # 7 s of 20 ms frames
    click = [QUIET, SPEECH, QUIET] * 100  # single voiced frames never start a phrase
    assert audio.record_phrase(source(click), FakeDetector(), timeout=5) == b""
    stopped = audio.record_phrase(source([QUIET] * 400), FakeDetector(), timeout=5, should_stop=lambda: True)
    assert stopped == b""


def tone(level):
    import numpy as np

    return (np.ones(audio.FRAME, dtype=np.int16) * level).tobytes()


def test_speech_detector_calibration_and_energy_gate(monkeypatch):
    detector = audio.SpeechDetector()
    detector.vad = None  # energy only
    assert detector.calibrate([tone(500)] * 25) == pytest.approx(650)  # ambient x 1.3
    assert not detector.is_speech(tone(600)) and detector.is_speech(tone(700))
    assert detector.calibrate([tone(10)] * 25) == audio.MIN_ENERGY  # floor in a silent room

    class Vad:
        def is_speech(self, frame, rate):
            return False

    detector.vad = Vad()
    assert not detector.is_speech(tone(5000))  # loud but not voice (e.g. a fan): VAD says no


def test_idle_recalibration(monkeypatch):
    detector = audio.SpeechDetector()
    detector.vad = None
    detector.calibrate([tone(100)] * 25)
    for _ in range(150):
        detector.is_speech(tone(50))
    assert detector.maybe_recalibrate() is False  # not idle for 10 minutes yet
    detector.calibrated_at -= audio.RECALIBRATE_IDLE_SECONDS + 1
    assert detector.maybe_recalibrate() is True and detector.ambient == pytest.approx(50)


# ---------------------------------------------------------------- wake word
class FakeOww:
    def __init__(self, scores):
        self.scores, self.chunks, self.resets = list(scores), [], 0

    def predict(self, chunk):
        self.chunks.append(len(chunk))
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


def make_wake(monkeypatch, scores, sensitivity=0.5):
    model = FakeOww(scores)
    monkeypatch.setattr(audio.WakeWord, "_load", lambda self: model)
    return audio.WakeWord(sensitivity), model


def test_wake_word_scores_80ms_chunks(monkeypatch):
    wake, model = make_wake(monkeypatch, [0.1, 0.49, 0.8])
    fired = [wake.feed(QUIET) for _ in range(12)]
    assert fired.index(True) == 11 and fired.count(True) == 1
    assert model.chunks == [1280, 1280, 1280] and model.resets == 1


def test_wake_sensitivity_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "WAKE_SENSITIVITY", 0.9)
    wake, _ = make_wake(monkeypatch, [0.8], sensitivity=None)
    assert wake.sensitivity == 0.9
    assert not any(wake.feed(QUIET) for _ in range(4))


def test_sklearn_is_skipped(monkeypatch):
    for name in ("openwakeword", "openwakeword.custom_verifier_model"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    audio._skip_sklearn_import()
    stub = sys.modules["openwakeword.custom_verifier_model"]
    with pytest.raises(NotImplementedError):
        stub.train_custom_verifier()


# ---------------------------------------------------------------- Voice.listen
class FakeMic:
    def __init__(self, frames):
        self.frames, self.flushes = list(frames), 0

    def read(self):
        return self.frames.pop(0) if self.frames else QUIET

    def flush(self):
        self.flushes += 1


class FakeSTT:
    name = "fake"

    def __init__(self, text="What's The Weather"):
        self.text, self.calls, self.prepared = text, [], []

    def transcribe(self, pcm, language):
        self.calls.append((len(pcm), language))
        return self.text

    def prepare(self, language):
        self.prepared.append(language)


def make_voice(frames=(), wake=None, wake_failed=False, stt_engine=None):
    v = voice.Voice.__new__(voice.Voice)
    v.text_mode, v.mute = False, True
    v._language = "en-IN"
    v._queue, v._tts_ok, v._thread = queue.Queue(), False, None
    v._pending, v._neural, v._player = {}, None, None
    v._stop_speech, v._cancel_wait = threading.Event(), threading.Event()
    v._hushed, v.last_wake = False, False
    v.mic, v.detector = FakeMic(frames), FakeDetector()
    v.wake, v._wake_failed = wake, wake_failed
    v._wake_ready = threading.Event()
    v._wake_ready.set()
    v.stt = stt_engine or FakeSTT()
    v.recognizer = v._legacy_source = None
    return v


class ScriptedWake:
    def __init__(self, fire_on):
        self.count, self.fire_on = 0, fire_on

    def feed(self, frame):
        self.count += 1
        return self.count == self.fire_on


def test_listen_waits_for_wake_word_then_records(monkeypatch):
    beeps = []
    monkeypatch.setattr(voice, "beep", lambda: beeps.append(1))
    engine = FakeSTT()
    v = make_voice([QUIET] * 10 + [SPEECH] * 40 + [QUIET] * 40, wake=ScriptedWake(fire_on=10), stt_engine=engine)
    assert v.listen(wake=True) == "what's the weather"
    assert v.last_wake is True and beeps == [1]
    assert engine.calls and engine.calls[0][1] == "en-IN"
    assert v.mic.flushes >= 2  # before listening, and after the beep


def test_nothing_reaches_stt_without_wake_word(monkeypatch):
    monkeypatch.setattr(voice, "WAKE_POLL_SECONDS", 0.05)
    engine = FakeSTT()
    v = make_voice([SPEECH] * 500, wake=ScriptedWake(fire_on=10 ** 9), stt_engine=engine)
    assert v.listen(wake=True) == ""
    assert engine.calls == [] and v.last_wake is False
    assert v.detector.recalibrated == 1  # idle time is used to recalibrate quietly


def test_fallback_to_text_wake_word_when_openwakeword_fails():
    engine = FakeSTT("jarvis what time is it")
    v = make_voice([SPEECH] * 30 + [QUIET] * 40, wake=None, wake_failed=True, stt_engine=engine)
    assert v.offline_wake is False
    assert v.listen(wake=True) == "jarvis what time is it"  # recorded directly; Assistant checks the text
    assert v.last_wake is False


def test_hotkey_cancels_wake_wait():
    v = make_voice([SPEECH] * 30 + [QUIET] * 40, wake=ScriptedWake(fire_on=10 ** 9))
    v.cancel_wait()
    assert v.listen(wake=True) == "what's the weather"
    assert not v._cancel_wait.is_set()


def test_language_switch_preloads_stt_model():
    engine = FakeSTT()
    v = make_voice(stt_engine=engine)
    v.language = "hi-IN"
    assert engine.prepared == ["hi-IN"] and v.language == "hi-IN"


# ---------------------------------------------------------------- barge-in
def test_stop_speaking_purges_queue_and_hushes_rest_of_reply():
    v = make_voice()
    v._tts_ok = True
    done = threading.Event()
    v._queue.put(("long answer", done))
    v.stop_speaking()
    assert done.is_set() and v._queue.empty() and v._stop_speech.is_set()
    v.speak("second sentence of the same reply")
    assert v._queue.empty()  # dropped
    v.listen(timeout=0.02)   # a new turn clears it
    assert v._hushed is False


def test_sapi_speech_stops_when_interrupted():
    v = make_voice()

    class Sapi:
        def __init__(self):
            self.calls, self.waits = [], 0

        def Speak(self, text, flags):
            self.calls.append((text, flags))

        def WaitUntilDone(self, ms):
            self.waits += 1
            if self.waits == 3:
                v._stop_speech.set()
            return False

    sapi = Sapi()
    v._speak_sapi(sapi, "a long sentence")
    assert sapi.calls == [("a long sentence", 1), ("", 3)]  # async speak, then purge


def test_hotkey_barge_in_stops_speech_and_listens(monkeypatch):
    import jarvis.assistant as assistant_module

    monkeypatch.setattr(assistant_module, "beep", lambda: None)
    calls = []

    class BargeVoice(FakeVoice):
        def stop_speaking(self):
            calls.append("stop")

        def cancel_wait(self):
            calls.append("cancel")

    assistant = Assistant(BargeVoice(), wake_enabled=True)
    assistant.wake()
    assert calls == ["stop", "cancel"] and assistant._forced.is_set()


# ---------------------------------------------------------------- the loop with the offline wake word
class WakeVoice(FakeVoice):
    offline_wake = True

    def __init__(self, answers, wakes):
        super().__init__(answers)
        self.wakes, self.calls = list(wakes), []
        self.last_wake = False

    def listen(self, timeout=8, phrase_time_limit=12, wake=False):
        self.calls.append(wake)
        self.last_wake = self.wakes.pop(0) if wake and self.wakes else False
        return super().listen()


def test_detected_wake_word_needs_no_spoken_jarvis():
    v = WakeVoice(["what time is it"], wakes=[True])
    assistant = Assistant(v, wake_enabled=True)
    assistant._last_reply -= 3600  # long after the last reply: wake word needed
    assert assistant.listen_once() == "time"
    assert v.calls == [True]
    assistant.listen_once()  # within the follow-up window: no wake word needed
    assert v.calls[-1] is False


def test_sleeping_wakes_only_on_detected_wake_word():
    v = WakeVoice(["what time is it", "what time is it"], wakes=[False, True])
    assistant = Assistant(v, wake_enabled=True)
    assistant.ctx.sleeping = True
    assert assistant.listen_once() is None and assistant.ctx.sleeping
    assert assistant.listen_once() == "time" and not assistant.ctx.sleeping


# ---------------------------------------------------------------- speech-to-text engines
def test_make_engine_choices(monkeypatch):
    assert stt.make("google").name == "google"
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # not installed
    assert stt.make("whisper").name == "google"


def test_whisper_model_and_device(monkeypatch):
    assert stt.whisper_model_name("en-IN") == "base.en"
    assert stt.whisper_model_name("hi-IN") == "small"
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    assert stt.whisper_device() == ("cuda", "float16")
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    assert stt.whisper_device() == ("cpu", "int8")


def test_whisper_falls_back_to_google_while_loading(monkeypatch):
    gate = threading.Event()

    class Model:
        def transcribe(self, audio, **kwargs):
            assert kwargs["language"] == "en" and audio.dtype.name == "float32"
            return [types.SimpleNamespace(text=" Open notepad.")], None

    def slow_load(self, name):
        gate.wait(5)
        return Model()

    monkeypatch.setattr(stt.WhisperSTT, "_load", slow_load)
    google = FakeSTT("open notepad (google)")
    whisper = stt.WhisperSTT(fallback=google, preload_language="en-IN")
    pcm = SPEECH * 10
    assert whisper.transcribe(pcm, "en-IN") == "open notepad (google)"  # still loading
    gate.set()
    for _ in range(100):
        if whisper.ready("en-IN"):
            break
        time.sleep(0.02)
    assert whisper.transcribe(pcm, "en-IN") == "Open notepad"  # trailing "." removed


# ---------------------------------------------------------------- "One sec"
@pytest.fixture
def temp_skill():
    added = []

    def add(name, pattern, fn):
        skill(name, pattern)(fn)
        added.append(router.SKILLS.pop())
        router.SKILLS.insert(0, added[-1])

    yield add
    for sk in added:
        router.SKILLS.remove(sk)


def test_one_sec_for_slow_skills(monkeypatch, temp_skill):
    monkeypatch.setattr(config, "ONE_SEC_AFTER", 0.05)
    temp_skill("slow thing", r"^slow thing$", lambda ctx, c, m: time.sleep(0.3) or "Here you go.")
    temp_skill("fast thing", r"^fast thing$", lambda ctx, c, m: "Instant.")
    temp_skill("asks", r"^asking thing$", lambda ctx, c, m: (ctx.ask("Which one?"), time.sleep(0.3))[0] and "ok")
    ctx = make_ctx(["the first"])
    handle(ctx, "slow thing")
    assert ctx.voice.said == ["One sec.", "Here you go."]
    ctx.voice.said.clear()
    handle(ctx, "fast thing")
    time.sleep(0.1)
    assert ctx.voice.said == ["Instant."]
    ctx.voice.said.clear()
    handle(ctx, "asking thing")
    assert "One sec." not in ctx.voice.said  # it spoke ("Which one?") before the deadline


# ---------------------------------------------------------------- voice commands
def test_recalibrate_mic_command():
    ctx = make_ctx()
    ctx.voice.text_mode = True
    handle(ctx, "recalibrate mic")
    assert ctx.voice.last == "The microphone isn't in use in text mode."
    ctx = make_ctx()
    ctx.voice.calibrate = lambda: 400.0
    assert handle(ctx, "recalibrate the microphone") == "recalibrate mic"
    assert ctx.voice.said == ["Stay quiet for a moment.", "Done. I've re-measured the background noise."]


def test_record_samples_command(monkeypatch):
    monkeypatch.setattr(voice, "beep", lambda: None)
    ctx = make_ctx()
    takes = [SPEECH * 50, b"", SPEECH * 60, SPEECH * 70, SPEECH * 80]
    ctx.voice.record_sample = lambda: takes.pop(0)
    assert handle(ctx, "record samples") == "record samples"
    saved = sorted(p.name for p in config.SAMPLES_DIR.iterdir())
    assert saved == ["sample1.txt", "sample1.wav", "sample3.txt", "sample3.wav", "sample4.txt", "sample4.wav",
                     "sample5.txt", "sample5.wav"]
    with wave.open(str(config.SAMPLES_DIR / "sample1.wav")) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
    assert (config.SAMPLES_DIR / "sample3.txt").read_text() == "set a timer for five minutes"
    assert ctx.voice.last.startswith("Saved 4 samples")


# ---------------------------------------------------------------- benchmark
def test_benchmark_loads_converts_and_compares(monkeypatch, tmp_path):
    import numpy as np

    from jarvis import benchmark

    path = tmp_path / "sample1.wav"
    stereo = np.zeros((44100, 2), dtype=np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(stereo.tobytes())
    path.with_suffix(".txt").write_text("open notepad", encoding="utf-8")
    pcm, seconds = benchmark.load_wav(path)
    assert seconds == pytest.approx(1.0, abs=0.01) and len(pcm) == 32000

    class Engine:
        def __init__(self, delay, text):
            self.delay, self.text = delay, text

        def transcribe(self, pcm, language):
            time.sleep(self.delay)
            return self.text

    engines = {"google": Engine(0.0, "open notepad"), "whisper": Engine(0.05, "open")}
    monkeypatch.setattr(benchmark, "make_engine", lambda name, language: (engines[name], 0.0))
    lines = []
    results = benchmark.run([path], ["google", "whisper"], out=lines.append)
    assert set(results) == {"google", "whisper"}
    assert any("100% words" in line for line in lines) and any("50% words" in line for line in lines)
    assert lines[-1] == "\nFastest on this machine: google. Set STT_ENGINE=google in .env."


def test_benchmark_without_samples(capsys, tmp_path):
    from jarvis import benchmark

    assert benchmark.main(["--dir", str(tmp_path)]) == 1
    assert "record samples" in capsys.readouterr().out
