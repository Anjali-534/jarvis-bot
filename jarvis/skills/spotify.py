"""Spotify: play songs, liked songs and playlists, pause/resume/skip, what's playing, volume.

Uses spotipy with the PKCE flow (no client secret): SPOTIFY_CLIENT_ID in .env, token cached in
data/spotify_token.json. Say "connect spotify" to sign in. If Spotify isn't connected, "play X on
spotify" opens a search in the Spotify app and pause/next fall back to the media keys.
Controlling playback through the API needs Spotify Premium.
"""
import difflib
import functools
import logging
import os
import re
import time
import urllib.parse

import config
from jarvis.router import SpokenError, skill

log = logging.getLogger(__name__)

SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing "
          "user-library-read playlist-read-private playlist-read-collaborative")
SP = r"spotify"
ON_SP = rf"(?:\s+(?:on|in|from|using)\s+{SP})"

NOT_CONNECTED = "Spotify isn't connected yet. Say connect spotify to set it up."
EXPIRED = "Your Spotify sign-in has expired. Say connect spotify to sign in again."
NO_DEVICE = ("I couldn't find a Spotify player. Open the Spotify app, play any song for a second, "
             "then try again.")
PREMIUM = "Spotify only lets apps control playback on Premium accounts."

SETUP_STEPS = """
=== Connect Jarvis to Spotify, one-time setup ===
1. Open https://developer.spotify.com/dashboard and log in with your Spotify account.
2. Click "Create app". Name: Jarvis. Description: anything.
   Redirect URI: {redirect}   (type it exactly, then click Add)
   Tick "Web API", accept the terms and Save.
3. Open the app's Settings and copy the Client ID (you do NOT need the client secret).
4. Put it in your .env file:
       SPOTIFY_CLIENT_ID=your_client_id_here
5. Restart Jarvis and say "connect spotify". A browser opens: log in and click Agree.
Note: Spotify only allows play/pause/skip through the API on Premium accounts.
"""


# ---------------------------------------------------------------- side effects (replaced in tests)
def open_uri(uri):
    os.startfile(uri)


def sleep(seconds):
    time.sleep(seconds)


def media_key(key):
    from jarvis.skills import system

    system.press(key)


# ---------------------------------------------------------------- auth
def setup_steps():
    return SETUP_STEPS.format(redirect=config.SPOTIFY_REDIRECT_URI)


def auth_manager():
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyPKCE

    config.SPOTIFY_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    return SpotifyPKCE(client_id=config.SPOTIFY_CLIENT_ID, redirect_uri=config.SPOTIFY_REDIRECT_URI, scope=SCOPES,
                       cache_handler=CacheFileHandler(cache_path=str(config.SPOTIFY_TOKEN_FILE)), open_browser=True)


def client():
    """A logged-in spotipy client, or None if Spotify isn't connected. Never opens a browser."""
    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_TOKEN_FILE.exists():
        return None
    try:
        import spotipy
    except ImportError:
        return None
    manager = auth_manager()
    try:
        token = manager.validate_token(manager.cache_handler.get_cached_token())  # refreshes if expired
    except Exception as exc:
        log.warning("[spotify] token refresh failed: %s", exc)
        raise SpokenError(EXPIRED) from exc
    if not token:
        return None
    return spotipy.Spotify(auth=token["access_token"], requests_timeout=6, retries=1)


def run_flow():
    """Open the browser for Spotify login and cache the token. Returns the display name."""
    import spotipy

    manager = auth_manager()
    token = manager.get_access_token(check_cache=False)  # listens on the redirect URI for the callback
    user = spotipy.Spotify(auth=token, requests_timeout=6).current_user()
    return user.get("display_name") or user.get("id") or "you"


def friendly_errors(fn):
    """Turn Spotify API failures into clear spoken messages."""

    @functools.wraps(fn)
    def wrapper(ctx, command, match):
        try:
            from spotipy.exceptions import SpotifyException
        except ImportError:
            SpotifyException = SpokenError
        try:
            return fn(ctx, command, match)
        except SpokenError:
            raise
        except SpotifyException as exc:
            status, reason = getattr(exc, "http_status", 0), str(getattr(exc, "reason", "") or exc).upper()
            log.warning("[spotify] HTTP %s: %s", status, exc)
            if status == 401:
                return EXPIRED
            if status == 403 and ("PREMIUM" in reason or "RESTRICT" in reason or "premium" in str(exc).lower()):
                return PREMIUM
            if status == 404 and "DEVICE" in reason:
                return NO_DEVICE
            if status == 429:
                return "Spotify says I'm making too many requests. Please try again in a minute."
            return f"Spotify returned an error, code {status}."

    return wrapper


