"""Microphone audio: one always-open 16 kHz stream, speech detection (webrtcvad + energy), offline wake word.

Everything works on 20 ms frames of 16-bit mono PCM (320 samples, 640 bytes):
* MicStream      opened once at startup and kept open (no re-opening per listen)
* SpeechDetector webrtcvad, gated by an energy threshold = calibrated ambient level x 1.3
* record_phrase  waits for speech, stops after END_SILENCE_MS of silence (or the phrase limit)
* WakeWord       openWakeWord's pretrained "hey jarvis" model, run locally on the stream
"""
import collections
import logging
import threading
import time

import config
from jarvis import timing

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 320                  # samples per 20 ms frame
FRAME_MS = 20
FRAME_BYTES = FRAME * 2
ENERGY_FACTOR = 1.3          # speech must be this much louder than the calibrated room level
MIN_ENERGY = 120.0           # floor so a very quiet room doesn't make every click "speech"
RECALIBRATE_IDLE_SECONDS = 600


def rms(frame):
    import numpy as np

    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0


# ---------------------------------------------------------------- microphone
def input_devices(pa=None):
    """[(index, name)] for every microphone Windows reports (MME devices, no duplicates)."""
    import pyaudio

    own = pa is None
    pa = pa or pyaudio.PyAudio()
    try:
        seen, devices = set(), []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = str(info.get("name", "")).strip()
            if info.get("maxInputChannels", 0) > 0 and info.get("hostApi", 0) == 0 and name and name not in seen:
                seen.add(name)
                devices.append((i, name))
        return devices
    finally:
        if own:
            pa.terminate()


def input_device_index(pa=None):
    """The device chosen in Settings (MIC_DEVICE: part of its name), or None for the Windows default."""
    import config

    wanted = (getattr(config, "MIC_DEVICE", "") or "").strip().lower()
    if not wanted:
        return None
    try:
        for index, name in input_devices(pa):
            if wanted in name.lower():
                return index
    except Exception:
        log.debug("couldn't list microphones", exc_info=True)
    log.warning("[audio] microphone %r not found; using the Windows default", wanted)
    return None


class MicStream:
    """A PyAudio input stream opened once. read() returns one 20 ms frame at 16 kHz."""

    def __init__(self):
        import pyaudio

        self._pa = pyaudio.PyAudio()
        self.native_rate = RATE
        device = input_device_index(self._pa)
        try:
            self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                                         frames_per_buffer=FRAME, input_device_index=device)
        except OSError:  # some drivers refuse 16 kHz: record at the device rate and resample
            info = (self._pa.get_device_info_by_index(device) if device is not None
                    else self._pa.get_default_input_device_info())
            self.native_rate = int(info["defaultSampleRate"])
            self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=self.native_rate, input=True,
                                         frames_per_buffer=int(FRAME * self.native_rate / RATE),
                                         input_device_index=device)
            log.info("[audio] microphone opened at %s Hz (resampling to 16 kHz)", self.native_rate)
        self._lock = threading.Lock()

    def read(self):
        with self._lock:
            if self.native_rate == RATE:
                return self._stream.read(FRAME, exception_on_overflow=False)
            raw = self._stream.read(int(FRAME * self.native_rate / RATE), exception_on_overflow=False)
        return resample(raw, self.native_rate, RATE)

    def flush(self):
        """Throw away audio buffered while Jarvis was busy (e.g. its own voice)."""
        with self._lock:
            try:
                available = self._stream.get_read_available()
                if available:
                    self._stream.read(available, exception_on_overflow=False)
            except OSError:
                pass

    def close(self):
        try:
            self._stream.stop_stream()
            self._stream.close()
        finally:
            self._pa.terminate()


def resample(raw, source_rate, target_rate):
    import numpy as np

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    count = int(len(samples) * target_rate / source_rate)
    if count <= 0:
        return b""
    positions = np.linspace(0, len(samples) - 1, count)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.int16).tobytes()


