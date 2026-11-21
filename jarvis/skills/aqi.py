"""Live air quality: Indian CPCB AQI from Open-Meteo, red zones from WAQI or Open-Meteo."""
import re
from dataclasses import dataclass

import config
from jarvis import geo, http
from jarvis.geo import Place
from jarvis.router import skill

AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WAQI_BOUNDS_URL = "https://api.waqi.info/v2/map/bounds"
POLLUTANTS = ("pm2_5", "pm10", "nitrogen_dioxide")
SPOKEN = {"pm2_5": "PM2.5", "pm10": "PM10", "nitrogen_dioxide": "NO2"}

# CPCB breakpoints: (concentration low, concentration high) for each AQI band.
# The top band is open-ended in CPCB tables; we cap it at a practical upper concentration.
INDEX_BANDS = [(0, 50), (50, 100), (100, 200), (200, 300), (300, 400), (400, 500)]
BREAKPOINTS = {
    "pm2_5": [(0, 30), (30, 60), (60, 90), (90, 120), (120, 250), (250, 380)],
    "pm10": [(0, 50), (50, 100), (100, 250), (250, 350), (350, 430), (430, 510)],
    "nitrogen_dioxide": [(0, 40), (40, 80), (80, 180), (180, 280), (280, 400), (400, 500)],
}

CATEGORIES = [
    (50, "Good", "Air is clean. It's safe to go out and exercise."),
    (100, "Satisfactory", "Air is fine for most people. Very sensitive people may feel minor discomfort."),
    (200, "Moderate", "A mask is recommended for children, the elderly and people with asthma or heart problems."),
    (300, "Poor", "Wear an N95 mask outside and avoid outdoor exercise."),
    (400, "Very Poor", "Limit time outdoors, wear an N95 mask, and keep windows closed."),
    (10**9, "Severe", "Stay indoors, run an air purifier if you have one, and wear an N95 if you must go out."),
]

NCR_POINTS = [
    Place("Central Delhi", 28.6328, 77.2197), Place("Rohini", 28.7383, 77.0822), Place("Dwarka", 28.5921, 77.0460),
    Place("Anand Vihar", 28.6469, 77.3164), Place("Noida", 28.5355, 77.3910), Place("Greater Noida", 28.4744, 77.5040),
    Place("Ghaziabad", 28.6692, 77.4538), Place("Gurugram", 28.4595, 77.0266), Place("Faridabad", 28.4089, 77.3178),
    Place("Sonipat", 28.9931, 77.0151),
]


# ---------------------------------------------------------------- CPCB maths
def sub_index(pollutant, concentration):
    if concentration is None:
        return None
    concentration = max(0.0, float(concentration))
    for (c_lo, c_hi), (i_lo, i_hi) in zip(BREAKPOINTS[pollutant], INDEX_BANDS):
        if concentration <= c_hi:
            return round(i_lo + (concentration - c_lo) * (i_hi - i_lo) / (c_hi - c_lo))
    return 500


def category(aqi):
    for limit, name, advice in CATEGORIES:
        if aqi <= limit:
            return name, advice
    return CATEGORIES[-1][1:]


def zone(aqi):
    return "Green" if aqi <= 100 else "Yellow" if aqi <= 200 else "Red"


@dataclass
class AirReading:
    place: Place
    current: dict
    average: dict
    aqi: int
    dominant: str

    @property
    def category(self):
        return category(self.aqi)[0]

    @property
    def advice(self):
        return category(self.aqi)[1]

    @property
    def zone(self):
        return zone(self.aqi)


def _average_24h(hourly, pollutant, now_time):
    times = hourly.get("time", [])
    values = hourly.get(pollutant, [])
    upto = [v for t, v in zip(times, values) if t <= now_time]
    recent = [v for v in upto[-24:] if v is not None]
    return sum(recent) / len(recent) if recent else None


