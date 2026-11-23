"""In-memory stand-ins for the Gmail, Calendar and Spotify APIs (no network, nothing sent)."""
import base64


class _Request:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result() if callable(self._result) else self._result


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def mail(mid, sender, subject, body="", to="anjali@example.com", date="Mon, 23 Nov 2026 09:00:00 +0530", html=False):
    part = {"mimeType": "text/html" if html else "text/plain", "body": {"data": b64(body)}}
    return {
        "id": mid, "snippet": body[:80],
        "payload": {"mimeType": "multipart/alternative", "headers": [
            {"name": "From", "value": sender}, {"name": "To", "value": to},
            {"name": "Subject", "value": subject}, {"name": "Date", "value": date},
        ], "parts": [part]},
    }


class FakeGmail:
    """service.users().messages().list/get and drafts().create. There is no send at all."""

    def __init__(self, messages=(), searches=None, more=()):
        self.store = {m["id"]: m for m in messages}
        self.searches = searches or {}   # query -> [ids]
        self.more = set(more)            # queries that report a next page
        self.queries, self.created_drafts, self.fetched = [], [], []

    def users(self):
        return self

    def messages(self):
        return self._Messages(self)

    def drafts(self):
        return self._Drafts(self)

    class _Messages:
        def __init__(self, gmail):
            self.gmail = gmail

        def list(self, userId, q, maxResults):
            self.gmail.queries.append(q)
            ids = self.gmail.searches.get(q, [])[:maxResults]
            result = {"messages": [{"id": i} for i in ids], "resultSizeEstimate": len(ids)}
            if q in self.gmail.more:
                result["nextPageToken"] = "next"
            return _Request(result)

        def get(self, userId, id, format="full", metadataHeaders=None):
            self.gmail.fetched.append((id, format))
            return _Request(self.gmail.store[id])

    class _Drafts:
        def __init__(self, gmail):
            self.gmail = gmail

        def create(self, userId, body):
            self.gmail.created_drafts.append(body)
            return _Request({"id": "draft1"})


def decode_raw(raw):
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


class FakeCalendar:
    def __init__(self, items=()):
        self.items = list(items)
        self.list_calls, self.inserted, self.deleted = [], [], []

    def events(self):
        return self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _Request({"items": list(self.items)})

    def insert(self, calendarId, body):
        self.inserted.append(body)
        return _Request({"id": "new", **body})

    def delete(self, calendarId, eventId):
        self.deleted.append(eventId)
        return _Request("")


def event(eid, title, start, minutes=30, all_day=False):
    """start: an ISO string with offset (e.g. '2026-11-23T17:00:00+05:30') or a date for all-day events."""
    if all_day:
        return {"id": eid, "summary": title, "start": {"date": start}, "end": {"date": start}}
    return {"id": eid, "summary": title, "start": {"dateTime": start}, "end": {"dateTime": start}}


class FakeSpotify:
    def __init__(self, devices=None, tracks=None, playlists=None, liked=None, playback=None):
        self._devices = [list(d) for d in (devices if devices is not None else [[{"id": "pc", "is_active": True}]])]
        self.tracks = tracks if tracks is not None else [
            {"name": "Believer", "uri": "spotify:track:believer", "artists": [{"name": "Imagine Dragons"}]}]
        self.playlists = playlists or []
        self.liked = liked or []
        self.playback = playback
        self.calls = []

    def devices(self):
        current = self._devices.pop(0) if len(self._devices) > 1 else self._devices[0]
        return {"devices": current}

    def search(self, q, type, limit):
        self.calls.append(("search", q, type))
        if type == "track":
            return {"tracks": {"items": self.tracks[:limit]}}
        return {"playlists": {"items": [p for p in self.playlists if q in p["name"].lower()][:limit]}}

    def current_user_playlists(self, limit):
        return {"items": self.playlists}

    def current_user_saved_tracks(self, limit):
        return {"items": [{"track": t} for t in self.liked]}

    def current_playback(self):
        return self.playback

    def start_playback(self, device_id=None, **kwargs):
        self.calls.append(("play", device_id, kwargs))

    def pause_playback(self):
        self.calls.append(("pause",))

    def next_track(self):
        self.calls.append(("next",))

    def previous_track(self):
        self.calls.append(("previous",))

    def volume(self, level, device_id=None):
        self.calls.append(("volume", level, device_id))

    def shuffle(self, state):
        self.calls.append(("shuffle", state))
