"""Find, open and tidy your files by name: "open my resume", "find the bogie pitch deck", "zip the jarvis folder".

A background index keeps the NAMES and modified dates (never the contents) of everything in Desktop, Documents,
Downloads, Pictures, Videos, Music, OneDrive and PROJECT_ROOTS, skipping node_modules, .git, virtualenvs and
AppData. It's saved in data/file_index.json and rebuilt every 6 hours.

Changing files always asks first and can be undone ("undo that"): create folder, rename, move, zip, delete.
Delete only ever sends to the Recycle Bin. Nothing in Windows, Program Files, AppData or other system folders
is ever touched.
"""
import difflib
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config
from jarvis import actions, desktop, hinglish as hi
from jarvis.router import SpokenError, on_startup, skill, speak_list

log = logging.getLogger(__name__)

MAX_DEPTH = 8
MAX_ENTRIES = 300_000
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "env", "site-packages", "appdata", ".idea",
             ".vscode", ".next", ".nuxt", "dist", "build", ".cache", ".tox", ".mypy_cache", ".pytest_cache",
             ".gradle", "target", "bower_components", "$recycle.bin", ".svn", ".hg", "vendor", ".expo"}
SKIP_FILES = re.compile(r"^(?:desktop\.ini|thumbs\.db|~\$.*|\.ds_store)$|\.(?:tmp|crdownload|part|partial|lnk~)$",
                        re.IGNORECASE)
FOLDER_NAMES = {"desktop": "Desktop", "documents": "Documents", "document": "Documents", "docs": "Documents",
                "downloads": "Downloads", "download": "Downloads", "pictures": "Pictures", "photos": "Pictures",
                "videos": "Videos", "music": "Music", "onedrive": "OneDrive", "one drive": "OneDrive"}
_FOLDER = r"(desktop|documents|docs|downloads|pictures|photos|videos|music|onedrive|one drive)"

DOCS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages"}
SLIDES = {".pptx", ".ppt", ".key", ".odp", ".pdf"}
SHEETS = {".xlsx", ".xls", ".csv", ".ods", ".numbers"}
IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".svg"}
VIDEOS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"}
AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}
ARCHIVES = {".zip", ".rar", ".7z", ".tar", ".gz"}
# Spoken type words -> the extensions they mean. These words are optional in the name.
TYPE_WORDS = {
    "deck": SLIDES, "presentation": SLIDES, "slides": SLIDES, "ppt": SLIDES, "powerpoint": SLIDES,
    "pdf": {".pdf"}, "document": DOCS, "doc": DOCS, "word file": {".doc", ".docx"}, "sheet": SHEETS,
    "spreadsheet": SHEETS, "excel": SHEETS, "excel file": SHEETS, "photo": IMAGES, "picture": IMAGES,
    "image": IMAGES, "pic": IMAGES, "screenshot": IMAGES, "video": VIDEOS, "song": AUDIO, "audio": AUDIO,
    "recording": AUDIO, "zip": ARCHIVES, "archive": ARCHIVES,
}
SYNONYMS = {"resume": {"resume", "cv", "biodata"}, "cv": {"resume", "cv", "biodata"}, "photo": {"photo", "img", "image"},
            "pic": {"pic", "img", "image", "photo"}}
FILLER = {"my", "the", "a", "an", "file", "files", "that", "this", "called", "named", "latest", "new", "old",
          "meri", "mera", "mere", "wali", "wala", "wale", "ki", "ka", "ke", "please"}
RUNNABLE = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".scr", ".com", ".reg", ".hta",
            ".lnk", ".jar", ".appx", ".msix"}


@dataclass(frozen=True)
class Entry:
    path: str
    mtime: float
    is_dir: bool = False

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def stem(self):
        return self.name if self.is_dir else os.path.splitext(self.name)[0]

    @property
    def ext(self):
        return "" if self.is_dir else os.path.splitext(self.name)[1].lower()


