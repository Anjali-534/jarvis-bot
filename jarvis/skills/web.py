"""YouTube, Google search and news."""
import logging
import re
import webbrowser
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

from jarvis import http
from jarvis.router import skill

log = logging.getLogger(__name__)


def open_url(url):
    webbrowser.open(url)


def find_youtube_video(query):
    """Return the first video id from YouTube search results, or None."""
    html = http.get_text("https://www.youtube.com/results", params={"search_query": query})
    found = re.search(r'"videoId":"([\w-]{11})"', html)
    return found.group(1) if found else None


@skill("play youtube", r"^(?:please\s+)?play\s+(.+?)(?:\s+(?:on|in|from)\s+youtube)?$", category='web', help=['play Arijit Singh songs', 'play lofi beats on YouTube'])
def play_youtube(ctx, command, match):
    query = match.group(1).strip()
    try:
        video = find_youtube_video(query)
    except Exception as exc:
        log.warning(f"[youtube] search failed: {exc}")
        video = None
    if video:
        open_url(f"https://www.youtube.com/watch?v={video}")
        return f"Playing {query} on YouTube."
    open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Here are YouTube results for {query}."


@skill("youtube search", r"\bsearch\s+(?:for\s+)?(.+?)\s+(?:on|in)\s+youtube\b", r"\bsearch youtube for\s+(.+)$",
       r"^youtube\s+(?:search\s+)?(?:for\s+)?(.+)$", category='web', help=['search lofi music on YouTube'])
def youtube_search(ctx, command, match):
    query = match.group(1).strip()
    open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Searching YouTube for {query}."


@skill("google", r"^(?:please\s+)?(?:search|google|look up|search for|search google for)\s+(?:for\s+)?(.+?)(?:\s+on google)?$", category='web', help=['search best laptops under 60000'])
def google(ctx, command, match):
    query = match.group(1).strip()
    open_url(f"https://www.google.com/search?q={quote_plus(query)}")
    return f"Searching Google for {query}."


# ---------------------------------------------------------------- news
NEWS_TOP = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
NEWS_SEARCH = "https://news.google.com/rss/search"
_GENERIC = {"", "latest", "the latest", "today's", "todays", "top", "the", "some", "any", "breaking", "recent", "the top",
            "me the", "me", "me some", "me the latest", "headline", "india"}


def news_topic(command):
    found = re.search(r"\b(?:news|headlines)\s+(?:about|on|regarding|for|related to|of)\s+(.+)$", command)
    if not found:
        found = re.search(r"^(?:what's the |what is the |tell me |give me |read )?(?:the )?(?:latest )?(.+?)\s+(?:news|headlines)\b", command)
    topic = found.group(1).strip(" ?.") if found else ""
    return "" if topic in _GENERIC else topic


def headlines(topic="", limit=5):
    if topic:
        xml_text = http.get_text(NEWS_SEARCH, params={"q": topic, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    else:
        xml_text = http.get_text(NEWS_TOP)
    root = ET.fromstring(xml_text)
    titles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        title = re.sub(r"\s+-\s+[^-]+$", "", title)
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


@skill("news", r"\bnews\b", r"\bheadlines\b", category='web', help=['news', 'news about cricket'])
def news(ctx, command, match):
    topic = news_topic(command)
    titles = headlines(topic)
    if not titles:
        return f"I couldn't find any news about {topic}." if topic else "I couldn't load the news right now."
    intro = f"Here's the latest on {topic}." if topic else "Here are today's top headlines."
    return intro + " " + " ".join(f"{i}. {t}." for i, t in enumerate(titles, 1))
