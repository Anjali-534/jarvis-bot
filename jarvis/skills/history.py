""""What did you just do?" and "undo that": the action log kept by jarvis.actions (data/logs/actions.log)."""
import re

from jarvis import actions, hinglish as hi
from jarvis.router import join_and, skill


@skill("what did you do", r"\bwhat\s+(?:did|have)\s+you\s+(?:just\s+)?(?:do|done|change|changed)\b",
       r"\bwhat was (?:that|the last thing you did)\b", r"\b(?:tumne|tune|aapne)\s+(?:abhi\s+)?kya\s+kiya\b",
       r"^(?:show|read)\s+(?:me\s+)?(?:the\s+|your\s+)?(?:action\s+)?(?:log|history)$",
       category="laptop", help=["what did you just do"])
def what_did_you_do(ctx, command, match):
    recent = actions.recent(3)
    if recent:
        lines = [a.what for a in recent]
    else:
        lines = actions.read_log_tail(3)
    if not lines:
        return "I haven't done anything on your laptop yet."
    last = lines[0]
    reply = f"Last, I {_past(last)}."
    if len(lines) > 1:
        reply += " Before that: " + join_and(_past(line) for line in lines[1:]) + "."
    return reply


def _past(text):
    """'Opened Notepad' -> 'opened Notepad' (it follows 'I')."""
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


@skill("undo that", r"^(?:please\s+)?(?:undo|revert|reverse|take back|cancel)\s+(?:that|this|it|the last (?:thing|action|change)|"
                    r"what you (?:just )?did|your last action)$",
       r"^(?:please\s+)?put it back$", rf"{hi.LEAD}(?:wapas|waapas|vapas)\s+(?:kar|karo|kar do|kar de|le lo|rakh do)$",
       rf"{hi.LEAD}(?:woh|wo|ye|yeh)\s+undo{hi.KAR}$",  # plain "undo" is Ctrl+Z (keyboard_control)
       category="laptop", help=["undo that"])
def undo_that(ctx, command, match):
    action = actions.last_undoable()
    if action is None:
        recent = actions.recent(1)
        if recent:
            return f"I can't undo the last thing I did: {_past(recent[0].what)}."
        return "There's nothing I can undo."
    if not ctx.confirm(f"Undo: {action.undo_text or action.what}?"):
        return "Okay, I left it."
    done, problem = actions.undo_last()
    if problem:
        return problem
    return "Undone: " + re.sub(r"^(\w)", lambda m: m.group(1).lower(), action.what) + "."
