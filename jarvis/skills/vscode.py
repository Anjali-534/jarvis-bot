"""VS Code projects: open projects/files, git status, git pull, and run dev servers.

Projects are found by scanning PROJECT_ROOTS (2 levels deep) for folders containing a
project marker, merged with manual aliases from data/projects.json. For safety, git is
read-only apart from a confirmed `pull`; nothing commits, pushes or resets by voice.
"""
import logging
import difflib
import json
import os
import re
import subprocess
from pathlib import Path

import config
from jarvis.router import SpokenError, skill, speak_list

log = logging.getLogger(__name__)

MARKERS = (".git", "package.json", "go.mod", "requirements.txt")
IGNORED_DIRS = {"node_modules", ".git", "dist", "build", ".next", "__pycache__", ".venv", "venv", "env",
                ".idea", ".vscode", ".pytest_cache", "target", "out", "coverage"}
MAX_DEPTH = 2
VSC = r"(?:v\.?\s?s\.?\s?code|visual studio code|code)"
ORDINALS = {"first": 0, "one": 0, "1": 0, "second": 1, "two": 1, "2": 1, "third": 2, "three": 2, "3": 2}

_cache: dict | None = None


# ---------------------------------------------------------------- side effects (replaced in tests)
def shell(command):
    """Run a shell command without waiting (used for `code` and `start`)."""
    log.info(f"[vscode] {command}")
    subprocess.Popen(command, shell=True)


def git(path, *args, timeout=60):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ---------------------------------------------------------------- project registry
def spoken(name):
    """'acme-admin_panel' -> 'acme admin panel'."""
    return re.sub(r"\s+", " ", re.sub(r"[-_.]+", " ", name)).strip().lower()


def is_project(folder):
    return any((folder / marker).exists() for marker in MARKERS)


def scan_projects(roots=None, max_depth=MAX_DEPTH):
    """Return {spoken name: Path} for project folders up to max_depth levels below each root."""
    found = {}

    def walk(folder, depth):
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name in IGNORED_DIRS or child.name.startswith("."):
                continue
            if is_project(child):
                name = spoken(child.name)
                if name in found and found[name] != child:
                    name = f"{spoken(folder.name)} {name}"
                found.setdefault(name, child)
            if depth < max_depth:
                walk(child, depth + 1)

    for root in roots if roots is not None else config.PROJECT_ROOTS:
        root = Path(root)
        if root.is_dir():
            walk(root, 1)
    return found


def load_aliases():
    path = config.PROJECTS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        log.warning(f"[vscode] {path} is not valid JSON")
        return {}
    return {spoken(k): Path(os.path.expandvars(os.path.expanduser(v))) for k, v in data.items()
            if not k.startswith("_")}  # "_help" notes in the file aren't projects


def get_projects(refresh=False):
    global _cache
    if _cache is None or refresh:
        _cache = {**scan_projects(), **load_aliases()}
    return _cache


def clean_query(text):
    text = spoken(text or "")
    text = re.sub(r"^(?:the|my)\s+", "", text)
    return re.sub(r"\s+(?:project|projects|folder|repo|repository|app|codebase)$", "", text).strip()


def _score(query, name):
    if query == name:
        return 1.0
    q_words, n_words = query.split(), name.split()
    ratio = max(difflib.SequenceMatcher(None, query, name).ratio(),
                difflib.SequenceMatcher(None, query.replace(" ", ""), name.replace(" ", "")).ratio())
    # every spoken word starts a word of the name, in order ("acme admin" -> "acme admin panel")
    it = iter(n_words)
    if q_words and all(any(n.startswith(q) for n in it) for q in q_words):
        return 0.85 + 0.15 * ratio
    return ratio


def match_projects(query, projects=None, cutoff=0.6):
    """Return project names ranked best-first, only those scoring at least `cutoff`."""
    projects = get_projects() if projects is None else projects
    query = clean_query(query)
    if not query:
        return []
    scored = sorted(((_score(query, name), name) for name in projects), key=lambda s: (-s[0], len(s[1])))
    return [name for score, name in scored if score >= cutoff]


def _is_clear_winner(query, ranked, projects):
    if len(ranked) == 1:
        return True
    first, second = _score(clean_query(query), ranked[0]), _score(clean_query(query), ranked[1])
    return first == 1.0 or first - second >= 0.08