def parse_reading(place, data):
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    now_time = current.get("time") or (hourly.get("time") or [""])[-1]
    averages, indices = {}, {}
    for pollutant in POLLUTANTS:
        avg = _average_24h(hourly, pollutant, now_time)
        if avg is None:
            avg = current.get(pollutant)
        averages[pollutant] = avg
        index = sub_index(pollutant, avg)
        if index is not None:
            indices[pollutant] = index
    if not indices:
        raise ValueError("no pollutant data")
    dominant = max(indices, key=indices.get)
    return AirReading(place, {p: current.get(p) for p in POLLUTANTS}, averages, indices[dominant], dominant)


def fetch_air(places):
    """Fetch readings for one or many places in a single Open-Meteo call."""
    places = list(places)
    data = http.get_json(AIR_URL, params={
        "latitude": ",".join(f"{p.lat:.4f}" for p in places),
        "longitude": ",".join(f"{p.lon:.4f}" for p in places),
        "current": ",".join(POLLUTANTS), "hourly": ",".join(POLLUTANTS),
        "past_days": 1, "forecast_days": 1, "timezone": "auto",
    })
    items = data if isinstance(data, list) else [data]
    return [parse_reading(place, item) for place, item in zip(places, items)]


def reading_for(ctx, command):
    return fetch_air([geo.current_place(ctx, command)])[0]


def summary(reading):
    return (f"The AQI in {reading.place.name} is {reading.aqi}, which is {reading.category}, "
            f"a {reading.zone.lower()} zone.")


# ---------------------------------------------------------------- skills
@skill("set location", r"\b(?:my (?:area|location|city|locality|pin ?code) is|i live in|i stay in|i am in|i'm in|"
                       r"set (?:my )?(?:location|area) to|change (?:my )?(?:location|area) to)\s+(.+)$",
       r"^(?:pin ?code\s+)?(\d[\d\s-]{4,}\d)$", category='air quality', help=['my area is Rohini', '110085'])
def set_location(ctx, command, match):
    raw = match.group(1).strip(" .")
    name = geo.find_pincode(raw) or geo.clean_place(raw) or raw
    if re.fullmatch(r"[\d\s-]+", raw) and not geo.find_pincode(raw):
        return None  # a random number, not a pincode
    place = geo.geocode(name)
    if place is None:
        return f"Sorry, I couldn't find {name}. Try a nearby area or your 6-digit pincode."
    ctx.state["location"] = place
    reading = fetch_air([place])[0]
    pin = geo.find_pincode(name)
    label = f"{place.name}, pincode {pin}," if pin and pin not in place.name else place.name
    return f"Got it, I'll use {label} as your area. {summary(reading)}"


def _pollutant_reply(ctx, command, pollutant):
    reading = reading_for(ctx, command)
    now, avg = reading.current.get(pollutant), reading.average.get(pollutant)
    if now is None and avg is None:
        return f"Sorry, there's no {SPOKEN[pollutant]} data for {reading.place.name} right now."
    pz = zone(sub_index(pollutant, avg if avg is not None else now))
    text = f"In {reading.place.name}, {SPOKEN[pollutant]} is "
    text += f"{now:.0f} micrograms per cubic metre right now" if now is not None else "unavailable right now"
    if avg is not None:
        text += f", with a 24-hour average of {avg:.0f}"
    return text + f". That's a {pz.lower()} zone."


PM = r"\bp\.?\s?m\.?\s?"


@skill("pm2.5", PM + r"2(?:\s*(?:\.|point|dot)\s*|\s)?5\b", r"\bfine particles?\b", r"\bparticulate matter 2", category='air quality', help=['PM2.5 in Dwarka'])
def pm25(ctx, command, match):
    return _pollutant_reply(ctx, command, "pm2_5")


@skill("pm10", PM + r"(?:10|ten)\b", category='air quality', help=['PM10'])
def pm10(ctx, command, match):
    return _pollutant_reply(ctx, command, "pm10")