def need_client():
    sp = client()
    if sp is None:
        raise SpokenError(NOT_CONNECTED)
    return sp


def device_id(sp):
    """An active (or any) Spotify device. Opens the desktop app and retries if there's none."""
    for attempt in range(3):
        devices = sp.devices().get("devices", [])
        if devices:
            device = next((d for d in devices if d.get("is_active")), devices[0])
            return device["id"]
        if attempt == 0:
            log.info("[spotify] no active device, opening the Spotify app")
            open_uri("spotify:")
        sleep(4)
    return None


def start(sp, **kwargs):
    device = device_id(sp)
    if not device:
        raise SpokenError(NO_DEVICE)
    sp.start_playback(device_id=device, **kwargs)


def artists_of(item):
    names = [a.get("name", "") for a in (item or {}).get("artists", [])][:2]
    return " and ".join(n for n in names if n)


# ---------------------------------------------------------------- play
@skill("spotify liked songs", r"^(?:please\s+)?(?:play|shuffle|start)\s+(?:my\s+)?(?:liked|saved|favou?rite)\s+songs"
                              rf"{ON_SP}?$", category="spotify", help=["play my liked songs"])
@friendly_errors
def play_liked(ctx, command, match):
    sp = client()
    if sp is None:
        open_uri("spotify:collection:tracks")
        return "Spotify isn't connected, so I opened your Liked Songs in the app. Press play there."
    items = sp.current_user_saved_tracks(limit=50).get("items", [])
    uris = [i["track"]["uri"] for i in items if i.get("track")]
    if not uris:
        return "You don't have any liked songs on Spotify yet."
    if "shuffle" in command:
        try:
            sp.shuffle(True)
        except Exception:
            pass
    start(sp, uris=uris)
    return "Playing your liked songs on Spotify."


def find_playlist(sp, name):
    """Your own playlists first (fuzzy name match), then Spotify's public ones."""
    mine = sp.current_user_playlists(limit=50).get("items", [])
    by_name = {(p.get("name") or "").lower(): p for p in mine if p}
    if name in by_name:
        return by_name[name]
    contains = [p for key, p in by_name.items() if name in key]
    if contains:
        return contains[0]
    close = difflib.get_close_matches(name, list(by_name), n=1, cutoff=0.6)
    if close:
        return by_name[close[0]]
    found = sp.search(q=name, type="playlist", limit=1).get("playlists", {}).get("items", [])
    return next((p for p in found if p), None)


@skill("spotify playlist",
       rf"^(?:please\s+)?(?:play|shuffle|start)\s+(?:the\s+|my\s+)?playlist\s+(?!on\b)(.+?){ON_SP}?$",
       rf"^(?:please\s+)?(?:play|shuffle|start)\s+(?:the\s+|my\s+)?(.+?)\s+playlist{ON_SP}?$",
       category="spotify", help=["play playlist workout", "play my chill playlist on Spotify"])
@friendly_errors
def play_playlist(ctx, command, match):
    name = match.group(1).strip(" .")
    sp = client()
    if sp is None:
        open_uri(f"spotify:search:{urllib.parse.quote(name + ' playlist')}")
        return f"Spotify isn't connected, so I searched Spotify for the {name} playlist. Say connect spotify to let me play it."
    playlist = find_playlist(sp, name.lower())
    if not playlist:
        return f"I couldn't find a playlist called {name} on Spotify."
    start(sp, context_uri=playlist["uri"])
    return f"Playing the {playlist.get('name') or name} playlist on Spotify."


@skill("spotify play", rf"^(?:please\s+)?play\s+(.+?){ON_SP}$", rf"^(?:please\s+)?{SP}\s+play\s+(.+)$",
       category="spotify", help=["play Believer on Spotify", "play Arijit Singh on Spotify"])
@friendly_errors
def play_song(ctx, command, match):
    query = re.sub(r"^(?:the\s+)?(?:song|track)\s+", "", match.group(1).strip(" ."))
    sp = client()
    if sp is None:
        open_uri(f"spotify:search:{urllib.parse.quote(query)}")
        return f"Spotify isn't connected, so I opened a Spotify search for {query}. Say connect spotify to let me play songs directly."
    tracks = sp.search(q=query, type="track", limit=1).get("tracks", {}).get("items", [])
    if not tracks:
        return f"I couldn't find {query} on Spotify."
    track = tracks[0]
    start(sp, uris=[track["uri"]])
    by = artists_of(track)
    return f"Playing {track.get('name', query)}" + (f" by {by}" if by else "") + " on Spotify."


