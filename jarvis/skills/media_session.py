"""What's playing in ANY app (Spotify, YouTube in Chrome, VLC...), through the Windows media session API.

Uses pywinrt (winrt-Windows.Media.Control, works on Python 3.13). "pause", "play", "next" and "previous" go to the
active session (system.py calls control()); without one they fall back to the media keys.
"""
import logging
import re

from jarvis.router import skill
from jarvis.skills import system

log = logging.getLogger(__name__)

# AppUserModelIDs -> names people say
APP_NAMES = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "308046b0af4a39cb": "Firefox",
             "vlc": "VLC", "videolan": "VLC", "brave": "Brave", "zunemusic": "Media Player", "zunevideo": "Movies & TV",
             "microsoft.media.player": "Media Player", "applemusic": "Apple Music", "amazonmusic": "Amazon Music",
             "whatsapp": "WhatsApp", "netflix": "Netflix", "primevideo": "Prime Video", "youtube music": "YouTube Music"}


def app_name(app_id):
    key = (app_id or "").lower()
    for part, name in APP_NAMES.items():
        if part in key:
            return name
    key = re.sub(r"\.exe$", "", key.split("!")[0].split("\\")[-1])
    return key.split("_")[0].split(".")[-1].capitalize() or "an app"


def sessions():
    """[{"app", "title", "artist", "playing", "session"}], the system's current session first."""
    from jarvis.skills.settings_control import run_async

    async def read():
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
        )

        manager = await Manager.request_async()
        current = manager.get_current_session()
        found = []
        for session in manager.get_sessions():
            try:
                props = await session.try_get_media_properties_async()
                status = session.get_playback_info().playback_status
            except Exception:
                continue
            found.append({"app": app_name(session.source_app_user_model_id), "title": (props.title or "").strip(),
                          "artist": (props.artist or "").strip(), "playing": status == Status.PLAYING,
                          "session": session, "current": current is not None and
                          session.source_app_user_model_id == current.source_app_user_model_id})
        found.sort(key=lambda s: (not s["playing"], not s["current"]))
        return found

    return run_async(read)


def _call(session, action):
    from jarvis.skills.settings_control import run_async

    method = {"pause": session.try_pause_async, "play": session.try_play_async, "next": session.try_skip_next_async,
              "previous": session.try_skip_previous_async, "toggle": session.try_toggle_play_pause_async}[action]

    async def go():
        return await method()

    return bool(run_async(go))


def control(action):
    """Send play/pause/next/previous to the active media session. False if there's none (use the media keys)."""
    try:
        found = sessions()
    except Exception:
        log.debug("No media session API", exc_info=True)
        return False
    if not found:
        return False
    if action == "play":
        target = next((s for s in found if s["current"]), found[0])
    else:
        target = next((s for s in found if s["playing"]), next((s for s in found if s["current"]), found[0]))
    try:
        return _call(target["session"], action)
    except Exception:
        log.debug("Media session %s failed", action, exc_info=True)
        return False


def describe(item):
    title = item["title"] or "something"
    artist = item["artist"]
    # YouTube puts the channel in "artist"; long drama titles often repeat it after a "|"
    title = re.split(r"\s+\|\s+", title)[0] if len(title) > 70 else title
    by = f" by {artist.split('|')[0].strip()}" if artist and artist.lower() not in title.lower() else ""
    return f"{title}{by}"


@skill("now playing", r"\bwhat(?:'s| is)\s+(?:this\s+song|playing|the\s+song|on)(?:\s+(?:right\s+)?now)?\??$",
       r"\bwhat song is (?:this|playing)\b", r"\bwhich song is (?:this|playing)\b", r"\bwhat am i (?:listening to|watching)\b",
       r"\b(?:kaunsa|kya|konsa)\s+(?:gaana|gana|song)\s+(?:chal|baj)\s+raha\b", r"\bkya\s+chal\s+raha\s+hai\b",
       category="laptop", help=["what's playing"], params={}, tool="now_playing")
def now_playing(ctx, command, match):
    if re.search(r"\bwhat(?:'s| is) on\b", command) and not re.search(r"\b(?:now|right now)\b", command):
        return None  # "what's on" alone is too vague (the calendar skill has "what's on my calendar")
    if not system.IS_WINDOWS:
        return system.NOT_WINDOWS
    try:
        found = sessions()
    except Exception:
        log.info("Media session API unavailable", exc_info=True)
        found = []
    playing = [s for s in found if s["playing"]]
    if playing:
        first = playing[0]
        reply = f"Playing {describe(first)} on {first['app']}."
        if len(playing) > 1:
            reply += " Also " + ", ".join(f"{describe(s)} on {s['app']}" for s in playing[1:3]) + "."
        return reply
    if found:
        return f"Nothing's playing. {describe(found[0])} is paused on {found[0]['app']}."
    from jarvis.skills import spotify

    try:
        if spotify.client() is not None:
            return None  # maybe Spotify on your phone: its skill knows
    except Exception:
        pass
    return "Nothing's playing right now."
