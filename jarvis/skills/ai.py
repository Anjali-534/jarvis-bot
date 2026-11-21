"""AI fallback: anything no other skill handled goes to an OpenAI-compatible chat API."""
import re
from datetime import datetime

import config
from jarvis import http
from jarvis.router import fallback, skill

MAX_TURNS = 8


def system_prompt():
    return (
        f"You are {config.ASSISTANT_NAME}, a friendly voice assistant for {config.USER_NAME}, who lives in India. "
        f"The current date and time is {datetime.now():%A %d %B %Y, %I:%M %p}. "
        "Your replies are spoken aloud, so answer in 1 to 3 short sentences of plain speech. "
        "Never use markdown, bullet points, code blocks, emojis or URLs."
    )


def strip_markdown(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__|\*|_|~~)(.+?)\1", r"\2", text)
    return re.sub(r"\s+", " ", text).strip()


@skill("forget conversation", r"\bforget (?:our|the|this|my) (?:conversation|chat)\b",
       r"\b(?:clear|reset) (?:the |our )?(?:chat|conversation|history)\b", r"\bstart (?:a )?new (?:chat|conversation)\b")
def forget(ctx, command, match):
    ctx.state["ai_history"] = []
    return "Okay, I've forgotten our conversation."


@fallback
def ai(ctx, command, match=None):
    if not config.AI_API_KEY:
        return ("I don't know how to answer that yet. "
                "To let me answer anything, add an AI_API_KEY to the .env file.")
    history = ctx.state.setdefault("ai_history", [])
    messages = [{"role": "system", "content": system_prompt()}, *history, {"role": "user", "content": command}]
    response = http.post_json(
        f"{config.AI_BASE_URL}/chat/completions",
        {"model": config.AI_MODEL, "messages": messages, "temperature": 0.6, "max_tokens": 300},
        headers={"Authorization": f"Bearer {config.AI_API_KEY}"},
    )
    if response.status_code == 401:
        return "My AI key was rejected. Please check the AI_API_KEY in your .env file."
    if response.status_code == 429:
        return "The AI service says I've hit a rate limit or I'm out of credit. Please try again later."
    if response.status_code >= 400:
        print(f"[ai] HTTP {response.status_code}: {response.text[:300]}")
        return f"The AI service returned an error, code {response.status_code}."
    reply = strip_markdown(response.json()["choices"][0]["message"]["content"] or "")
    if not reply:
        return "I'm not sure what to say to that."
    history.extend([{"role": "user", "content": command}, {"role": "assistant", "content": reply}])
    del history[:-MAX_TURNS * 2]
    return reply
