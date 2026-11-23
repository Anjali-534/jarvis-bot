"""Speech-to-text engines: Google (online, via SpeechRecognition) or Whisper (local, faster-whisper).

STT_ENGINE=google|whisper in .env. Whisper loads in a background thread; until it's ready, Google is used.
Whisper uses "base.en" for English and "small" for Hindi, on CUDA if available, else CPU with int8.
"""
import logging
import os
import threading

log = logging.getLogger(__name__)

RATE = 16000


class GoogleSTT:
    name = "google"

    def __init__(self):
        import speech_recognition as sr

        self._sr = sr
        self.recognizer = sr.Recognizer()

    def transcribe(self, pcm, language="en-IN"):
        audio = self._sr.AudioData(pcm, RATE, 2)
        try:
            return self.recognizer.recognize_google(audio, language=language)
        except self._sr.UnknownValueError:
            return ""
        except self._sr.RequestError as exc:
            log.warning("[stt] Google speech service error: %s", exc)
            return ""


def whisper_device():
    """('cuda', 'float16') with an NVIDIA GPU, else ('cpu', 'int8')."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def whisper_model_name(language):
    return "small" if (language or "").lower().startswith("hi") else "base.en"


class WhisperSTT:
    name = "whisper"

    def __init__(self, fallback=None, preload_language="en-IN"):
        self.fallback = fallback
        self.models = {}
        self._loading = {}
        self._lock = threading.Lock()
        self.device, self.compute_type = whisper_device()
        if preload_language:
            self.prepare(preload_language)

    def _load(self, name):
        from faster_whisper import WhisperModel

        return WhisperModel(name, device=self.device, compute_type=self.compute_type, cpu_threads=os.cpu_count() or 4)

    def prepare(self, language):
        """Start loading the model for this language in the background (no-op if loaded or loading)."""
        name = whisper_model_name(language)
        with self._lock:
            if name in self.models or name in self._loading:
                return self._loading.get(name)

            def load():
                try:
                    model = self._load(name)
                    with self._lock:
                        self.models[name] = model
                    log.info("[stt] Whisper %s ready on %s (%s)", name, self.device, self.compute_type)
                except Exception as exc:
                    log.warning("[stt] Whisper %s failed to load (%s); using Google instead", name, exc)
                finally:
                    with self._lock:
                        self._loading.pop(name, None)

            thread = threading.Thread(target=load, name=f"whisper-{name}", daemon=True)
            self._loading[name] = thread
        thread.start()
        return thread

    def ready(self, language="en-IN"):
        return whisper_model_name(language) in self.models

    def transcribe(self, pcm, language="en-IN"):
        name = whisper_model_name(language)
        model = self.models.get(name)
        if model is None:
            self.prepare(language)
            if self.fallback is None:
                return ""
            log.debug("[stt] Whisper %s still loading, using %s", name, self.fallback.name)
            return self.fallback.transcribe(pcm, language)
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(audio, language="hi" if name == "small" else "en", beam_size=1,
                                       without_timestamps=True, condition_on_previous_text=False)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text.rstrip(".?!") if len(text.split()) <= 12 else text  # "Open notepad." -> "Open notepad"


def make(engine=None, language="en-IN"):
    """The configured engine. Whisper falls back to Google while loading or if it isn't installed."""
    import config

    engine = (engine or config.STT_ENGINE or "google").lower()
    google = GoogleSTT()
    if engine == "whisper":
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            log.warning("[stt] faster-whisper isn't installed; using Google. Run pip install -r requirements.txt")
            return google
        return WhisperSTT(fallback=google, preload_language=language)
    return google
