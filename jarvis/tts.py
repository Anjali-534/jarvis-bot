"""Natural speech: Microsoft's free neural voices (edge-tts), played with pygame's mixer.

* EdgeTTS.synth(text) -> mp3 bytes. Needs internet; raises on failure or after TTS_TIMEOUT seconds, and then
  stays "down" for a minute so every sentence doesn't wait for the timeout again (Voice falls back to pyttsx3).
* Short phrases (up to CACHE_MAX_CHARS) are cached as mp3 in data/tts_cache/, so "Ho gaya!" or "Haan, bolo?"
  play instantly the second time. pregenerate() fills the cache with the common ones in the background.
* Player.play(mp3, stop) plays and returns early if `stop` (a threading.Event) is set: barge-in.
"""
import asyncio
import hashlib
import io
import logging
import os
import threading
import time

import config

log = logging.getLogger(__name__)

CACHE_MAX_CHARS = 60
DOWN_FOR = 60.0  # seconds to skip edge-tts after it failed


def cache_path(text, voice=None, rate=None):
    key = f"{voice or config.TTS_VOICE}|{rate or config.TTS_RATE}|{text}".encode("utf-8")
    return config.TTS_CACHE_DIR / (hashlib.sha1(key).hexdigest()[:20] + ".mp3")


class EdgeTTS:
    def __init__(self, voice=None, rate=None):
        import edge_tts  # ImportError: the caller uses pyttsx3

        self._edge = edge_tts
        self.voice = voice or config.TTS_VOICE
        self.rate = rate or config.TTS_RATE
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def available(self):
        return time.monotonic() >= self._down_until

    def mark_down(self, reason):
        with self._lock:
            if self.available:
                log.warning("[voice] Neural voice unavailable (%s); using the offline voice for a minute.", reason)
            self._down_until = time.monotonic() + DOWN_FOR

    def _download(self, text, timeout):
        async def collect():
            audio = bytearray()
            communicate = self._edge.Communicate(text, self.voice, rate=self.rate)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    audio += chunk["data"]
            return bytes(audio)

        async def bounded():
            return await asyncio.wait_for(collect(), timeout)

        return asyncio.run(bounded())

    def synth(self, text, timeout=None):
        """mp3 bytes for `text` (from the cache for short phrases). Raises if edge-tts is down or fails."""
        path = cache_path(text, self.voice, self.rate) if len(text) <= CACHE_MAX_CHARS else None
        if path is not None and path.exists():
            return path.read_bytes()
        if not self.available:
            raise RuntimeError("neural voice is down")
        try:
            audio = self._download(text, timeout or config.TTS_TIMEOUT)
            if not audio:
                raise RuntimeError("no audio came back")
        except Exception as exc:
            self.mark_down(exc.__class__.__name__ if not str(exc) else str(exc)[:80])
            raise
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(audio)
            except OSError as exc:
                log.debug("[voice] couldn't cache %r: %s", text, exc)
        return audio


class Player:
    """Plays mp3 bytes through pygame's mixer (no window)."""

    def __init__(self):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame

        pygame.mixer.init(frequency=24000, channels=1)  # edge-tts sends 24 kHz mono mp3
        self._music = pygame.mixer.music

    def play(self, mp3, stop):
        """Play to the end, or until `stop` is set. Returns False if it was stopped."""
        self._music.load(io.BytesIO(mp3), "mp3")
        self._music.play()
        try:
            while self._music.get_busy():
                if stop.is_set():
                    self._music.stop()
                    return False
                time.sleep(0.02)
            return True
        finally:
            try:
                self._music.unload()
            except Exception:
                pass


def common_phrases():
    """The short lines worth having ready: every variant of the quick replies, in the current personality."""
    from jarvis import persona

    keys = ("yes", "done", "didnt_catch", "back", "one_sec", "never_mind", "not_done", "checking_online", "thanks",
            "hello", "sleep", "goodbye_short")
    phrases = []
    for key in keys:
        for template in persona.variants(key):
            try:
                text = template.format(name=persona.nickname(), assistant=config.ASSISTANT_NAME,
                                       part_of_day=persona.part_of_day(), **persona._verb_forms())
            except (KeyError, IndexError):
                continue
            if len(text) <= CACHE_MAX_CHARS:
                phrases.append(text)
    return phrases


def pregenerate(engine, phrases=None, busy=lambda: False):
    """Download the common phrases that aren't cached yet (background thread at startup). Waits while Jarvis is
    speaking (`busy`), so live sentences don't queue behind it. Returns how many were downloaded."""
    made = 0
    for text in phrases if phrases is not None else common_phrases():
        while busy():
            time.sleep(0.2)
        if not engine.available:
            break
        if cache_path(text, engine.voice, engine.rate).exists():
            continue
        try:
            engine.synth(text)
            made += 1
        except Exception:
            break
    if made:
        log.info("[voice] cached %d common phrases in %s", made, config.TTS_CACHE_DIR)
    return made