# ---------------------------------------------------------------- controls
@skill("spotify pause", rf"^(?:please\s+)?(?:pause|stop)\s+(?:the\s+)?(?:music\s+on\s+)?{SP}(?:\s+music)?$",
       rf"^{SP}\s+(?:pause|stop)$", category="spotify", help=["pause Spotify"])
@friendly_errors
def pause(ctx, command, match):
    sp = client()
    if sp is None:
        media_key("playpause")
        return ""
    playback = sp.current_playback()
    if not playback or not playback.get("is_playing"):
        return "Spotify isn't playing anything."
    sp.pause_playback()
    return ""


@skill("spotify resume", rf"^(?:please\s+)?(?:resume|unpause|continue)\s+{SP}$", rf"^{SP}\s+(?:resume|play)$",
       category="spotify", help=["resume Spotify"])
@friendly_errors
def resume(ctx, command, match):
    sp = client()
    if sp is None:
        media_key("playpause")
        return ""
    start(sp)
    return ""


@skill("spotify next", rf"^(?:please\s+)?(?:next|skip)(?:\s+(?:the|this))?(?:\s+(?:song|track))?{ON_SP}$",
       rf"^{SP}\s+(?:next|skip)(?:\s+(?:song|track))?$", category="spotify", help=["next song on Spotify"])
@friendly_errors
def next_song(ctx, command, match):
    sp = client()
    if sp is None:
        media_key("nexttrack")
        return ""
    sp.next_track()
    return ""


@skill("spotify previous", rf"^(?:please\s+)?(?:previous|last)(?:\s+(?:song|track))?{ON_SP}$",
       rf"^{SP}\s+(?:previous|back)(?:\s+(?:song|track))?$", category="spotify", help=["previous song on Spotify"])
@friendly_errors
def previous_song(ctx, command, match):
    sp = client()
    if sp is None:
        media_key("prevtrack")
        return ""
    sp.previous_track()
    return ""


@skill("spotify now playing", r"\bwhat(?:'s| is)\s+(?:this\s+song|playing|the\s+song|on)(?:\s+(?:right\s+)?now)?"
                              rf"{ON_SP}?$", r"\bwhat song is (?:this|playing)\b", rf"\bwhich song is (?:this|playing)\b",
       category="spotify", help=["what's playing"])
@friendly_errors
def now_playing(ctx, command, match):
    if re.search(r"\bwhat(?:'s| is) on\b", command) and SP not in command:
        return None  # "what's on" alone is too vague
    sp = need_client()
    playback = sp.current_playback()
    item = (playback or {}).get("item")
    if not item:
        return "Nothing is playing on Spotify right now."
    by = artists_of(item)
    state = "Playing" if playback.get("is_playing") else "Paused on"
    return f"{state} {item.get('name', 'an unknown track')}" + (f" by {by}" if by else "") + "."


@skill("spotify volume", rf"^(?:set\s+)?{SP}\s+volume\s+(?:to\s+)?(\d{{1,3}})(?:\s*(?:percent|%))?$",
       rf"^(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+)?(\d{{1,3}})(?:\s*(?:percent|%))?{ON_SP}$",
       category="spotify", help=["Spotify volume 50"])
@friendly_errors
def volume(ctx, command, match):
    level = max(0, min(100, int(match.group(1))))
    sp = need_client()
    device = device_id(sp)
    if not device:
        return NO_DEVICE
    sp.volume(level, device_id=device)
    return f"Spotify volume {level}."


# ---------------------------------------------------------------- connect
@skill("connect spotify", rf"\b(?:connect|link|sign in to|sign into|log ?in to|set up|setup)\s+(?:my\s+)?{SP}"
                          rf"(?:\s+account)?$", category="spotify", help=["connect spotify"])
def connect_spotify(ctx, command, match):
    try:
        import spotipy  # noqa: F401
    except ImportError:
        return "The spotipy library isn't installed. Run pip install -r requirements.txt, then try again."
    if not config.SPOTIFY_CLIENT_ID:
        log.info(setup_steps())
        return ("First you need a Spotify app. I've printed the steps in the terminal and the log, and they're in "
                "the README. Put its client ID in the .env file as SPOTIFY_CLIENT_ID, restart me, "
                "and say connect spotify again.")
    ctx.say("I'm opening Spotify login in your browser. Log in and click Agree.")
    try:
        name = run_flow()
    except Exception as exc:
        log.warning("[spotify] sign-in failed: %s", exc)
        return ("The Spotify sign-in didn't finish. Check that the app's redirect URI is exactly "
                f"{config.SPOTIFY_REDIRECT_URI}, then say connect spotify again.")
    return f"Spotify is connected, {name}. Try: play Believer on Spotify."
