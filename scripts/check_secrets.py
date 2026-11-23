"""Block commits that contain API keys or other secrets.

    python scripts/check_secrets.py          # check the files staged for commit (what the pre-commit hook runs)
    python scripts/check_secrets.py --all    # check every tracked file
    python scripts/check_secrets.py FILE...  # check specific files

Install it as a git pre-commit hook with:  python scripts/install_hooks.py

Detects Google keys (AIza..., AQ.Ab...), Groq (gsk_...), OpenAI/Anthropic-style (sk-...), GitHub tokens,
Slack tokens, private-key blocks, and long random-looking tokens. A line ending with the comment
`secret-check: ignore` is skipped. Nothing found is ever printed in full: matches are masked.
"""
import math
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("Google auth key", re.compile(r"AQ\.[0-9A-Za-z_\-]{20,}")),
    ("Groq API key", re.compile(r"gsk_[0-9A-Za-z]{20,}")),
    ("OpenAI-style secret key", re.compile(r"\bsk-(?:proj-|ant-|or-v1-)?[0-9A-Za-z_\-]{20,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[0-9A-Za-z_]{20,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[0-9A-Za-z\-]{10,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
]
# Long random-looking tokens, e.g. KEY=8fJ2kL9...: 32+ chars mixing upper, lower and digits, high entropy
TOKEN = re.compile(r"(?<![0-9A-Za-z_\-])[0-9A-Za-z_\-]{32,}(?![0-9A-Za-z_\-])")
MIN_ENTROPY = 4.0
IGNORE_MARKER = "secret-check: ignore"
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".wav", ".mp3", ".onnx", ".tflite", ".pdf", ".zip",
                 ".pyc", ".bin", ".lock"}


def entropy(text):
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def looks_random(token):
    if not (re.search(r"[a-z]", token) and re.search(r"[A-Z]", token) and re.search(r"\d", token)):
        return False  # hex hashes, words_with_underscores, CONSTANT_NAMES...
    if re.fullmatch(r"[A-Za-z]+(?:[_\-][A-Za-z0-9]+)+", token) and entropy(token) < 4.5:
        return False  # long identifiers like test_reminder_fires_into_later_test_v2
    return entropy(token) >= MIN_ENTROPY


def mask(secret):
    return f"{secret[:6]}... ({len(secret)} chars)"


def scan_text(text):
    """Yield (line number, kind, masked match) for each likely secret."""
    for number, line in enumerate(text.splitlines(), 1):
        if IGNORE_MARKER in line:
            continue
        found = False
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                found = True
                yield number, kind, mask(match.group(0))
        if found:
            continue
        for match in TOKEN.finditer(line):
            if looks_random(match.group(0)):
                yield number, "long random token", mask(match.group(0))


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def staged_files():
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").stdout
    return [line for line in out.splitlines() if line.strip()]


def staged_content(path):
    """The version of the file that will be committed (not the working copy)."""
    result = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    return result.stdout.decode("utf-8", errors="replace") if result.returncode == 0 else ""


def check(paths, read):
    problems = []
    for path in paths:
        if Path(path).suffix.lower() in SKIP_SUFFIXES:
            continue
        text = read(path)
        if "\x00" in text[:4096]:
            continue  # binary
        problems += [(path, *hit) for hit in scan_text(text)]
    return problems


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--all"]:
        paths, read = git("ls-files").stdout.splitlines(), lambda p: Path(p).read_text(encoding="utf-8",
                                                                                         errors="replace")
    elif args:
        paths, read = args, lambda p: Path(p).read_text(encoding="utf-8", errors="replace")
    else:
        paths, read = staged_files(), staged_content
    problems = check(paths, read)
    if not problems:
        return 0
    print("\nCommit blocked: these look like secrets (API keys / tokens):\n", file=sys.stderr)
    for path, line, kind, masked in problems:
        print(f"  {path}:{line}  {kind}  {masked}", file=sys.stderr)
    print("\nMove secrets to .env (git-ignored) and unstage them. If it's a false alarm, add the comment\n"
          f"'{IGNORE_MARKER}' at the end of that line. (Bypass once with git commit --no-verify.)\n",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