# ---------------------------------------------------------------- speech detection
class SpeechDetector:
    """Is this 20 ms frame speech? webrtcvad if installed, always gated by an energy threshold."""

    def __init__(self, vad_mode=2):
        self.vad = None
        try:
            import webrtcvad

            self.vad = webrtcvad.Vad(vad_mode)
        except Exception as exc:  # ImportError, or a broken build
            log.info("[audio] webrtcvad unavailable (%s); using the energy threshold only", exc)
        self.ambient = MIN_ENERGY / ENERGY_FACTOR
        self.calibrated_at = 0.0
        self._quiet = collections.deque(maxlen=250)  # recent non-speech frame levels (5 s)

    @property
    def threshold(self):
        return max(MIN_ENERGY, self.ambient * ENERGY_FACTOR)

    def calibrate(self, frames):
        """Set the room level from a list of frames (e.g. 0.5 s of quiet)."""
        levels = sorted(rms(f) for f in frames)
        if levels:
            self.ambient = levels[len(levels) // 2]
        self.calibrated_at = time.monotonic()
        log.debug("[audio] calibrated: ambient %.0f, threshold %.0f", self.ambient, self.threshold)
        return self.threshold

    def is_speech(self, frame):
        level = rms(frame)
        loud = level > self.threshold
        if not loud:
            self._quiet.append(level)
            return False
        if self.vad is None:
            return True
        try:
            return self.vad.is_speech(frame, RATE)
        except Exception:
            return True

    def maybe_recalibrate(self):
        """Quietly re-measure the room from recent quiet frames after 10 idle minutes."""
        if time.monotonic() - self.calibrated_at < RECALIBRATE_IDLE_SECONDS or len(self._quiet) < 100:
            return False
        levels = sorted(self._quiet)
        self.ambient = levels[len(levels) // 2]
        self.calibrated_at = time.monotonic()
        log.debug("[audio] idle recalibration: threshold %.0f", self.threshold)
        return True


def record_phrase(read, detector, timeout=None, phrase_limit=None, silence_ms=None, preroll_ms=300,
                  should_stop=None):
    """Record one phrase from read() (a 20 ms frame source). Returns PCM bytes, or b"" if nobody spoke.

    Waits up to `timeout` seconds for speech, keeps `preroll_ms` from before it started, and stops after
    `silence_ms` of silence or `phrase_limit` seconds of speech.
    """
    timeout = config.LISTEN_TIMEOUT if timeout is None else timeout
    phrase_limit = config.PHRASE_LIMIT if phrase_limit is None else phrase_limit
    silence_ms = config.END_SILENCE_MS if silence_ms is None else silence_ms
    preroll = collections.deque(maxlen=max(1, preroll_ms // FRAME_MS))
    wait_frames = int(timeout * 1000 / FRAME_MS)
    limit_frames = int(phrase_limit * 1000 / FRAME_MS)
    silence_frames = max(1, silence_ms // FRAME_MS)

    voiced_run = 0
    for _ in range(wait_frames):  # wait for 2 voiced frames in a row (40 ms) so a click doesn't count
        if should_stop and should_stop():
            return b""
        frame = read()
        preroll.append(frame)
        voiced_run = voiced_run + 1 if detector.is_speech(frame) else 0
        if voiced_run >= 2:
            break
    else:
        return b""
    timing.mark("speech")
    frames, quiet = list(preroll), 0
    while len(frames) < limit_frames:
        frame = read()
        frames.append(frame)
        quiet = 0 if detector.is_speech(frame) else quiet + 1
        if quiet >= silence_frames:
            frames = frames[:len(frames) - quiet + max(1, quiet // 3)]  # keep a little trailing silence
            break
    timing.mark("end")
    return b"".join(frames)


# ---------------------------------------------------------------- wake word
def _skip_sklearn_import():
    """openwakeword/__init__ imports its custom-verifier trainer, which pulls in scikit-learn (6-12 s on a
    slow laptop). Jarvis never trains verifiers, so register a stub for that one module instead."""
    import sys
    import types

    name = "openwakeword.custom_verifier_model"
    if name in sys.modules or "openwakeword" in sys.modules:
        return

    def train_custom_verifier(*args, **kwargs):
        raise NotImplementedError("custom verifier training is disabled in Jarvis")

    stub = types.ModuleType(name)
    stub.train_custom_verifier = train_custom_verifier
    sys.modules[name] = stub


class WakeWord:
    """openWakeWord "hey jarvis", fully offline. Raises on load if the library or model is missing."""

    MODEL = "hey_jarvis"
    CHUNK_FRAMES = 4  # openWakeWord scores 80 ms chunks (4 x 20 ms)

    def __init__(self, sensitivity=None):
        self.sensitivity = config.WAKE_SENSITIVITY if sensitivity is None else sensitivity
        self.model = self._load()
        self._buffer = []

    def _load(self):
        _skip_sklearn_import()
        import openwakeword
        import openwakeword.utils
        from openwakeword.model import Model

        try:
            return Model(wakeword_models=[self.MODEL], inference_framework="onnx")
        except (ValueError, FileNotFoundError, OSError):
            log.info("[wake] downloading the %s model (one time)...", self.MODEL)
            openwakeword.utils.download_models([self.MODEL])
            return Model(wakeword_models=[self.MODEL], inference_framework="onnx")

    def feed(self, frame):
        """Add a 20 ms frame. Returns True when the wake word is heard."""
        import numpy as np

        self._buffer.append(frame)
        if len(self._buffer) < self.CHUNK_FRAMES:
            return False
        chunk = np.frombuffer(b"".join(self._buffer), dtype=np.int16)
        self._buffer = []
        scores = self.model.predict(chunk)
        score = max(scores.values()) if scores else 0.0
        if score >= self.sensitivity:
            log.debug("[wake] heard (score %.2f)", score)
            self.reset()
            return True
        return False

    def reset(self):
        self._buffer = []
        try:
            self.model.reset()
        except Exception:
            pass
