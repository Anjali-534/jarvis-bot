"""Gmail: unread count, read one email on request, search, and save DRAFTS (Jarvis never sends email).

Also owns "connect google" / "disconnect google", which the Calendar skill shares.
Email bodies are only read aloud when asked for ("read the email from Dhruv").
"""
import base64
import difflib
import html
import logging
import re
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime

import config
from jarvis import google_auth
from jarvis.google_auth import friendly_errors
from jarvis.router import skill
from jarvis.skills.whatsapp import load_emails, tidy_message

log = logging.getLogger(__name__)

MAIL = r"e-?\s?mails?"
PRIMARY_UNREAD = "in:inbox category:primary is:unread"
MAX_COUNT = 100


def gmail():
    return google_auth.service("gmail", "v1")


# ---------------------------------------------------------------- message helpers
def headers_of(message):
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def sender_name(from_header):
    """'Dhruv Sharma <d@x.com>' -> 'Dhruv Sharma'; 'd@x.com' -> 'd'."""
    name, address = parseaddr(from_header or "")
    name = name.strip().strip('"')
    return name or (address.split("@")[0] if address else "someone")


def spoken_date(date_header):
    try:
        moment = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return ""
    return f"{moment.day} {moment.strftime('%B')}"


def list_ids(service, query, limit):
    result = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
    return [m["id"] for m in result.get("messages", [])], bool(result.get("nextPageToken"))


def metadata(service, message_id):
    return service.users().messages().get(userId="me", id=message_id, format="metadata",
                                          metadataHeaders=["From", "To", "Subject", "Date"]).execute()


