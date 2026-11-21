"""Jarvis entry point: wake-word loop around the skill router.

    python main.py            voice mode with wake word
    python main.py --no-wake  voice mode, every phrase is a command
    python main.py --text     type instead of talking (wake word off)
    python main.py --mute     print replies instead of speaking them
"""
import argparse
import re
import time

import config
import jarvis.skills  # noqa: F401  (registers all skills)
from jarvis.router import Context, handle
from jarvis.skills.general import time_of_day


def _wake_words():
    words = set(config.WAKE_WORDS)
    words |= {w.replace("ok ", "okay ") for w in words if w.startswith("ok ")}
    words |= {"जार्विस", "हे जार्विस"}  # how Hindi speech recognition writes "Jarvis"
    return sorted(words, key=len, reverse=True)


# If a trailing "jarvis" follows one of these, it's part of the command (e.g. a project name).
_DANGLING = re.compile(r"\b(?:of|in|for|on|to|at|about|from|with|into|called|named|open|run|start|pull|"
                       r"launch|close|status|work on)$")


# If a leading "jarvis" is followed by one of these, it's the subject ("जार्विस का गिट स्टेटस", "jarvis project ...").
_SUBJECT_NEXT = re.compile(r"^(?:का|की|के|को|में|मे|से|पर|प्रोजेक्ट|project|folder|repo)(?:\s|$)")


def strip_wake_word(text, wake_words=None):
    """Return (woke, command). The wake word is stripped from the start, end or middle."""
    words = wake_words or _wake_words()
    t = re.sub(r"\s+", " ", (text or "").lower()).strip()
    alternatives = "|".join(re.escape(w) for w in words)
    if re.fullmatch(rf"(?:please\s+)?(?:turn off|shut ?down|stop|close|bye|goodbye|good bye|exit|quit)[\s,]+(?:{alternatives})[.!]?", t):
        return True, "exit"
    for word in words:
        w = re.escape(word)
        lead = re.fullmatch(rf"{w}(?!\w)[\s,.!?]*(.*)", t)
        if lead:
            rest = lead.group(1).strip()
            if _SUBJECT_NEXT.match(rest):
                return True, t  # the name is the project, not the wake word
            return True, rest
        trail = re.fullmatch(rf"(.*?)[\s,]*\b{w}[\s.!?]*", t)
        if trail:
            before = trail.group(1).strip()
            if _DANGLING.search(before):
                return True, t  # "git status of jarvis": the name is the project, not the wake word
            return True, before
    for word in words:
        middle = re.search(rf"\b{re.escape(word)}\b[\s,]*", t)
        if middle:
            after = t[middle.end():].strip()
            return True, after or t[:middle.start()].strip(" ,")
    return False, t


def run(text_mode=False, wake_enabled=True, mute=False, voice=None):
    if voice is None:
        from jarvis.voice import Voice

        voice = Voice(text_mode=text_mode, mute=mute)
    if getattr(voice, "text_mode", False):
        wake_enabled = False  # typing "jarvis" every time is pointless
    ctx = Context(voice)
    ctx.say(f"Good {time_of_day()}, {config.USER_NAME}. {config.ASSISTANT_NAME} is online.")
    name = config.ASSISTANT_NAME
    print(f"Say '{name}, help' to hear what I can do. Full list: COMMANDS.md"
          if wake_enabled else "Say 'help' to hear what I can do. Full list: COMMANDS.md")
    last_reply = time.monotonic()
    misses = 0
    try:
        while ctx.running:
            heard = voice.listen()
            if not heard:
                if not wake_enabled and not ctx.sleeping:
                    misses += 1
                    if misses >= 2:
                        ctx.say("Sorry, I didn't catch that.")
                        misses = 0
                continue
            misses = 0
            woke, command = strip_wake_word(heard)
            if ctx.sleeping:
                if not woke:
                    continue
                ctx.sleeping = False
                if not command:
                    ctx.say("I'm back. Yes?")
                    last_reply = time.monotonic()
                    continue
            elif wake_enabled and not woke and time.monotonic() - last_reply > config.FOLLOW_UP_SECONDS:
                continue
            if not command:
                ctx.say("Yes?")
            else:
                handle(ctx, command)
            last_reply = time.monotonic()
    except KeyboardInterrupt:
        print()
        ctx.say(f"Goodbye, {config.USER_NAME}.")
    finally:
        close = getattr(voice, "close", None)
        if close:
            close()
    return ctx


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"{config.ASSISTANT_NAME} personal voice assistant")
    parser.add_argument("--text", action="store_true", help="type commands instead of speaking")
    parser.add_argument("--no-wake", action="store_true", help="don't require the wake word")
    parser.add_argument("--mute", action="store_true", help="print replies instead of speaking them")
    args = parser.parse_args(argv)
    run(text_mode=args.text, wake_enabled=config.WAKE_WORD_ENABLED and not args.no_wake, mute=args.mute)


if __name__ == "__main__":
    main()
