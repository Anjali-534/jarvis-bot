"""WhatsApp Desktop: send messages, open a chat to call, and manage contacts.

Contacts live in data/contacts.json as {"mom": "919876543210"} (git-ignored).
Every message is read back and confirmed before it is sent.
"""
import difflib
import json
import os
import re
import sys
import time
import urllib.parse
import webbrowser

import config
from jarvis.router import skill

WA = r"whats\s?app"
ADD_HOW = "Say add contact, then their name and number, or add them to data slash contacts dot json."


# ---------------------------------------------------------------- side effects (replaced in tests)
def open_uri(uri):
    os.startfile(uri)


def open_web(url):
    webbrowser.open(url)


def press_enter():
    import pyautogui

    pyautogui.press("enter")


def sleep(seconds):
    time.sleep(seconds)


def whatsapp_installed():
    """True if the whatsapp:// protocol is registered (WhatsApp Desktop is installed)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "whatsapp"))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- contacts
def load_contacts():
    path = config.CONTACTS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        print(f"[whatsapp] {path} is not valid JSON")
        return {}
    return {str(k).strip().lower(): str(v) for k, v in data.items() if str(k).strip()}


def save_contact(name, number):
    contacts = load_contacts()
    contacts[name.strip().lower()] = number
    path = config.CONTACTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contacts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_number(raw):
    """'98765 43210' -> '919876543210'. Returns None if it doesn't look like a phone number."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return "91" + digits
    if 11 <= len(digits) <= 15:
        return digits
    return None


def spoken_number(number):
    """Group digits so TTS reads them clearly: 919876543210 -> '91 98765 43210'."""
    if len(number) == 12 and number.startswith("91"):
        return f"+91 {number[2:7]} {number[7:]}"
    return "+" + " ".join(re.findall(r"\d{1,4}", number))


def resolve_contact(text, contacts):
    """Return (name, number, leftover_message) or None. Handles typos and 'whatsapp mom i'm late'."""
    text = re.sub(r"^(?:my|to)\s+", "", (text or "").strip().lower()).strip(" .,")
    if not text or not contacts:
        return None
    if text in contacts:
        return text, contacts[text], ""
    close = difflib.get_close_matches(text, list(contacts), n=1, cutoff=0.7)
    if close:
        return close[0], contacts[close[0]], ""
    words = text.split()
    for size in range(min(len(words) - 1, 3), 0, -1):  # contact name followed by the message
        head, rest = " ".join(words[:size]), " ".join(words[size:])
        match = head if head in contacts else next(iter(difflib.get_close_matches(head, list(contacts), n=1, cutoff=0.8)), None)
        if match:
            return match, contacts[match], rest
    return None


_SEP = r"(?:saying|that says|which says|says|that|to say|and say|telling (?:her|him|them)|:)"
_SEND_PATTERNS = [
    re.compile(rf"^(?:send|write)\s+(?:a\s+|an\s+)?(?:{WA}(?:\s+message)?|message|text|msg)\s+to\s+(.+?)"
               rf"(?:\s+on\s+{WA})?(?:\s+{_SEP}\s+(.+))?$"),
    re.compile(rf"^(?:{WA}|message|text|msg|tell)\s+(?:to\s+)?(.+?)(?:\s+on\s+{WA})?(?:\s+{_SEP}\s+(.+))?$"),
]


def parse_message_command(command):
    """'send a whatsapp to mom saying i'll be late' -> ('mom', "i'll be late")."""
    command = re.sub(r"^(?:please|jarvis|hey jarvis)\s+", "", command.strip().lower()).rstrip(" .?!")
    for pattern in _SEND_PATTERNS:
        match = pattern.match(command)
        if match:
            contact = re.sub(rf"\s+on\s+{WA}$", "", match.group(1)).strip()
            return contact, (match.group(2) or "").strip() or None
    return None