def _decode(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def body_text(payload):
    """The plain text of a message payload (HTML converted to text if there's no plain part)."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload or {})
    if plain:
        return "\n".join(plain)
    if rich:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(rich))
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        return html.unescape(re.sub(r"<[^>]+>", " ", text))
    return ""


def strip_quoted(text):
    """Drop the quoted reply chain and signature so only the new part is summarised."""
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or re.match(r"^-{2,}\s*(?:original message|forwarded message)", stripped, re.I):
            break
        if re.match(r"^on .{4,120} wrote:$", stripped, re.I) or stripped == "--":
            break
        lines.append(stripped)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def first_sentences(text, count=2, limit=300):
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    summary = " ".join(sentences[:count]).strip()
    if len(summary) > limit:
        summary = summary[:limit].rsplit(" ", 1)[0] + "..."
    return summary


def summarize(text):
    """An AI summary if an AI key is set, otherwise the first two sentences."""
    text = strip_quoted(text)
    if not text:
        return ""
    if config.AI_API_KEY:
        from jarvis.skills.ai import complete, strip_markdown

        try:
            status, reply = complete("Summarise this email in at most two short sentences of plain spoken English. "
                                     "No markdown, no URLs.", [], text[:4000])
            if status < 400 and strip_markdown(reply):
                return strip_markdown(reply)
            log.warning("[gmail] AI summary failed with HTTP %s", status)
        except Exception as exc:
            log.warning("[gmail] AI summary failed: %s", exc)
    return first_sentences(text)


# ---------------------------------------------------------------- addresses
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def spoken_email(text):
    """'riya dot sharma at gmail dot com' -> 'riya.sharma@gmail.com' (None if it isn't an address)."""
    text = (text or "").strip().lower()
    found = EMAIL_RE.search(text)
    if found:
        return found.group(0)
    candidate = re.sub(r"\s+at\s+", "@", text)
    candidate = re.sub(r"\s*\bdot\b\s*", ".", candidate)
    candidate = re.sub(r"\s*\b(?:underscore)\b\s*", "_", candidate).replace(" ", "")
    return candidate if EMAIL_RE.fullmatch(candidate) else None


def recent_address(service, name):
    """Find someone's address in recent mail: senders first, then people you've written to."""
    words = [w for w in re.split(r"\s+", name.lower()) if w]
    for query, header in ((f"from:({name})", "from"), (f"in:sent to:({name})", "to")):
        ids, _ = list_ids(service, query, 5)
        for message_id in ids:
            for person in headers_of(metadata(service, message_id)).get(header, "").split(","):
                display, address = parseaddr(person)
                haystack = f"{display} {address}".lower()
                if address and all(w in haystack for w in words):
                    return display.strip('"') or name.title(), address
    return None


def find_recipient(service, name):
    """Return (display name, address) from an address said aloud, contacts.json, or recent mail."""
    address = spoken_email(name)
    if address:
        return address, address
    name = re.sub(r"^(?:my|to)\s+", "", name.strip().lower())
    emails = load_emails()
    if name in emails:
        return name.title(), emails[name]
    close = difflib.get_close_matches(name, list(emails), n=1, cutoff=0.75)
    if close:
        return close[0].title(), emails[close[0]]
    return recent_address(service, name)


# ---------------------------------------------------------------- check / search / read
@skill("check email",
       rf"^(?:please\s+)?(?:check|read)\s+(?:my\s+)?(?:{MAIL}|inbox|gmail)$",
       rf"\bany\s+(?:new\s+|unread\s+)?{MAIL}\b", rf"\b(?:new|unread)\s+{MAIL}\b(?!\s+(?:to|from)\b)",
       rf"\bdo i have (?:any\s+)?(?:new\s+|unread\s+)?(?:{MAIL})\b", rf"^(?:my\s+)?(?:{MAIL}|inbox)$",
       category="email", help=["check my email", "any new emails"])
@friendly_errors
def check_email(ctx, command, match):
    if re.match(rf"^(?:e-?\s?mail|mail)\s", command):
        return None  # "email dhruv saying the new emails look fine" is a draft
    service = gmail()
    ids, more = list_ids(service, PRIMARY_UNREAD, MAX_COUNT)
    if not ids:
        return "You have no unread emails in your Primary inbox."
    count = f"more than {MAX_COUNT}" if more else str(len(ids))
    plural = "" if count == "1" else "s"
    top = [headers_of(metadata(service, i)) for i in ids[:5]]
    items = [f"from {sender_name(h.get('from'))}: {h.get('subject') or 'no subject'}" for h in top]
    intro = f"You have {count} unread email{plural} in your Primary inbox."
    if len(ids) > 5:
        intro += " The latest 5:"
    return f"{intro} " + "; ".join(items) + "."


@skill("search email",
       rf"\bsearch\s+(?:my\s+)?(?:{MAIL}|gmail|inbox)\s+(?:for\s+)?(.+)$",
       rf"\b(?:find|look for)\s+(?:the\s+|my\s+)?{MAIL}\s+(?:about|for|with|containing|regarding)\s+(.+)$",
       category="email", help=["search email for invoice"])
@friendly_errors
def search_email(ctx, command, match):
    query = match.group(1).strip(" .?")
    service = gmail()
    ids, _ = list_ids(service, query, 3)
    if not ids:
        return f"I didn't find any emails matching {query}."
    parts = []
    for n, message_id in enumerate(ids, 1):
        h = headers_of(metadata(service, message_id))
        when = spoken_date(h.get("date", ""))
        parts.append(f"{n}: from {sender_name(h.get('from'))}, {h.get('subject') or 'no subject'}"
                     + (f", on {when}" if when else ""))
    return f"Top {len(ids)} match{'es' if len(ids) != 1 else ''} for {query}. " + ". ".join(parts) + "."


@skill("read email",
       rf"\bread\s+(?:me\s+)?(?:the\s+|my\s+)?(?:latest\s+|last\s+|new\s+|recent\s+)?{MAIL}\s+from\s+(.+)$",
       rf"\bwhat did\s+(.+?)\s+(?:e-?\s?mail|write|send)(?:\s+me)?$",
       category="email", help=["read the email from Dhruv"])
@friendly_errors
def read_email(ctx, command, match):
    who = match.group(1).strip(" .?")
    service = gmail()
    emails = load_emails()
    address = emails.get(who) or spoken_email(who)
    ids, _ = list_ids(service, f"in:inbox from:({address or who})", 1)
    if not ids:
        return f"I couldn't find an email from {who} in your inbox."
    message = service.users().messages().get(userId="me", id=ids[0], format="full").execute()
    h = headers_of(message)
    summary = summarize(body_text(message.get("payload"))) or first_sentences(html.unescape(message.get("snippet", "")))
    intro = f"The latest email from {sender_name(h.get('from'))}, subject {h.get('subject') or 'none'}."
    return f"{intro} {summary}" if summary else f"{intro} It doesn't have any text."


# ---------------------------------------------------------------- drafts
_SEP = r"(?:\s+(?:saying|that says|which says|to say|telling (?:him|her|them)|that)\s+|\s*:\s*)"
COMPOSE_RE = re.compile(rf"^(.+?)(?:\s+(?:about|regarding|with (?:the )?subject)\s+(.+?))?(?:{_SEP}(.+))?$")


def parse_compose(text):
    """'dhruv about the demo saying it's at 5' -> ('dhruv', 'the demo', "it's at 5")."""
    text = re.sub(r"^(?:to\s+)", "", text.strip().rstrip(" .?!"))
    match = COMPOSE_RE.match(text)
    if not match:
        return None
    return match.group(1).strip(), (match.group(2) or "").strip() or None, (match.group(3) or "").strip() or None


def subject_from(body):
    words = re.sub(r"[.!?,;:]+$", "", body).split()
    subject = " ".join(words[:6]) + ("..." if len(words) > 6 else "")
    return subject[:1].upper() + subject[1:]


def create_draft(service, to, subject, body):
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()


@skill("email draft",
       rf"^(?:please\s+)?(?:send|write|draft|compose)\s+(?:an?\s+)?(?:new\s+)?{MAIL}\s+to\s+(.+)$",
       rf"^(?:please\s+)?(?:e-?\s?mail|mail)\s+(?!(?:from|to me)\b)(.+)$",
       category="email", help=["email Dhruv saying the demo is at 5", "write an email to Riya about the trip"])
@friendly_errors
def email_draft(ctx, command, match):
    parsed = parse_compose(match.group(1))
    if not parsed:
        return None
    who, subject, body = parsed
    service = gmail()
    found = find_recipient(service, who)
    if not found:
        return (f"I couldn't find an email address for {who}. Add it to data, contacts dot json, "
                f'for example "{who}": {{"email": "name@example.com"}}, or say the address.')
    name, address = found
    body = body or ctx.ask("What should the email say?")
    if not body or not body.strip():
        return "Okay, cancelled."
    body = tidy_message(body)
    subject = tidy_message(subject) if subject else subject_from(body)
    if not ctx.confirm(f"Here's the draft to {name}, {address}. Subject: {subject}. Message: {body}. "
                       "Save it as a draft?"):
        return "Okay, I didn't save it."
    create_draft(service, address, subject, body)
    return "Draft saved in Gmail. Open Gmail to check it and press send when you're ready."


# ---------------------------------------------------------------- connect
@skill("connect google",
       r"\b(?:connect|link|sign in to|sign into|log ?in to|set up|setup)\s+(?:my\s+)?(?:google|gmail|google calendar|"
       r"calendar)(?:\s+account)?$", category="email", help=["connect google"])
def connect_google(ctx, command, match):
    if not google_auth.libraries_ok():
        return google_auth.MISSING_LIBS
    if not google_auth.has_client_file():
        log.info(google_auth.setup_steps())
        return ("First I need a Google OAuth client file. I've printed the setup steps in the terminal and the log, "
                "and they're in the README too. Save the file as data, google credentials dot json, "
                "then say connect google again.")
    if google_auth.is_connected() and not ctx.confirm("Google is already connected. Do you want to sign in again?"):
        return "Okay, keeping the current connection."
    ctx.say("I'm opening Google sign-in in your browser. Choose your account and allow access.")
    try:
        google_auth.run_flow()
    except Exception as exc:
        log.warning("[google] sign-in failed: %s", exc)
        return "The Google sign-in didn't finish, so nothing changed. Say connect google to try again."
    from jarvis.skills import calendar

    calendar.start_reminders(ctx)
    return "Google is connected. Try: check my email, or what's on my calendar today."


@skill("disconnect google", r"\b(?:disconnect|unlink|sign out of|log ?out of|remove)\s+(?:my\s+)?(?:google|gmail)"
                            r"(?:\s+account)?$", category="email", help=["disconnect google"])
def disconnect_google(ctx, command, match):
    if not google_auth.is_connected():
        return "Google isn't connected."
    if not ctx.confirm("Disconnect your Google account from Jarvis?"):
        return "Okay, still connected."
    google_auth.disconnect()
    from jarvis.skills import calendar

    calendar.stop_reminders()
    return "Disconnected. Say connect google to sign in again."