# ---------------------------------------------------------------- folders
def known_folders():
    """{"Desktop": Path, ...}: where Windows really keeps them (OneDrive often moves Desktop and Documents)."""
    folders = {}
    try:
        from win32com.shell import shell, shellcon

        for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"):
            folders[name] = Path(shell.SHGetKnownFolderPath(getattr(shellcon, f"FOLDERID_{name}")))
    except Exception:
        home = Path.home()
        folders = {name: home / name for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")}
    if os.environ.get("OneDrive"):
        folders["OneDrive"] = Path(os.environ["OneDrive"])
    return folders


def folder(name):
    """'downloads' -> Path to the Downloads folder (or None)."""
    key = FOLDER_NAMES.get((name or "").lower().strip())
    path = known_folders().get(key) if key else None
    return path if path and path.exists() else None


def index_roots():
    roots, seen = [], set()
    for path in [*known_folders().values(), *config.PROJECT_ROOTS]:
        key = os.path.normcase(str(path))
        if key not in seen and Path(path).is_dir():
            seen.add(key)
            roots.append(Path(path))
    return roots


# ---------------------------------------------------------------- the index
def _is_venv(path):
    return os.path.exists(os.path.join(path, "pyvenv.cfg"))


def scan(roots=None, max_depth=MAX_DEPTH):
    """[Entry] for every file and folder name under the roots. Never opens a file."""
    entries, seen = [], set()
    stack = [(Path(r), 0) for r in (roots if roots is not None else index_roots())]
    while stack and len(entries) < MAX_ENTRIES:
        path, depth = stack.pop()
        key = os.path.normcase(str(path))
        if key in seen or actions.is_system(path):
            continue
        seen.add(key)
        try:
            with os.scandir(path) as listing:
                items = list(listing)
        except OSError:
            continue
        for item in items:
            name = item.name
            try:
                is_dir = item.is_dir(follow_symlinks=False)
                mtime = item.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if is_dir:
                if name.lower() in SKIP_DIRS or name.startswith((".", "$")) or _is_venv(item.path) or \
                        actions.is_system(item.path):
                    continue
                entries.append(Entry(item.path, mtime, True))
                if depth < max_depth:
                    stack.append((Path(item.path), depth + 1))
            elif not SKIP_FILES.search(name) and not name.startswith("."):
                entries.append(Entry(item.path, mtime, False))
    return entries


_index = {"entries": None, "built": None}
_index_lock = threading.Lock()


def build():
    started = time.monotonic()
    entries = scan()
    data = {"built": datetime.now().isoformat(timespec="seconds"),
            "entries": [[e.path, round(e.mtime), int(e.is_dir)] for e in entries]}
    try:
        config.FILE_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.FILE_INDEX_FILE.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    except OSError:
        log.warning("Couldn't save the file index", exc_info=True)
    log.info("[files] indexed %d names in %.1f s", len(entries), time.monotonic() - started)
    return data["built"], entries


def _stale(built):
    try:
        return datetime.now() - datetime.fromisoformat(built) > timedelta(hours=config.FILE_INDEX_HOURS)
    except (TypeError, ValueError):
        return True


def entries(refresh=False):
    """The indexed names: memory, then data/file_index.json, then a fresh scan."""
    if _index["entries"] is not None and not refresh and not _stale(_index["built"]):
        return _index["entries"]
    with _index_lock:
        if _index["entries"] is not None and not refresh and not _stale(_index["built"]):
            return _index["entries"]
        built, found = None, None
        if not refresh:
            try:
                data = json.loads(config.FILE_INDEX_FILE.read_text(encoding="utf-8"))
                if not _stale(data.get("built")):
                    built, found = data["built"], [Entry(p, m, bool(d)) for p, m, d in data["entries"]]
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if found is None:
            built, found = build()
        _index.update(entries=found, built=built)
        return found


def reset_index():
    _index.update(entries=None, built=None)


def add_to_index(path):
    """Keep the index right after Jarvis creates, renames or moves something (no full rescan)."""
    if _index["entries"] is None:
        return
    p = Path(path)
    try:
        _index["entries"].append(Entry(str(p), p.stat().st_mtime, p.is_dir()))
    except OSError:
        pass


def drop_from_index(path):
    if _index["entries"] is None:
        return
    key = os.path.normcase(str(path))
    _index["entries"] = [e for e in _index["entries"] if os.path.normcase(e.path) != key]


# ---------------------------------------------------------------- search and ranking
_CAMEL = re.compile(r"([a-z])([A-Z])")
_DIGITS = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_SPLIT = re.compile(r"[^a-z0-9]+")


def name_words(text):
    """'BogiePitchDeck_v2' -> ['bogie', 'pitch', 'deck', 'v', '2']"""
    text = _DIGITS.sub(" ", _CAMEL.sub(r"\1 \2", text))
    return [w for w in _SPLIT.split(text.lower()) if w]


def parse_query(query):
    """'the bogie pitch deck' -> (['bogie', 'pitch'], {'.pptx', ...}, ['pitch', 'deck'], wants_folder)."""
    text = (query or "").lower().strip(" .?!")
    text = re.sub(r"\s+dot\s+(\w{2,4})$", r".\1", text)       # "main dot py"
    wants_folder = bool(re.search(r"\bfolders?\b", text))
    text = re.sub(r"\bfolders?\b", " ", text)
    exts, type_words = set(), []
    explicit = re.search(r"\.(\w{1,5})$", text)
    if explicit:
        exts = {"." + explicit.group(1)}
        text = text[:explicit.start()]
    for phrase in sorted(TYPE_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}s?\b", text):
            exts |= TYPE_WORDS[phrase] if not explicit else set()
            type_words += phrase.split()
            text = re.sub(rf"\b{re.escape(phrase)}s?\b", " ", text)
    words = [w for w in name_words(text) if w not in FILLER]
    return words, exts, type_words, wants_folder


_words_cache = {"key": None, "names": [], "vocabulary": ()}


def _names_of(items):
    """Each entry's name words, and every word in the index, computed once per index (5,000+ names)."""
    key = (id(items), len(items))  # add_to_index appends to the same list
    if _words_cache["key"] != key:
        names = [name_words(e.stem) for e in items]
        _words_cache.update(key=key, names=names, vocabulary=sorted({w for ws in names for w in ws}))
    return _words_cache["names"], _words_cache["vocabulary"]


def _word_matches(word, vocabulary):
    """{index word: how well it matches the spoken word}: exact/synonym 1.0, prefix 0.9, close spelling 0.8."""
    found = {w: 1.0 for w in SYNONYMS.get(word, {word})}
    if len(word) >= 3:
        for w in vocabulary:
            if w.startswith(word) and w not in found:
                found[w] = 0.9
    for w in difflib.get_close_matches(word, vocabulary, n=20, cutoff=0.85):
        found.setdefault(w, 0.8)
    return found


def _word_score(word, names):
    """How well one spoken word matches a name (used by tests and small lists)."""
    matches = _word_matches(word, sorted(set(names)))
    return max((matches.get(n, 0.0) for n in names), default=0.0)


def rank(query, items=None, within=None, now=None):
    """[(score, Entry)] best first. Name words matter most; the file type, recency and depth break ties."""
    words, exts, type_words, wants_folder = parse_query(query)
    if not words and not type_words:
        return []
    now = now or time.time()
    items = entries() if items is None else items
    all_names, vocabulary = _names_of(items)
    wanted = [_word_matches(w, vocabulary) for w in words]
    within = os.path.normcase(str(within)) if within else None
    scored = []
    for entry, names in zip(items, all_names):
        if wants_folder and not entry.is_dir:
            continue
        if within and not os.path.normcase(entry.path).startswith(within):
            continue
        if not names:
            continue
        if words:
            word_score = sum(max((m.get(n, 0.0) for n in names), default=0.0) for m in wanted) / len(words)
            if word_score < (0.8 if len(words) == 1 else 0.6):
                continue
            ratio = difflib.SequenceMatcher(None, "".join(words), "".join(names)).ratio()
        else:  # only a type: "open my latest pdf"
            if not exts or entry.ext not in exts:
                continue
            word_score, ratio = 0.6, 0.0
        total = 0.7 * word_score + 0.25 * ratio
        total += 0.05 * sum(1 for w in type_words if w in names)
        if exts:
            total += 0.12 if entry.ext in exts else (-0.08 if entry.is_dir else -0.15)
        elif entry.is_dir and not wants_folder:
            total -= 0.03
        age_days = (now - entry.mtime) / 86400
        total += 0.06 if age_days < 7 else 0.03 if age_days < 60 else 0.0
        total -= min(0.06, 0.008 * entry.path.count(os.sep))
        scored.append((round(total, 4), entry))
    scored.sort(key=lambda pair: (-pair[0], -pair[1].mtime))
    return scored


TIE = 0.05


def where(entry):
    """'in Documents' / 'in the jarvis folder'."""
    parent = Path(entry.path).parent
    for name, path in known_folders().items():
        if os.path.normcase(str(parent)) == os.path.normcase(str(path)):
            return f"in {name}"
    return f"in the {parent.name} folder" if parent.name else ""


def when(entry, now=None):
    moment = datetime.fromtimestamp(entry.mtime)
    days = ((now or datetime.now()).date() - moment.date()).days
    if days <= 0:
        return "changed today"
    if days == 1:
        return "changed yesterday"
    if days < 30:
        return f"changed {days} days ago"
    return f"changed in {moment:%B %Y}"


def describe(entry):
    return f"{entry.name} {where(entry)}".strip()


def choose(ctx, query, ranked, verb="open"):
    """One clear winner, or read out the top 3 and ask which. Returns an Entry or None."""
    from jarvis.skills import app_index

    if not ranked:
        return None
    top = ranked[0][0]
    close = [e for s, e in ranked[:3] if top - s <= TIE]
    if len(close) == 1:
        return close[0]
    options = [e for _, e in ranked[:3]]
    listing = "; ".join(f"{n}, {describe(e)}" for n, e in enumerate(options, 1))
    answer = ctx.ask(f"I found {len(ranked)} matches. {listing}. Which one should I {verb}?")
    if not answer or re.search(r"\b(?:none|neither|nothing|no|nahi|cancel)\b", answer):
        return None
    return app_index.pick(answer, options, label=lambda e: e.stem)


# ---------------------------------------------------------------- side effects (tests replace these)
def open_path(path):
    desktop.start(str(path))


def trash(path):
    from send2trash import send2trash

    send2trash(str(path))


def restore_from_bin(original):
    """Put a file Jarvis sent to the Recycle Bin back where it was."""
    import win32com.client

    original = Path(original)
    recycle = win32com.client.Dispatch("Shell.Application").NameSpace(10)
    matches = []
    for item in recycle.Items():
        came_from = str(item.ExtendedProperty("System.Recycle.DeletedFrom") or "")
        if os.path.normcase(came_from) != os.path.normcase(str(original.parent)):
            continue
        if item.Name in (original.name, original.stem) and item.Path.lower().endswith(original.suffix.lower()):
            matches.append(item)
    if not matches:
        raise SpokenError(f"I couldn't find {original.name} in the Recycle Bin any more.")
    item = matches[-1]
    item.InvokeVerb("undelete")
    for _ in range(20):
        if original.exists():
            return True
        time.sleep(0.1)
    shutil.move(item.Path, str(original))  # the verb didn't run: move it back ourselves
    return True


# ---------------------------------------------------------------- finding a target
def looks_like_path(text):
    return bool(re.match(r"^[a-z]:[\\/]", text.strip(), re.IGNORECASE))


def resolve(ctx, query, verb="open", wants_dir=None):
    """The one file or folder the user means (asking if unclear). SpokenError if there's none."""
    query = query.strip(" .?!\"'")
    if looks_like_path(query):
        path = actions.guard(Path(query), verb)
        if not path.exists():
            raise SpokenError(f"I can't find {query}.")
        return Entry(str(path), path.stat().st_mtime, path.is_dir())
    known = folder(re.sub(r"^(?:my|the)\s+", "", query).replace(" folder", ""))
    if known is not None and verb == "open":
        return Entry(str(known), known.stat().st_mtime, True)
    ranked = rank(query + (" folder" if wants_dir else ""))
    entry = choose(ctx, query, ranked, verb)
    if entry is None:
        raise SpokenError(f"I couldn't find anything called {query}." if not ranked else "Okay, I left it.")
    actions.guard(entry.path, verb)
    return entry


def open_entry(ctx, entry):
    if entry.ext in RUNNABLE and not ctx.confirm(f"{entry.name} is a program or script. Run it?"):
        actions.note(f"Didn't run {entry.path} (you said no)")
        return "Okay, I didn't run it."
    open_path(entry.path)
    actions.record(f"Opened {entry.path}")
    return f"Opening {entry.name}."


def is_fileish(text):
    words, exts, type_words, wants_folder = parse_query(text)
    return bool(exts or type_words or wants_folder or re.search(r"\b(?:files?|resume|cv|biodata|notes? file)\b", text)
                or looks_like_path(text))


# ---------------------------------------------------------------- skills: opening and finding
@skill("open folder", rf"^(?:please\s+)?(?:open|show|go to)\s+(?:me\s+)?(?:my\s+|the\s+)?{_FOLDER}\s+folder$",
       rf"^(?:please\s+)?(?:open|go to)\s+(?:my\s+|the\s+)?{_FOLDER}$",
       rf"{hi.LEAD}{hi.MY}{_FOLDER}(?:\s+folder)?{hi.OPEN}$",
       category="files", help=["open my Downloads folder"])
def open_folder(ctx, command, match):
    path = folder(match.group(1))
    if path is None:
        return f"I can't find your {match.group(1)} folder."
    open_path(path)
    actions.record(f"Opened the {path.name} folder")
    return f"Opening {path.name}."


@skill("latest download", r"\b(?:open|show|play|launch)\s+(?:me\s+)?(?:the\s+|my\s+)?(?:latest|last|newest|most recent|recent)\s+"
                          r"(?:download|downloaded file|file (?:i|that i) downloaded|file in downloads)\b",
       rf"{hi.LEAD}(?:latest|last|naya|nayi)\s+(?:download|downloaded file)(?:\s+wali file)?{hi.OPEN}$",
       category="files", help=["open the latest download"])
def latest_download(ctx, command, match):
    downloads = folder("downloads")
    if downloads is None:
        return "I can't find your Downloads folder."
    newest = newest_files(downloads, 1)
    if not newest:
        return "Your Downloads folder is empty."
    return open_entry(ctx, newest[0])


def newest_files(path, count=None, since=None):
    """The newest files directly in a folder (fresh from disk, not the index)."""
    found = []
    try:
        with os.scandir(path) as listing:
            for item in listing:
                if item.is_file() and not SKIP_FILES.search(item.name):
                    mtime = item.stat().st_mtime
                    if since is None or mtime >= since:
                        found.append(Entry(item.path, mtime, False))
    except OSError:
        return []
    found.sort(key=lambda e: -e.mtime)
    return found[:count] if count else found


@skill("downloads today", r"\b(?:files?|what|stuff)\s+(?:did\s+)?i\s+download(?:ed)?\s+(today|yesterday|this week)\b",
       r"\b(?:show|list|what are)\s+(?:me\s+)?(?:my\s+|the\s+)?(?:files|downloads)\s+(?:i\s+)?(?:downloaded\s+)?(today|yesterday|this week)\b",
       r"\b(today|yesterday)(?:'s)?\s+downloads\b", r"\baaj\s+(?:kya|kaun si files?)\s+download\b()",
       category="files", help=["show files I downloaded today"])
def downloads_today(ctx, command, match):
    downloads = folder("downloads")
    if downloads is None:
        return "I can't find your Downloads folder."
    span = (match.group(1) or "today").lower()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = None
    if span == "yesterday":
        start, end = start - timedelta(days=1), start
    elif span == "this week":
        start -= timedelta(days=start.weekday())
    files = [e for e in newest_files(downloads, since=start.timestamp()) if end is None or e.mtime < end.timestamp()]
    if not files:
        return f"You haven't downloaded anything {span}."
    return speak_list(ctx, f"You downloaded {len(files)} file{'s' if len(files) > 1 else ''} {span}:",
                      [e.name for e in files])


@skill("open file", r"^(?:please\s+)?(?:open|launch)\s+(?:up\s+)?(.+)$",
       rf"{hi.LEAD}{hi.MY}(.+?){hi.OPEN}$",
       category="files", help=["open my resume", "meri resume khol"],
       tool="open_file", params={"name": "file name the user said, e.g. my resume, bogie pitch deck"},
       command="open my {name} file")
def open_file(ctx, command, match):
    from jarvis.skills import apps, app_index

    target = match.group(1).strip(" .?!")
    if re.search(r"\b(?:in|on|with)\s+(?:vs\s?code|visual studio code|code|chrome|edge|firefox|youtube|spotify)$|"
                 r"\bproject$|\bsettings$|\bsettings page$", target):
        return None
    fileish = is_fileish(target) or bool(re.match(r"(?:my|meri|mera|mere)\s", command.split(" ", 1)[-1]))
    key = re.sub(r"^(?:the|my|meri|mera|mere)\s+", "", target)
    if not fileish:
        if apps.is_known_target(key) or app_index.candidates(key)[0] or app_index.candidates(key)[1]:
            return None  # an app or a website
    ranked = rank(target)
    if not ranked:
        if fileish and not apps.is_known_target(key) and not app_index.match(key):
            return f"I couldn't find a file called {key}."
        return None  # "open figma": the app skill tries the Start menu
    if not fileish and ranked[0][0] < 0.75:
        return None
    entry = choose(ctx, target, ranked)
    if entry is None:
        return "Okay, I didn't open anything."
    actions.guard(entry.path, "open")
    return open_entry(ctx, entry)


@skill("find file", r"^(?:please\s+)?(?:find|locate|look for|search my (?:laptop|pc|computer|files) for|where(?:'s| is) my)\s+"
                    r"(?:me\s+)?(.+?)(?:\s+(?:on|in)\s+my\s+(?:laptop|pc|computer|files))?\??$",
       rf"{hi.LEAD}{hi.MY}(.+?){hi.FIND}$", rf"{hi.LEAD}{hi.MY}(.+?)\s+kahan\s+(?:hai|h|padi hai|pada hai)\??$",
       category="files", help=["find the bogie pitch deck", "where is my resume"],
       tool="find_file", params={"name": "file or folder name, e.g. bogie pitch deck"}, command="find my {name} file")
def find_file(ctx, command, match):
    target = match.group(1).strip(" .?!")
    if re.search(r"\bout\b|^(?:a|an|some|me a|nearby|good)\b|\b(?:near me|nearby|online|restaurant|job|flight)\b", target):
        return None  # "find a good restaurant": the web
    ranked = rank(target)
    if not ranked:
        if not is_fileish(target):
            return None  # "where is my order": not a file, let the AI answer
        return f"I couldn't find anything called {re.sub(r'^(?:my|the) +', '', target)}."
    top = ranked[0][0]
    close = [e for s, e in ranked[:3] if top - s <= TIE]
    if len(close) == 1:
        entry = close[0]
        if ctx.confirm(f"Found {describe(entry)}, {when(entry)}. Want me to open it?"):
            actions.guard(entry.path, "open")
            return open_entry(ctx, entry)
        return "Okay."
    entry = choose(ctx, target, ranked)
    if entry is None:
        return "Okay, I left them."
    actions.guard(entry.path, "open")
    return open_entry(ctx, entry)


@skill("refresh files", r"^(?:please\s+)?(?:refresh|rescan|re-?index|rebuild|update)\s+(?:the\s+|my\s+)?(?:files?|file index)$",
       category="files", help=["refresh files"])
def refresh_files(ctx, command, match):
    found = entries(refresh=True)
    return f"Done. I know the names of {len(found):,} files and folders now."


# ---------------------------------------------------------------- skills: changing files (always asks first)
BAD_NAME = re.compile(r'[\\/:*?"<>|]')


def _clean_name(name):
    name = re.sub(r"\s+dot\s+(\w{1,5})$", r".\1", name.strip(" .'\""))
    if not name or BAD_NAME.search(name) or name in (".", ".."):
        raise SpokenError("That name has characters Windows doesn't allow in file names.")
    return name


@skill("create folder", r"^(?:please\s+)?(?:create|make|add)\s+(?:a\s+)?(?:new\s+)?folder\s+(?:called\s+|named\s+)?(.+?)"
                        rf"(?:\s+(?:on|in)\s+(?:my\s+|the\s+)?{_FOLDER})?$",
       r"^(?:please\s+)?new\s+folder\s+(?:called\s+|named\s+)?(.+?)" + rf"(?:\s+(?:on|in)\s+(?:my\s+|the\s+)?{_FOLDER})?$",
       category="files", help=["create folder Projects on desktop"])
def create_folder(ctx, command, match):
    name = _clean_name(match.group(1))
    where_to = folder(match.group(2) or "desktop")
    if where_to is None:
        return "I can't find that folder."
    path = where_to / name
    actions.guard(path, "create")
    if path.exists():
        return f"There's already a {name} in {where_to.name}."
    if not ctx.confirm(f"Create a folder called {name} in {where_to.name}?"):
        actions.note(f"Didn't create {path} (you said no)")
        return "Okay, I didn't create it."
    path.mkdir(parents=False)
    add_to_index(path)

    def undo():
        if any(path.iterdir()):
            raise SpokenError(f"{name} has files in it now, so I left it.")
        path.rmdir()
        drop_from_index(path)

    actions.record(f"Created the folder {path}", undo=undo, undo_text=f"remove the empty folder {name}")
    return f"Created {name} in {where_to.name}."


@skill("rename file", r"^(?:please\s+)?rename\s+(?:my\s+|the\s+)?(.+?)\s+(?:to|as|into)\s+(.+)$",
       category="files", help=["rename notes.txt to ideas.txt"],
       params={"name": "file or folder to rename", "new_name": "the new name"}, command="rename {name} to {new_name}",
       risky=True, confirms=True)
def rename_file(ctx, command, match):
    if re.search(r"\broutine\b", match.group(1)):
        return None
    entry = resolve(ctx, match.group(1), "rename")
    old = Path(entry.path)
    new_name = _clean_name(match.group(2))
    if not entry.is_dir and not Path(new_name).suffix:
        new_name += old.suffix  # keep .pdf when you just say the new name
    new = old.with_name(new_name)
    actions.guard(new, "rename")
    if new.exists():
        return f"There's already something called {new_name} there."
    if not ctx.confirm(f"Rename {old.name} to {new_name}?"):
        actions.note(f"Didn't rename {old} (you said no)")
        return "Okay, I didn't rename it."
    old.rename(new)
    drop_from_index(old)
    add_to_index(new)

    def undo():
        if old.exists():
            raise SpokenError(f"Something called {old.name} is back there, so I can't rename it back.")
        new.rename(old)
        drop_from_index(new)
        add_to_index(old)

    actions.record(f"Renamed {old} to {new_name}", undo=undo, undo_text=f"rename {new_name} back to {old.name}")
    return f"Renamed it to {new_name}."


@skill("move file", r"^(?:please\s+)?move\s+(?:my\s+|the\s+)?(.+?)\s+(?:to|into|in)\s+(?:my\s+|the\s+)?(.+?)(?:\s+folder)?$",
       rf"{hi.LEAD}{hi.MY}(.+?)\s+(?:ko\s+)?(?:my\s+|meri\s+)?(.+?)\s+(?:mein|me|pe|par)\s+(?:move|daal|daalo|daal do|rakh|rakho|rakh do)"
       rf"{hi.KAR}$",
       category="files", help=["move resume to Documents"],
       params={"name": "file or folder to move", "destination": "folder, e.g. documents"},
       command="move {name} to {destination}", risky=True, confirms=True)
def move_file(ctx, command, match):
    name, dest_name = match.group(1).strip(), match.group(2).strip()
    if re.search(r"\b(?:screen|monitor|display|left|right|desktop \d|next desktop|this|it|window)\b", dest_name) or \
            re.search(r"^(?:this|it|the window|this window)$", name):
        return None  # windows: "move this to the other screen"
    entry = resolve(ctx, name, "move")
    source = Path(entry.path)
    destination = folder(dest_name)
    if destination is None:
        dest = resolve(ctx, dest_name + " folder", "move into", wants_dir=True)
        destination = Path(dest.path)
    actions.guard(destination, "move into")
    target = destination / source.name
    if target.exists():
        return f"{destination.name} already has a {source.name}."
    if os.path.normcase(str(source.parent)) == os.path.normcase(str(destination)):
        return f"It's already in {destination.name}."
    if not ctx.confirm(f"Move {source.name} from {source.parent.name} to {destination.name}?"):
        actions.note(f"Didn't move {source} (you said no)")
        return "Okay, I didn't move it."
    shutil.move(str(source), str(target))
    drop_from_index(source)
    add_to_index(target)

    def undo():
        if source.exists():
            raise SpokenError(f"There's a new {source.name} where it was, so I can't move it back.")
        shutil.move(str(target), str(source))
        drop_from_index(target)
        add_to_index(source)

    actions.record(f"Moved {source} to {destination}", undo=undo,
                   undo_text=f"move {source.name} back to {source.parent.name}")
    return f"Moved {source.name} to {destination.name}."


@skill("zip file", r"^(?:please\s+)?(?:zip|compress)\s+(?:up\s+)?(?:my\s+|the\s+)?(.+?)$",
       rf"{hi.LEAD}{hi.MY}(.+?)\s+(?:ko\s+)?(?:zip|compress){hi.KAR}$",
       category="files", help=["zip the jarvis folder"])
def zip_file(ctx, command, match):
    entry = resolve(ctx, match.group(1), "zip")
    source = Path(entry.path)
    base = source.parent / (source.stem if not entry.is_dir else source.name)
    archive, n = base.with_name(base.name + ".zip"), 2
    while archive.exists():
        archive, n = base.with_name(f"{base.name} ({n}).zip"), n + 1
    actions.guard(archive, "create")
    if not ctx.confirm(f"Zip {source.name} into {archive.name}?"):
        actions.note(f"Didn't zip {source} (you said no)")
        return "Okay, I didn't zip it."
    if entry.is_dir:
        made = shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=str(source.parent), base_dir=source.name)
    else:
        import zipfile

        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
            out.write(source, source.name)
        made = str(archive)
    add_to_index(made)

    def undo():
        trash(made)
        drop_from_index(made)

    actions.record(f"Zipped {source} into {made}", undo=undo, undo_text=f"send {Path(made).name} to the Recycle Bin")
    return f"Made {Path(made).name} next to it."