def say_list(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def resolve_project(ctx, query, required=True):
    """Return (name, Path). Asks which one if several match. Raises SpokenError (or returns None)."""
    projects = get_projects()
    ranked = match_projects(query, projects)
    if not ranked:
        if not required:
            return None
        raise SpokenError(f"I couldn't find a project called {clean_query(query)}. "
                          "Say list my projects to hear them, or rescan projects if it's new.")
    if not _is_clear_winner(query, ranked, projects):
        options = ranked[:3]
        answer = clean_query(ctx.ask(f"I found {say_list(options)}. Which one?"))
        if not answer:
            raise SpokenError("Okay, never mind.")
        pick = next((ORDINALS[w] for w in answer.split() if w in ORDINALS), None)
        if pick is not None and pick < len(options):
            chosen = options[pick]
        else:
            narrowed = match_projects(answer, {n: projects[n] for n in options}, cutoff=0.5)
            if not narrowed:
                raise SpokenError("Sorry, that wasn't one of them.")
            chosen = narrowed[0]
        return chosen, projects[chosen]
    return ranked[0], projects[ranked[0]]


# ---------------------------------------------------------------- helpers
def find_file(root, query):
    """Find a file in the project by spoken name, skipping dependency/build folders."""
    query = re.sub(r"\s*\bdot\b\s*", ".", query.strip().lower()).strip(" .")
    variants = {query, query.replace(" ", ""), query.replace(" ", "_"), query.replace(" ", "-")}
    exact, by_stem, partial = [], [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            lower = filename.lower()
            path = Path(dirpath) / filename
            if lower in variants:
                exact.append(path)
            elif Path(lower).stem in variants:
                by_stem.append(path)
            elif any(v and v in lower for v in variants):
                partial.append(path)
    for group in (exact, by_stem, partial):
        if group:
            return sorted(group, key=lambda p: (len(p.relative_to(root).parts), len(str(p))))[0]
    return None


def detect_run_command(path):
    path = Path(path)
    package = path / "package.json"
    if package.exists():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
        except (json.JSONDecodeError, OSError):
            scripts = {}
        if "dev" in scripts:
            return "npm run dev"
        if "start" in scripts:
            return "npm start"
    if (path / "go.mod").exists():
        return "go run ."
    if (path / "main.py").exists():
        return "python main.py"
    return None


def summarize_status(name, output):
    """Speakable summary of `git status --short --branch` (never reads file names)."""
    lines = [line for line in output.splitlines() if line.strip()]
    header = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    changes = [line for line in lines if not line.startswith("## ")]
    header = re.sub(r"^No commits yet on ", "", header)
    branch = re.split(r"\.\.\.|\s", header, maxsplit=1)[0] or "an unknown branch"
    parts = [f"{name.title()} is on branch {branch}"]
    ahead = re.search(r"ahead (\d+)", header)
    behind = re.search(r"behind (\d+)", header)
    sync = []
    if ahead:
        n = int(ahead.group(1))
        sync.append(f"{n} commit{'s' if n != 1 else ''} ahead")
    if behind:
        n = int(behind.group(1))
        sync.append(f"{n} commit{'s' if n != 1 else ''} behind")
    if sync:
        parts[0] += ", " + " and ".join(sync) + " of the remote"
    elif "..." in header:
        parts[0] += ", up to date with the remote"
    if not changes:
        parts.append("The working tree is clean.")
    else:
        untracked = sum(1 for line in changes if line.startswith("??"))
        text = f"{len(changes)} changed file{'s' if len(changes) != 1 else ''}"
        if untracked:
            text += f", {untracked} of them untracked" if untracked != len(changes) else ", all untracked"
        parts.append(text[:1].upper() + text[1:] + ".")
    return ". ".join(parts[:1]) + ". " + " ".join(parts[1:])


def _project_arg(ctx, match, prompt="Which project?"):
    query = (match.group(1) if match and match.groups() else "") or ""
    if not clean_query(query):
        query = ctx.ask(prompt)
    if not clean_query(query):
        raise SpokenError("Okay, never mind.")
    return query


# ---------------------------------------------------------------- skills
@skill("vscode new window", rf"\bopen {VSC} here\b", rf"\bnew {VSC} window\b", rf"\bopen (?:a )?new {VSC} window\b", category='vs code', help=['new VS Code window'])
def new_window(ctx, command, match):
    shell("code -n")
    return "Opening a new VS Code window."


@skill("vscode open file", rf"^(?:please\s+)?open\s+(?:the\s+)?file\s+(.+?)\s+(?:in|from)\s+(?:the\s+|my\s+)?(.+?)(?:\s+in\s+{VSC})?$", category='vs code', help=['open file main dot py in jarvis'])
def open_file(ctx, command, match):
    name, path = resolve_project(ctx, match.group(2))
    found = find_file(path, match.group(1))
    if not found:
        return f"I couldn't find a file called {match.group(1)} in {name}."
    shell(f'code -g "{found}"')
    return f"Opening {found.name} from {name}."


@skill("vscode list projects", r"\b(?:list|show|what are|tell me)\b.*\bprojects\b", r"\bmy projects\b",
       r"\bhow many projects\b", category='vs code', help=['list my projects'])
def list_projects(ctx, command, match):
    projects = get_projects()
    if not projects:
        return ("I didn't find any projects. Set your project folders (PROJECT_ROOTS) in Settings, "
                "or add aliases to data slash projects dot json.")
    names = sorted(projects)
    return speak_list(ctx, f"You have {len(names)} project{'s' if len(names) != 1 else ''}:", names)


@skill("vscode rescan", r"\b(?:rescan|re scan|refresh|reload|scan)\b.*\bprojects?\b", category='vs code', help=['rescan projects'])
def rescan(ctx, command, match):
    projects = get_projects(refresh=True)
    return f"Done. I found {len(projects)} project{'s' if len(projects) != 1 else ''}."


@skill("git status", r"\bgit status\b(?:\s+(?:of|for|in|on))?(?:\s+(?:the|my))?\s*(.*?)(?:\s+(?:project|repo))?$",
       r"\bwhat(?:'s| has| is)? changed in\s+(?:the\s+|my\s+)?(.+?)(?:\s+project)?$", category='vs code', help=['git status of jarvis'])
def git_status(ctx, command, match):
    name, path = resolve_project(ctx, _project_arg(ctx, match, "Which project's git status?"))
    result = git(path, "status", "--short", "--branch")
    if result.returncode != 0:
        if "not a git repository" in (result.stderr or "").lower():
            return f"{name.title()} isn't a git repository."
        log.warning("[git] %s", result.stderr.strip())
        return f"Git couldn't read the status of {name}."
    return summarize_status(name, result.stdout)


@skill("git pull", r"^(?:please\s+)?(?:git\s+)?pull\s+(?:the\s+latest\s+(?:changes\s+)?(?:for|in|into)\s+)?(?:the\s+|my\s+)?(.+?)"
                   r"(?:\s+(?:project|repo))?(?:\s+from\s+(?:github|origin|remote))?$", category='vs code', help=['pull jarvis'], params={"project": "project name"}, command="pull {project}", risky=True, confirms=True)
def git_pull(ctx, command, match):
    found = resolve_project(ctx, match.group(1), required=False)
    if found is None:
        return None  # "pull up the weather" is not a git pull
    name, path = found
    if not (Path(path) / ".git").exists():
        return f"{name.title()} isn't a git repository."
    if not ctx.confirm(f"Pull the latest changes into {name}?"):
        return "Okay, I didn't pull."
    result = git(path, "pull", "--ff-only", timeout=120)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        log.warning("[git] %s", output.strip())
        return f"The pull into {name} failed. Check the terminal for details."
    if "already up to date" in output.lower():
        return f"{name.title()} is already up to date."
    return f"Pulled the latest changes into {name}."


@skill("run project", r"^(?:please\s+)?(?:run|start|launch)\s+(?:the\s+|my\s+)?(.+?)\s+(?:project|app|server|dev server)$",
       r"^(?:please\s+)?run\s+(?:the\s+|my\s+)?(.+)$", category='vs code', help=['run acme backend'], params={"project": "project name"}, command="run {project}", risky=True, confirms=True)
def run_project(ctx, command, match):
    found = resolve_project(ctx, match.group(1), required=False)
    if found is None:
        return None  # "run notepad" / "start whatsapp app" belong to the apps skill
    name, path = found
    run_command = detect_run_command(path)
    if not run_command:
        return f"I don't know how to run {name}. It has no dev or start script, go.mod or main.py."
    if not ctx.confirm(f"Run {run_command} in {name}?"):
        return "Okay, I didn't run it."
    title = name.replace('"', "")
    shell(f'start "{title}" /D "{path}" cmd /k {run_command}')
    return f"Started {name} in a new terminal window."


@skill("vscode open project", rf"^(?:please\s+)?open\s+(?:the\s+|my\s+)?(?:project\s+)?(.+?)\s+(?:project\s+|folder\s+)?in\s+{VSC}$",
       r"^(?:please\s+)?open\s+(?:the\s+|my\s+)?(.+?)\s+project$", r"^(?:please\s+)?open\s+project\s+(.+)$",
       r"^(?:let's\s+|i want to\s+)?work on\s+(?:the\s+|my\s+)?(.+)$", category='vs code', help=['open jarvis in VS Code', 'work on acme backend'])
def open_project(ctx, command, match):
    name, path = resolve_project(ctx, match.group(1))
    shell(f'code "{path}"')
    return f"Opening {name} in VS Code."
