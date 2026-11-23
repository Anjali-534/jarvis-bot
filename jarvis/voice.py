"""Text-to-speech (on its own worker thread, interruptible) and listening.

Listening uses one microphone stream kept open for the whole session (jarvis.audio):
* listen(wake=True) waits for the offline "hey jarvis" wake word (openWakeWord). Nothing leaves the
  laptop until it fires. Then it beeps and records the command.
* Commands end after END_SILENCE_MS of silence (webrtcvad + a calibrated energy threshold).
* The recorded command goes to the STT engine (Google, or local Whisper with STT_ENGINE=whisper).
If openWakeWord can't load, Jarvis falls back to the old text wake word ("jarvis ..." in the transcript).
If the 16 kHz stream can't be opened at all, it falls back to SpeechRecognition's microphone, opened once.
"""
import logging
import queue
import threading
import time

import config
from jarvis import timing

log = logging.getLogger(__name__)

CALIBRATE_SECONDS = 0.5
WAKE_POLL_SECONDS = 1.0  # listen(wake=True) returns "" this often so the main loop can check its flags


def speakable(text):
    """Windows voices can't read Devanagari, so speak it in Roman letters ("मैं लेट हूँ" -> "main let hun")."""
    from jarvis import translit

    return translit.to_roman(text) if translit.has_devanagari(text) else text


def beep():
    """A short 'I'm listening' chime (a beep, not speech, so the mic doesn't hear words)."""
    import sys

    if sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(880, 120)
        except Exception:
            pass


