"""VS Code skill tests with a fake folder tree; `code`, `start` and git are faked."""
import json
import subprocess
from pathlib import Path

import pytest
from helpers import make_ctx

import config
from jarvis.router import handle
from jarvis.skills import vscode


def touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "code"
    touch(root / "bogie-backend" / "package.json", json.dumps({"scripts": {"dev": "nodemon", "start": "node ."}}))
    touch(root / "bogie-admin-panel" / "package.json", json.dumps({"scripts": {"start": "react-scripts start"}}))
    (root / "bogie-frontend" / ".git").mkdir(parents=True)
    touch(root / "bogie-frontend" / "src" / "App.tsx")
    touch(root / "bogie-frontend" / "node_modules" / "react" / "App.tsx")
    touch(root / "tools" / "go-api" / "go.mod")                       # level 2: found
    touch(root / "deep" / "a" / "too-deep" / "requirements.txt")     # level 3: skipped
    touch(root / "node_modules" / "left-pad" / "package.json")       # ignored dir
    touch(root / "jarvis" / "requirements.txt")
    touch(root / "jarvis" / "main.py")
    touch(root / "jarvis" / "jarvis" / "router.py")
    touch(root / "jarvis" / "build" / "router.py")
    (root / "empty-folder").mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOTS", [root])
    return root


@pytest.fixture
def shell_log(monkeypatch):
    log = []
    monkeypatch.setattr(vscode, "shell", log.append)
    return log


def test_scan_finds_projects_two_levels_deep(tree):
    projects = vscode.scan_projects()
    assert set(projects) == {"bogie backend", "bogie admin panel", "bogie frontend", "go api", "jarvis"}
    assert projects["go api"] == tree / "tools" / "go-api"


def test_aliases_merge_and_rescan(tree, shell_log):
    touch(config.PROJECTS_FILE, json.dumps({"api": str(tree / "bogie-backend")}))
    assert vscode.get_projects()["api"] == tree / "bogie-backend"
    touch(tree / "new-thing" / "go.mod")
    assert "new thing" not in vscode.get_projects()          # cached
    ctx = make_ctx()
    handle(ctx, "rescan projects")
    assert ctx.voice.last == "Done. I found 7 projects."


@pytest.mark.parametrize("query, best", [
    ("bogie admin", "bogie admin panel"), ("bogie-backend", "bogie backend"), ("jarvis", "jarvis"),
    ("the jarvis project", "jarvis"), ("bogey front end", "bogie frontend"), ("go api", "go api"),
])
def test_fuzzy_matching(tree, query, best):
    assert vscode.match_projects(query)[0] == best


def test_no_match():
    assert vscode.match_projects("photoshop", {"jarvis": Path("x")}) == []


def test_open_project(tree, shell_log):
    ctx = make_ctx()
    assert handle(ctx, "open bogie admin in vs code") == "vscode open project"
    assert shell_log == [f'code "{tree / "bogie-admin-panel"}"']
    assert ctx.voice.last == "Opening bogie admin panel in VS Code."


def test_ambiguous_project_asks(tree, shell_log):
    ctx = make_ctx(["the second one"])
    handle(ctx, "work on bogie")
    assert ctx.voice.said[0].startswith("I found ") and ctx.voice.said[0].endswith("Which one?")
    options = ctx.voice.said[0][len("I found "):-len(". Which one?")].replace(" and ", ", ").split(", ")
    assert len(options) == 3 and all(o.startswith("bogie") for o in options)
    chosen = vscode.get_projects()[options[1]]
    assert shell_log == [f'code "{chosen}"']


def test_ambiguous_answer_by_name(tree, shell_log):
    ctx = make_ctx(["backend"])
    handle(ctx, "open bogie project")
    assert shell_log == [f'code "{tree / "bogie-backend"}"']


def test_unknown_project(tree, shell_log):
    ctx = make_ctx()
    handle(ctx, "open photoshop project")
    assert "couldn't find a project called photoshop" in ctx.voice.last
    assert shell_log == []


def test_new_window(shell_log):
    ctx = make_ctx()
    handle(ctx, "open vs code here")
    assert shell_log == ["code -n"]


def test_open_file_skips_ignored_dirs(tree, shell_log):
    assert vscode.find_file(tree / "jarvis", "router dot py") == tree / "jarvis" / "jarvis" / "router.py"
    assert vscode.find_file(tree / "bogie-frontend", "app") == tree / "bogie-frontend" / "src" / "App.tsx"
    assert vscode.find_file(tree / "jarvis", "nothing here") is None
    ctx = make_ctx()
    handle(ctx, "open file router.py in jarvis")
    assert shell_log == [f'code -g "{tree / "jarvis" / "jarvis" / "router.py"}"']


