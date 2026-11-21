"""Turn an area name or pincode into coordinates."""
import re
from dataclasses import dataclass

import requests

import config
from jarvis import http
from jarvis.router import SpokenError

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float


FALLBACK_PLACES = {"new delhi": Place("New Delhi", 28.6139, 77.2090), "delhi": Place("Delhi", 28.6519, 77.2315)}

_CACHE: dict[str, Place | None] = {}

PINCODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Words that mean "wherever I am" rather than a real place name
_VAGUE = {
    "my area", "here", "my location", "my city", "this area", "my place", "my locality", "today", "now", "tomorrow",
    "city", "the city", "this place", "around here",
    "right now", "outside", "the area", "this city", "me", "us", "my home", "home",
}
_NOT_A_PLACE = re.compile(
    r"\b(?:walk|run|jog|exercise|mask|kids|children|outdoors?|outside|go|going|step|people|health|my|minutes?|hours?)\b"
)
_TRAILING = re.compile(r"\s+(?:today|tonight|now|right now|tomorrow|please|currently|this (?:morning|evening|week))$")
_LOCATION_RE = re.compile(r"\b(?:in|at|for|near|around|of)\s+(?!the\s+(?:air|aqi|pm|level))")


def find_pincode(text):
    """Find a 6-digit pincode, also when speech recognition splits it ("110 085")."""
    joined = re.sub(r"(?<=\d)[\s-]+(?=\d)", "", text or "")
    match = PINCODE_RE.search(joined)
    return match.group(1) if match else None


def clean_place(text):
    place = re.sub(r"[?.!,]+$", "", (text or "").strip().lower())
    while True:
        trimmed = _TRAILING.sub("", place).strip()
        if trimmed == place:
            break
        place = trimmed
    place = re.sub(r"^(?:the|my area|area)\s+", "", place).strip()
    if not place or place in _VAGUE or _NOT_A_PLACE.search(place):
        return None
    return place


def location_from_command(command):
    """'pm2.5 in rohini sector 7' -> 'rohini sector 7'; a pincode -> the pincode; 'my area' -> None."""
    pin = find_pincode(command)
    if pin:
        return pin
    matches = list(_LOCATION_RE.finditer(command or ""))
    if not matches:
        return None
    return clean_place(command[matches[-1].end():])


def _nominatim(query):
    params = {"format": "jsonv2", "limit": 1, "addressdetails": 0, "accept-language": "en"}
    pin = find_pincode(query)
    if pin and len(query.replace(" ", "")) <= 8:
        params.update(postalcode=pin, country="India")
    else:
        params.update(q=query, countrycodes=config.COUNTRY_CODE)
    results = http.get_json(NOMINATIM_URL, params=params)
    if not results:
        return None
    first = results[0]
    parts = [p.strip() for p in first.get("display_name", "").split(",") if p.strip() and not p.strip().isdigit()]
    if pin:
        name = parts[0] if parts else f"pincode {pin}"
    elif _DEVANAGARI.search(query):
        name = parts[0] if parts else query  # "दिल्ली" -> "Delhi", so the voice can say it
    else:
        name = query.title()
    return Place(name, float(first["lat"]), float(first["lon"]))


def _open_meteo(query):
    params = {"name": query, "count": 1, "language": "en", "format": "json"}
    if config.COUNTRY_CODE:
        params["countryCode"] = config.COUNTRY_CODE.upper()
    data = http.get_json(OPEN_METEO_GEOCODE_URL, params=params)
    results = data.get("results") or []
    if not results:
        return None
    first = results[0]
    use_result_name = query.isdigit() or _DEVANAGARI.search(query)
    return Place(first.get("name", query) if use_result_name else query.title(), first["latitude"], first["longitude"])


def geocode(query):
    """Return a Place for an area name or pincode, or None if it can't be found."""
    key = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not key:
        return None
    if key in _CACHE:
        return _CACHE[key]
    place, error = None, None
    for finder in (_nominatim, _open_meteo):
        try:
            place = finder(key)
        except (requests.RequestException, ValueError, KeyError) as exc:
            error = exc
            continue
        if place:
            break
    if place is None:
        place = FALLBACK_PLACES.get(key)
    if place is None and isinstance(error, requests.RequestException):
        raise error
    _CACHE[key] = place
    return place


def default_place():
    return geocode(config.DEFAULT_LOCATION) or FALLBACK_PLACES["new delhi"]


def current_place(ctx, command=""):
    """The place named in the command, else the remembered location, else the default."""
    name = location_from_command(command)
    if name:
        place = geocode(name)
        if place is None:
            raise SpokenError(f"Sorry, I couldn't find {name} on the map. Try a nearby area or a pincode.")
        return place
    return ctx.state.get("location") or default_place()