def tidy_message(text):
    text = re.sub(r"\bi\b", "I", text.strip())
    text = re.sub(r"\bi(?='(?:m|ll|ve|d)\b)", "I", text)
    return text[:1].upper() + text[1:]


def send(number, text):
    """Open the chat with the text filled in, wait, press Enter. Returns 'desktop' or 'web'."""
    encoded = urllib.parse.quote(text)
    if whatsapp_installed():
        open_uri(f"whatsapp://send?phone={number}&text={encoded}")
        sleep(config.WHATSAPP_SEND_DELAY)
        press_enter()
        return "desktop"
    open_web(f"https://web.whatsapp.com/send?phone={number}&text={encoded}")
    sleep(config.WHATSAPP_SEND_DELAY + 12)  # WhatsApp Web takes much longer to load
    press_enter()
    return "web"


# ---------------------------------------------------------------- skills
@skill("whatsapp add contact",
       r"^(?:please\s+)?(?:add|save|create)\s+(?:a\s+)?(?:new\s+)?(?:whats\s?app\s+)?contact\s+(.+?)\s+"
       r"(?:with\s+(?:the\s+)?(?:phone\s+)?number\s+|number\s+|as\s+|on\s+)?(\+?\d[\d\s-]{7,}\d)$")
def add_contact(ctx, command, match):
    name = re.sub(r"\s+(?:with|number|phone)$", "", match.group(1).strip(" ,")).strip()
    number = normalize_number(match.group(2))
    if not name or not number:
        return "That doesn't look like a valid phone number. Say the 10-digit mobile number."
    existing = load_contacts().get(name.lower())
    verb = "Update" if existing else "Save"
    if not ctx.confirm(f"{verb} {name.title()} as {spoken_number(number)}?"):
        return "Okay, I didn't save it."
    save_contact(name, number)
    return f"Saved {name.title()}. You can now say: send a WhatsApp to {name}."


@skill("whatsapp call", rf"^(?:please\s+)?(?:video\s+|voice\s+)?call\s+(.+?)\s+on\s+{WA}$",
       rf"^(?:please\s+)?{WA}\s+(?:video\s+|voice\s+)?call\s+(?:to\s+)?(.+)$")
def call_contact(ctx, command, match):
    contacts = load_contacts()
    found = resolve_contact(match.group(1), contacts)
    if not found:
        return f"I don't have {match.group(1)} in your WhatsApp contacts. {ADD_HOW}"
    name, number, _ = found
    if not whatsapp_installed():
        return "WhatsApp Desktop isn't installed, and WhatsApp Web can't make calls. Please call from your phone."
    open_uri(f"whatsapp://send?phone={number}")
    kind = "video" if "video" in command else "voice"
    return f"I've opened your chat with {name.title()}. Tap the {kind} call button at the top to start the call."


@skill("whatsapp message", rf"^(?:please\s+)?(?:send|write|message|text|msg|tell)\b.*\b{WA}\b",
       rf"^(?:please\s+)?{WA}\s+\S", help="send a whatsapp to mom saying I'll be late")
def send_message(ctx, command, match):
    parsed = parse_message_command(command)
    if not parsed:
        return None
    contact_text, message = parsed
    contacts = load_contacts()
    if not contacts:
        return f"You haven't saved any WhatsApp contacts yet. {ADD_HOW}"
    found = resolve_contact(contact_text, contacts)
    if not found:
        return f"I don't have {contact_text} in your WhatsApp contacts. {ADD_HOW}"
    name, number, leftover = found
    message = message or leftover or ctx.ask("What should I say?")
    if not message or not message.strip():
        return "Okay, cancelled."
    message = tidy_message(message)
    if not ctx.confirm(f"Send '{message}' to {name.title()}?"):
        return "Okay, I didn't send it."
    how = send(number, message)
    if how == "web":
        return (f"Sent to {name.title()} using WhatsApp Web. "
                "If it asked you to scan a QR code, log in and try again.")
    return f"Sent to {name.title()}."
