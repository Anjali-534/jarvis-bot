"""Jarvis in a console window.

    python main.py            voice mode with wake word (Ctrl+Alt+J also wakes it)
    python main.py --no-wake  voice mode, every phrase is a command
    python main.py --text     type instead of talking (wake word off)
    python main.py --mute     print replies instead of speaking them
    python main.py --timing   print how long each step of every turn took

To run in the background with a tray icon instead, use run_jarvis.pyw.
"""
import argparse
import logging

import config
import jarvis.skills  # noqa: F401  (registers all skills)
from jarvis import hotkey
from jarvis.assistant import Assistant, strip_wake_word  # noqa: F401  (strip_wake_word re-exported for tests)
from jarvis.instance import SingleInstance
from jarvis.logs import setup_logging

log = logging.getLogger("jarvis.main")


def run(text_mode=False, wake_enabled=True, mute=False, voice=None, use_hotkey=False):
    """Run the listening loop in this thread until goodbye. Returns the Context."""
    if voice is None:
        from jarvis.voice import Voice

        voice = Voice(text_mode=text_mode, mute=mute)
    assistant = Assistant(voice, wake_enabled=wake_enabled)
    listener = hotkey.start(assistant.wake) if use_hotkey and not getattr(voice, "text_mode", False) else None
    try:
        return assistant.run()
    finally:
        if listener:
            listener.stop()
        close = getattr(voice, "close", None)
        if close:
            close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"{config.ASSISTANT_NAME} personal voice assistant")
    parser.add_argument("--text", action="store_true", help="type commands instead of speaking")
    parser.add_argument("--no-wake", action="store_true", help="don't require the wake word")
    parser.add_argument("--mute", action="store_true", help="print replies instead of speaking them")
    parser.add_argument("--timing", action="store_true", help="print per-turn latency (wake, STT, skill, TTS)")
    args = parser.parse_args(argv)
    setup_logging(console=True)
    if args.timing:
        from jarvis import timing

        timing.ENABLED = True

    instance = SingleInstance()
    if not instance.acquire():
        instance.signal_existing()
        log.info(f"{config.ASSISTANT_NAME} is already running (look for the tray icon). Not starting a second copy.")
        return 1
    try:
        run(text_mode=args.text, wake_enabled=config.WAKE_WORD_ENABLED and not args.no_wake, mute=args.mute,
            use_hotkey=True)
    finally:
        instance.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
