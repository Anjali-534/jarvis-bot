"""Text-to-speech (on its own worker thread) and speech-to-text."""
import queue
import threading

import config


def speakable(text):
    """Windows voices can't read Devanagari, so speak it in Roman letters ("मैं लेट हूँ" -> "main let hun")."""
    from jarvis import translit

    return translit.to_roman(text) if translit.has_devanagari(text) else text


class Voice:
    def __init__(self, text_mode=False, mute=False):
        self.text_mode = text_mode
        self.mute = mute
        self.language = config.SPEECH_LANGUAGE  # "switch to hindi" changes this for the session
        self._queue = queue.Queue()
        self._tts_ok = False
        self._thread = None
        self.recognizer = None
        self.microphone = None

        if not mute:
            ready = threading.Event()
            self._thread = threading.Thread(target=self._tts_worker, args=(ready,), name="tts", daemon=True)
            self._thread.start()
            ready.wait(timeout=15)

        if not text_mode:
            self._init_microphone()

    # ---------------- text to speech ----------------
    def _tts_worker(self, ready):
        engine = None
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
            self._tts_ok = True
        except Exception as exc:
            print(f"[voice] Text-to-speech unavailable ({exc}); I'll print replies instead.")
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
                engine.say(text)
                engine.runAndWait()
            except Exception as exc:
                print(f"[voice] TTS error: {exc}")
            finally:
                done.set()

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
        print(f"{config.ASSISTANT_NAME}: {text}")
        if not self._tts_ok:
            return
        done = threading.Event()
        self._queue.put((speakable(text), done))
        if wait:
            done.wait(timeout=120)

    # ---------------- speech to text ----------------
    def _init_microphone(self):
        try:
            import speech_recognition as sr

            self.recognizer = sr.Recognizer()
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.pause_threshold = 0.8
            self.microphone = sr.Microphone()
            print("[voice] Calibrating for background noise, please stay quiet...")
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
        except Exception as exc:
            print(f"[voice] No microphone available ({exc}). Switching to text mode.")
            self.text_mode = True
            self.microphone = None

    def listen(self, timeout=8, phrase_time_limit=12):
        if self.text_mode:
            try:
                return input("You: ").strip().lower()
            except EOFError:
                return "exit"

        import speech_recognition as sr

        try:
            with self.microphone as source:
                print("Listening...")
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return ""
        except Exception as exc:
            print(f"[voice] Microphone error: {exc}")
            return ""
        try:
            text = self.recognizer.recognize_google(audio, language=self.language)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            print(f"[voice] Speech service error: {exc}")
            return ""
        print(f"You said: {text}")
        return text.strip().lower()

    def close(self):
        if self._thread and self._tts_ok:
            self._queue.put(None)
            self._thread.join(timeout=5)