@skill("delete file", r"^(?:please\s+)?(?:delete|remove|trash|bin|get rid of|recycle)\s+(?:my\s+|the\s+)?(.+?)(?:\s+(?:file|folder))?$",
       rf"{hi.LEAD}{hi.MY}(.+?)\s+(?:ko\s+)?(?:delete|hata|hatao|hata do|mita|mitao|mita do|uda|uda do){hi.KAR}$",
       category="files", help=["delete old notes.txt"],
       params={"name": "file or folder to send to the Recycle Bin"}, command="delete {name} file", risky=True,
       confirms=True)
def delete_file(ctx, command, match):
    target = match.group(1).strip()
    named_file = bool(re.search(r"\.\w{1,5}$|\s+dot\s+\w{1,5}$", target))  # "notes.txt" is a file
    if not named_file and re.search(r"\b(?:reminders?|notes|event|meeting|routine|contact|emails?|drafts?|calendar|"
                                    r"timer|history|conversation|this|that|it|recycle bin|temp files)\b", target):
        return None  # other skills' things, or nothing specific
    entry = resolve(ctx, target, "delete")
    path = Path(entry.path)
    kind = "folder" if entry.is_dir else "file"
    if not ctx.confirm(f"Move the {kind} {path.name} from {path.parent.name} to the Recycle Bin?"):
        actions.note(f"Didn't delete {path} (you said no)")
        return "Okay, I didn't delete it."
    trash(path)
    drop_from_index(path)

    def undo():
        restore_from_bin(path)
        add_to_index(path)

    actions.record(f"Sent {path} to the Recycle Bin", undo=undo, undo_text=f"restore {path.name} from the Recycle Bin")
    return f"Moved {path.name} to the Recycle Bin. Say undo that to bring it back."


# ---------------------------------------------------------------- the background index
_stop = threading.Event()


@on_startup
def _index_in_background(ctx):
    if not desktop.IS_WINDOWS:
        return
    _stop.clear()

    def loop():
        while not _stop.is_set():
            try:
                _names_of(entries())  # warm the name-word cache too, so the first search is instant
            except Exception:
                log.exception("[files] couldn't build the file index")
            _stop.wait(max(600, config.FILE_INDEX_HOURS * 3600 / 4))

    threading.Thread(target=loop, name="file-index", daemon=True).start()


def stop_indexing():
    _stop.set()