@pytest.mark.parametrize("folder, command", [
    ("bogie-backend", "npm run dev"), ("bogie-admin-panel", "npm start"), ("tools/go-api", "go run ."),
    ("jarvis", "python main.py"), ("bogie-frontend", None),
])
def test_detect_run_command(tree, folder, command):
    assert vscode.detect_run_command(tree / folder) == command


def test_run_project_confirms_and_opens_terminal(tree, shell_log):
    ctx = make_ctx(["yes"])
    handle(ctx, "run bogie backend")
    assert ctx.voice.said[0] == "Run npm run dev in bogie backend?"
    assert shell_log == [f'start "bogie backend" /D "{tree / "bogie-backend"}" cmd /k npm run dev']


def test_run_project_refused(tree, shell_log):
    ctx = make_ctx(["no"])
    handle(ctx, "start the go api server")
    assert ctx.voice.last == "Okay, I didn't run it." and shell_log == []


def test_run_unknown_falls_through_to_apps(tree, monkeypatch):
    from jarvis.skills import apps

    opened = []
    monkeypatch.setattr(apps, "open_target", lambda name: opened.append(name) or name)
    ctx = make_ctx()
    assert handle(ctx, "run notepad") == "open"
    assert opened == ["notepad"]


def test_list_projects(tree):
    ctx = make_ctx()
    handle(ctx, "list my projects")
    ctx = make_ctx(["yes"])
    handle(ctx, "list my projects")
    assert ctx.voice.said == ["You have 5 projects: bogie admin panel, bogie backend and bogie frontend.", "Want more?",
                              "go api and jarvis."]


REAL_GIT = vscode.git  # captured at import, before conftest replaces it


def fake_git(monkeypatch, stdout="", returncode=0, stderr=""):
    """Fake subprocess.run so vscode.git's real argument building is exercised."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    monkeypatch.setattr(vscode, "git", REAL_GIT)
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_git_status_summary(tree, monkeypatch):
    calls = fake_git(monkeypatch, "## main...origin/main [ahead 2, behind 1]\n M src/secret.ts\n?? notes.md\n?? x.py\n")
    ctx = make_ctx()
    handle(ctx, "git status of jarvis")
    assert calls == [["git", "-C", str(tree / "jarvis"), "status", "--short", "--branch"]]
    assert ctx.voice.last == ("Jarvis is on branch main, 2 commits ahead and 1 commit behind of the remote. "
                              "3 changed files, 2 of them untracked.")
    assert "secret" not in ctx.voice.last


@pytest.mark.parametrize("output, expected", [
    ("## master...origin/master\n", "Jarvis is on branch master, up to date with the remote. The working tree is clean."),
    ("## feature\n M a.py\n", "Jarvis is on branch feature. 1 changed file."),
    ("## No commits yet on main\n?? a\n", "Jarvis is on branch main. 1 changed file, all untracked."),
])
def test_summarize_status(output, expected):
    assert vscode.summarize_status("jarvis", output) == expected


def test_git_status_not_a_repo(tree, monkeypatch):
    fake_git(monkeypatch, returncode=128, stderr="fatal: not a git repository")
    ctx = make_ctx()
    handle(ctx, "git status of go api")
    assert ctx.voice.last == "Go Api isn't a git repository."


def test_git_status_asks_which_project(tree, monkeypatch):
    fake_git(monkeypatch, "## main\n")
    ctx = make_ctx(["jarvis"])
    handle(ctx, "git status")
    assert ctx.voice.said[0] == "Which project's git status?"
    assert ctx.voice.last.startswith("Jarvis is on branch main")


def test_pull_requires_confirmation(tree, monkeypatch):
    calls = fake_git(monkeypatch, "Already up to date.\n")
    ctx = make_ctx(["no"])
    handle(ctx, "pull bogie frontend")
    assert calls == [] and ctx.voice.last == "Okay, I didn't pull."
    ctx = make_ctx(["yes"])
    handle(ctx, "pull bogie frontend")
    assert calls == [["git", "-C", str(tree / "bogie-frontend"), "pull", "--ff-only"]]
    assert ctx.voice.last == "Bogie Frontend is already up to date."


def test_no_dangerous_git_commands_by_voice(tree, monkeypatch):
    calls = fake_git(monkeypatch)
    for phrase in ("push jarvis", "git push jarvis", "commit jarvis", "git reset jarvis"):
        handle(make_ctx(["yes"]), phrase)
    assert all(args[3] not in ("push", "commit", "reset") for args in calls)
