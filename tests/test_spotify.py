"""Spotify skill tests. spotipy, the Spotify app and the media keys are all faked."""
import logging

import pytest
from fakes import FakeSpotify
from helpers import make_ctx

import config
from jarvis.router import handle
from jarvis.skills import spotify


@pytest.fixture
def launched(monkeypatch):
    log = []
    monkeypatch.setattr(spotify, "open_uri", lambda uri: log.append(("uri", uri)))
    monkeypatch.setattr(spotify, "media_key", lambda key: log.append(("key", key)))
    monkeypatch.setattr(spotify, "sleep", lambda s: log.append(("sleep", s)))
    return log


def connect(monkeypatch, fake):
    monkeypatch.setattr(spotify, "client", lambda: fake)
    return fake


# ---------------------------------------------------------------- not connected
def test_client_is_none_without_setup():
    assert spotify.client() is None  # no client id, no token: never opens a browser


def test_play_falls_back_to_search_uri(launched):
    ctx = make_ctx()
    assert handle(ctx, "play believer by imagine dragons on spotify") == "spotify play"
    assert launched == [("uri", "spotify:search:believer%20by%20imagine%20dragons")]
    assert ctx.voice.last.startswith("Spotify isn't connected, so I opened a Spotify search")


def test_liked_and_playlist_fall_back(launched):
    handle(make_ctx(), "play my liked songs")
    handle(make_ctx(), "play playlist workout")
    assert launched == [("uri", "spotify:collection:tracks"), ("uri", "spotify:search:workout%20playlist")]


def test_controls_fall_back_to_media_keys(launched):
    for phrase in ("pause spotify", "next song on spotify", "spotify previous", "resume spotify"):
        handle(make_ctx(), phrase)
    assert launched == [("key", "playpause"), ("key", "nexttrack"), ("key", "prevtrack"), ("key", "playpause")]


@pytest.mark.parametrize("phrase", ["what's playing", "spotify volume 50"])
def test_not_connected_message(launched, phrase):
    ctx = make_ctx()
    handle(ctx, phrase)
    assert ctx.voice.last == spotify.NOT_CONNECTED
    assert launched == []


def test_connect_without_client_id_prints_steps(caplog):
    ctx = make_ctx()
    with caplog.at_level(logging.INFO):
        assert handle(ctx, "connect spotify") == "connect spotify"
    assert "developer.spotify.com/dashboard" in caplog.text and "http://127.0.0.1:8888/callback" in caplog.text
    assert "SPOTIFY_CLIENT_ID" in ctx.voice.last


def test_connect_runs_pkce_flow(monkeypatch):
    monkeypatch.setattr(config, "SPOTIFY_CLIENT_ID", "abc")
    monkeypatch.setattr(spotify, "run_flow", lambda: "Anjali")
    ctx = make_ctx()
    handle(ctx, "connect my spotify account")
    assert ctx.voice.last == "Spotify is connected, Anjali. Try: play Believer on Spotify."


# ---------------------------------------------------------------- connected
def test_play_song(monkeypatch, launched):
    fake = connect(monkeypatch, FakeSpotify())
    ctx = make_ctx()
    handle(ctx, "play the song believer on spotify")
    assert fake.calls == [("search", "believer", "track"), ("play", "pc", {"uris": ["spotify:track:believer"]})]
    assert ctx.voice.last == "Playing Believer by Imagine Dragons on Spotify."


def test_play_opens_app_when_no_device_then_retries(monkeypatch, launched):
    fake = connect(monkeypatch, FakeSpotify(devices=[[], [{"id": "laptop", "is_active": False}]]))
    handle(make_ctx(), "play believer on spotify")
    assert launched == [("uri", "spotify:"), ("sleep", 4)]
    assert fake.calls[-1] == ("play", "laptop", {"uris": ["spotify:track:believer"]})


def test_play_with_no_device_at_all(monkeypatch, launched):
    fake = connect(monkeypatch, FakeSpotify(devices=[[]]))
    ctx = make_ctx()
    handle(ctx, "play believer on spotify")
    assert ctx.voice.last == spotify.NO_DEVICE
    assert ("uri", "spotify:") in launched and not any(c[0] == "play" for c in fake.calls)


def test_play_liked_songs(monkeypatch, launched):
    liked = [{"uri": f"spotify:track:{i}", "name": str(i), "artists": []} for i in range(3)]
    fake = connect(monkeypatch, FakeSpotify(liked=liked))
    ctx = make_ctx()
    assert handle(ctx, "play my liked songs") == "spotify liked songs"
    assert fake.calls == [("play", "pc", {"uris": ["spotify:track:0", "spotify:track:1", "spotify:track:2"]})]
    assert ctx.voice.last == "Playing your liked songs on Spotify."


def test_play_own_playlist_fuzzy(monkeypatch, launched):
    playlists = [{"name": "Workout Mix", "uri": "spotify:playlist:w"}, {"name": "Chill", "uri": "spotify:playlist:c"}]
    fake = connect(monkeypatch, FakeSpotify(playlists=playlists))
    ctx = make_ctx()
    assert handle(ctx, "play my workout playlist on spotify") == "spotify playlist"
    assert fake.calls == [("play", "pc", {"context_uri": "spotify:playlist:w"})]
    assert ctx.voice.last == "Playing the Workout Mix playlist on Spotify."


def test_controls(monkeypatch, launched):
    playing = {"is_playing": True, "item": {"name": "Kesariya", "artists": [{"name": "Arijit Singh"}]}}
    fake = connect(monkeypatch, FakeSpotify(playback=playing))
    ctx = make_ctx()
    handle(ctx, "pause spotify")
    handle(ctx, "next song on spotify")
    handle(ctx, "previous song on spotify")
    handle(ctx, "spotify volume 50")
    assert fake.calls == [("pause",), ("next",), ("previous",), ("volume", 50, "pc")]
    assert ctx.voice.last == "Spotify volume 50."
    handle(ctx, "what's playing")
    assert ctx.voice.last == "Playing Kesariya by Arijit Singh."


def test_nothing_playing(monkeypatch, launched):
    connect(monkeypatch, FakeSpotify(playback=None))
    ctx = make_ctx()
    handle(ctx, "what's playing on spotify")
    assert ctx.voice.last == "Nothing is playing on Spotify right now."
    handle(ctx, "pause spotify")
    assert ctx.voice.last == "Spotify isn't playing anything."


def test_premium_required_is_explained(monkeypatch, launched):
    from spotipy.exceptions import SpotifyException

    fake = connect(monkeypatch, FakeSpotify())

    def refuse(**kwargs):
        raise SpotifyException(403, -1, "Player command failed: Premium required", reason="PREMIUM_REQUIRED")

    fake.next_track = refuse
    ctx = make_ctx()
    handle(ctx, "spotify next")
    assert ctx.voice.last == spotify.PREMIUM


def test_plain_media_commands_still_use_media_keys(recorded_skills):
    from jarvis.router import handle as route

    assert route(make_ctx(), "next song") == "next track"
    assert route(make_ctx(), "pause the music") == "play pause"
    assert route(make_ctx(), "play believer") == "play youtube"
