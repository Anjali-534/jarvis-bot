"""Short Wikipedia answers. Returns None on any problem so the AI can answer instead."""
import re
from urllib.parse import quote

from jarvis import http
from jarvis.router import skill

OPENSEARCH_URL = "https://en.wikipedia.org/w/api.php"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"


def first_sentences(text, count=2):
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text.strip())
    return " ".join(sentences[:count]).strip()


def wikipedia_summary(topic):
    results = http.get_json(OPENSEARCH_URL, params={
        "action": "opensearch", "search": topic, "limit": 1, "namespace": 0, "format": "json",
    })
    titles = results[1] if len(results) > 1 else []
    if not titles:
        return None
    page = http.get_json(SUMMARY_URL + quote(titles[0].replace(" ", "_"), safe=""))
    if page.get("type") == "disambiguation" or not page.get("extract"):
        return None
    return first_sentences(page["extract"])


@skill("wikipedia",
       r"^(?:who\s+(?:is|was|are|were)|tell me (?:something )?about|what\s+(?:is|are|was|were)(?:\s+(?:a|an|the))?|"
       r"search wikipedia for|wikipedia)\s+(.+?)\??$")
def wikipedia(ctx, command, match):
    topic = match.group(1).strip()
    if not topic or len(topic) < 2 or re.search(r"\b(?:you|your|my|i|me|this|that|it)\b", topic):
        return None
    try:
        return wikipedia_summary(topic)
    except Exception as exc:
        print(f"[wikipedia] {exc}")
        return None