class Voice:
    def __init__(self, text_mode=False, mute=False):
        self.text_mode = text_mode
        self.mute = mute
        self._language = config.SPEECH_LANGUAGE  # "switch to hindi" changes this for the session
        self._queue = queue.Queue()
        self._tts_ok = False
        self._thread = None
        self._stop_speech = threading.Event()
        self._hushed = False          # after barge-in: drop the rest of this reply
        self._cancel_wait = threading.Event()
        self.last_wake = False        # True if the last listen() was started by the offline wake word
        self.mic = None               # audio.MicStream
        self.detector = None          # audio.SpeechDetector
        self.wake = None              # audio.WakeWord once loaded
        self._wake_ready = threading.Event()
        self._wake_failed = False
        self.stt = None
        self.recognizer = None        # legacy SpeechRecognition fallback
        self._legacy_source = None

        tts_ready = threading.Event()
        if not mute:
            self._thread = threading.Thread(target=self._tts_worker, args=(tts_ready,), name="tts", daemon=True)
            self._thread.start()
        if not text_mode:
            self._init_audio()
        if not mute:
            tts_ready.wait(timeout=15)

    # ---------------------------------------------------------------- language
    @property
    def language(self):
        return self._language

    @language.setter
    def language(self, code):
        self._language = code
        prepare = getattr(self.stt, "prepare", None)
        if prepare:
            prepare(code)  # Whisper: start loading the Hindi model now, not on the first Hindi command

    # ---------------------------------------------------------------- text to speech
    def _tts_worker(self, ready):
        engine = sapi = None
        try:
            try:
                import comtypes

                comtypes.CoInitialize()
            except Exception:
                pass
            import pyttsx3

            engine = pyttsx3.init()
            self._select_voice(engine)
            engine.setProperty("rate", config.VOICE_RATE)
            engine.setProperty("volume", 1.0)
            sapi = getattr(getattr(getattr(engine, "proxy", None), "_driver", None), "_tts", None)
            if sapi is not None:
                sapi.EventInterests = 0  # we poll instead of using pyttsx3's event loop
            self._tts_ok = True
        except Exception as exc:
            log.warning(f"[voice] Text-to-speech unavailable ({exc}); I'll print replies instead.")
        finally:
            ready.set()
        if not self._tts_ok:
            return
        while True:
            item = self._queue.get()
            if item is None:
                break
            text, done = item
            try:
                if self._stop_speech.is_set() or not text:  # "" is wait_idle()'s marker
                    continue
                if sapi is not None:
                    self._speak_sapi(sapi, text)
                else:  # not Windows SAPI: can't be interrupted mid-sentence
                    engine.say(text)
                    engine.runAndWait()
            except Exception as exc:
                log.warning(f"[voice] TTS error: {exc}")
            finally:
                done.set()

    def _speak_sapi(self, sapi, text):
        sapi.Speak(text, 1)  # SVSFlagsAsync
        while not sapi.WaitUntilDone(30):
            if self._stop_speech.is_set():
                sapi.Speak("", 3)  # SVSFPurgeBeforeSpeak | async: stop right now
                break

    @staticmethod
    def _select_voice(engine):
        voices = engine.getProperty("voices") or []
        if not voices:
            return
        wanted = config.VOICE_NAME.lower()
        chosen = next((v for v in voices if wanted and wanted in (v.name or "").lower()), None)
        if chosen is None:
            chosen = voices[1] if len(voices) > 1 else voices[0]
        engine.setProperty("voice", chosen.id)

    def speak(self, text, wait=True):
        log.info(f"{config.ASSISTANT_NAME}: {text}")
        if not self._tts_ok or self._hushed:
            return
        self._stop_speech.clear()
        done = threading.Event()
        self._queue.put((speakable(text), done))
        if wait:
            started = time.perf_counter()
            done.wait(timeout=120)
            timing.add("tts", time.perf_counter() - started)

    def wait_idle(self):
        """Wait until everything queued with speak(wait=False) has been spoken."""
        if not self._tts_ok or self._hushed:
            return
        done = threading.Event()
        started = time.perf_counter()
        self._queue.put(("", done))
        done.wait(timeout=120)
        timing.add("tts", time.perf_counter() - started)

    def stop_speaking(self):
        """Barge-in: stop talking now and skip whatever else this reply was going to say."""
        self._hushed = True
        self._stop_speech.set()
        while True:
            try:
                _, done = self._queue.get_nowait()
            except queue.Empty:
                break
            done.set()

    def cancel_wait(self):
        """Stop waiting for the wake word (hotkey pressed): the next listen() records straight away."""
        self._cancel_wait.set()

    # ---------------------------------------------------------------- microphone setup
    def _init_audio(self):
        from jarvis import audio, stt

        try:
            self.mic = audio.MicStream()
        except Exception as exc:
            log.warning(f"[voice] Couldn't open the 16 kHz microphone stream ({exc}); trying the basic one.")
            self._init_legacy_microphone()
            if self.text_mode:
                return
        else:
            # Load the wake model in the background first, so it overlaps calibration, TTS start-up and the greeting
            if config.WAKE_WORD_ENABLED:
                threading.Thread(target=self._load_wake, name="wake-load", daemon=True).start()
            else:
                self._wake_failed = True
            self.detector = audio.SpeechDetector()
            self.calibrate()
        self.stt = stt.make(language=self._language)

    def _load_wake(self):
        from jarvis import audio

        try:
            self.wake = audio.WakeWord()
            log.info("[wake] Offline wake word ready: say \"Hey Jarvis\".")
        except Exception as exc:
            self._wake_failed = True
            log.warning(f"[wake] openWakeWord unavailable ({exc}); using the spoken wake word through speech "
                        "recognition instead.")
        finally:
            self._wake_ready.set()

    def _init_legacy_microphone(self):
        """SpeechRecognition's microphone, opened ONCE, with fast end-of-phrase settings."""
        try:
            import speech_recognition as sr

            self.recognizer = sr.Recognizer()
            microphone = sr.Microphone()
            self._legacy_source = microphone.__enter__()  # keep it open for the whole session
            self.recognizer.adjust_for_ambient_noise(self._legacy_source, duration=CALIBRATE_SECONDS)
            self._apply_legacy_settings()
        except Exception as exc:
            log.warning(f"[voice] No microphone available ({exc}). Switching to text mode.")
            self.text_mode = True
            self._legacy_source = None

    def _apply_legacy_settings(self):
        r = self.recognizer
        r.dynamic_energy_threshold = False
        r.energy_threshold = r.energy_threshold * 1.3
        r.pause_threshold = 0.5
        r.non_speaking_duration = 0.3

    def calibrate(self):
        """Measure the room's background level (about half a second). Returns the new threshold."""
        if self.detector is not None and self.mic is not None:
            self.mic.flush()
            frames = [self.mic.read() for _ in range(int(CALIBRATE_SECONDS * 1000 / 20))]
            return self.detector.calibrate(frames)
        if self.recognizer is not None and self._legacy_source is not None:
            self.recognizer.adjust_for_ambient_noise(self._legacy_source, duration=CALIBRATE_SECONDS)
            self._apply_legacy_settings()
            return self.recognizer.energy_threshold
        return None

    @property
    def offline_wake(self):
        """True if the offline wake word is (or is about to be) available."""
        return self.mic is not None and not self._wake_failed

    # ---------------------------------------------------------------- listening
    def listen(self, timeout=None, phrase_time_limit=None, wake=False):
        self.last_wake = False
        if self.text_mode:
            try:
                text = input("You: ").strip().lower()
            except EOFError:
                return "exit"
            timing.begin()
            return text
        self._hushed = False
        if self.mic is None:
            return self._listen_legacy(timeout, phrase_time_limit)

        from jarvis import audio

        self.mic.flush()  # don't transcribe our own reply
        if wake and self._wait_for_wake():
            self.last_wake = True
        elif wake and not self._cancel_wait.is_set() and not self._wake_failed:
            return ""  # no wake word yet: let the main loop check its flags
        self._cancel_wait.clear()
        if not self.last_wake:
            timing.begin()
        pcm = audio.record_phrase(self.mic.read, self.detector, timeout=timeout, phrase_limit=phrase_time_limit,
                                  should_stop=self._cancel_wait.is_set)
        if not pcm:
            return ""
        started = time.perf_counter()
        text = self.stt.transcribe(pcm, self.language) if self.stt else ""
        timing.add("stt", time.perf_counter() - started)
        if text:
            log.info(f"You said: {text}")
        return text.strip().lower()

    def _wait_for_wake(self):
        """Feed the stream to the wake word model for up to WAKE_POLL_SECONDS. True when it fires."""
        if not self._wake_ready.wait(timeout=10) or self.wake is None:
            return False
        deadline = time.monotonic() + WAKE_POLL_SECONDS
        while time.monotonic() < deadline:
            if self._cancel_wait.is_set():
                return False
            frame = self.mic.read()
            self.detector.is_speech(frame)  # tracks quiet levels for idle recalibration
            if self.wake.feed(frame):
                timing.begin()
                timing.mark("wake")
                beep()
                self.mic.flush()  # drop the beep itself
                return True
        self.detector.maybe_recalibrate()
        return False

    def _listen_legacy(self, timeout, phrase_time_limit):
        if self._legacy_source is None:
            return ""
        import speech_recognition as sr

        timing.begin()
        try:
            audio = self.recognizer.listen(self._legacy_source, timeout=timeout or config.LISTEN_TIMEOUT,
                                           phrase_time_limit=phrase_time_limit or config.PHRASE_LIMIT)
        except sr.WaitTimeoutError:
            return ""
        except Exception as exc:
            log.warning(f"[voice] Microphone error: {exc}")
            return ""
        timing.mark("end")
        started = time.perf_counter()
        text = self.stt.transcribe(audio.get_raw_data(convert_rate=16000, convert_width=2), self.language) \
            if self.stt else ""
        timing.add("stt", time.perf_counter() - started)
        if text:
            log.info(f"You said: {text}")
        return text.strip().lower()

    def record_sample(self, seconds_limit=7.0):
        """Record one phrase and return 16 kHz PCM bytes (for 'record samples')."""
        if self.mic is None:
            return b""
        from jarvis import audio

        self.mic.flush()
        return audio.record_phrase(self.mic.read, self.detector, timeout=8, phrase_limit=seconds_limit)

    def close(self):
        if self._thread and self._tts_ok:
            self._queue.put(None)
            self._thread.join(timeout=5)
        if self.mic is not None:
            try:
                self.mic.close()
            except Exception:
                pass