@skill("no2", r"\bn\.?\s?o\.?\s?(?:2|two)\b", r"\bnitrogen dioxide\b", category='air quality', help=['NO2 level'])
def no2(ctx, command, match):
    return _pollutant_reply(ctx, command, "nitrogen_dioxide")


def _waqi_red_zones(place):
    data = http.get_json(WAQI_BOUNDS_URL, params={
        "latlng": f"{place.lat - 0.35},{place.lon - 0.35},{place.lat + 0.35},{place.lon + 0.35}",
        "networks": "all", "token": config.WAQI_TOKEN,
    })
    if data.get("status") != "ok":
        raise ValueError(f"WAQI error: {data.get('data')}")
    stations = []
    for item in data.get("data", []):
        value = str(item.get("aqi", "-")).strip()
        if not value.isdigit():
            continue
        name = (item.get("station") or {}).get("name", "Unknown station").split(",")[0].strip()
        stations.append((name, int(value)))
    stations.sort(key=lambda s: -s[1])
    return stations


@skill("red zones", r"\bred\s*zones?\b", r"\bred areas?\b", r"\bmost polluted\b", r"\bworst (?:air|aqi|areas?|pollution)\b", category='air quality', help=['red zones'])
def red_zones(ctx, command, match):
    threshold = config.RED_ZONE_THRESHOLD
    if config.WAQI_TOKEN:
        place = geo.current_place(ctx, command)
        try:
            stations = _waqi_red_zones(place)
        except ValueError as exc:
            print(f"[aqi] {exc}; falling back to Open-Meteo")
            stations = None
        if stations:
            red = [s for s in stations if s[1] > threshold][:6]
            if red:
                listed = ", ".join(f"{name} {aqi}" for name, aqi in red)
                return f"Red zones near {place.name} right now, from live monitoring stations: {listed}."
            name, aqi = stations[0]
            return f"No station near {place.name} is above {threshold} right now. The worst is {name} at {aqi}."
    readings = sorted(fetch_air(NCR_POINTS), key=lambda r: -r.aqi)
    red = [r for r in readings if r.aqi > threshold][:6]
    if red:
        listed = ", ".join(f"{r.place.name} {r.aqi}" for r in red)
        return f"Today's red zones in Delhi NCR are: {listed}. These are area-level estimates."
    worst = readings[0]
    return (f"None of the Delhi NCR areas I track are in the red zone right now. "
            f"The worst is {worst.place.name} with an AQI of {worst.aqi}, {worst.category}.")


_OUTDOOR = r"(?:go|step|walk|run|jog|exercise|play|cycle|be)\b"


@skill("safe outside",
       r"^(?:is it\s+)?safe(?:\s+(?:today|now|outside))?$",
       rf"\bis it safe\b(?!\s+to\s+(?!{_OUTDOOR})\w+)(?!\s+for\s+(?:me\s+)?to\s+(?!{_OUTDOOR})\w+)",
       rf"\bsafe\s+(?:to\s+{_OUTDOOR}|outside|outdoors|for\s+(?:a\s+)?(?:walk|run|jog|morning walk|kids|children|exercise))",
       r"\bpollution safe\b", r"\b(?:need|wear|carry) (?:a |my )?mask\b", r"\bmorning walk\b",
       r"\bgo (?:out|outside)\b.*\b(?:safe|okay|ok)\b", category='air quality', help=['is it safe to go out', 'do I need a mask'])
def safe_outside(ctx, command, match):
    reading = reading_for(ctx, command)
    return f"{summary(reading)} {reading.advice}"


@skill("aqi", r"\ba\.?\s?q\.?\s?i\b", r"\bair quality\b", r"\bpollution\b", r"\bsmog\b", r"\bhow is the air\b",
       r"\bair index\b", category='air quality', help=["what's the AQI", 'air quality in Noida'])
def aqi_skill(ctx, command, match):
    reading = reading_for(ctx, command)
    return f"{summary(reading)} The main pollutant is {SPOKEN[reading.dominant]}. {reading.advice}"
