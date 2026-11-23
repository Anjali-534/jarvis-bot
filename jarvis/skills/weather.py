"""Weather and rain forecasts from Open-Meteo (no API key)."""
import re

import config
from jarvis import geo, http
from jarvis.router import skill

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES = {
    0: "clear skies", 1: "mainly clear skies", 2: "partly cloudy skies", 3: "overcast skies",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers", 85: "snow showers",
    86: "heavy snow showers", 95: "a thunderstorm", 96: "a thunderstorm with hail", 99: "a severe thunderstorm with hail",
}


def describe(code):
    return WMO_CODES.get(int(code) if code is not None else -1, "mixed conditions")


def forecast(place):
    return http.get_json(FORECAST_URL, params={
        "latitude": place.lat, "longitude": place.lon,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto", "forecast_days": 2,
    }, cache_ttl=config.CACHE_MINUTES * 60)


def _day(data, index):
    daily = data["daily"]
    return {
        "code": daily["weather_code"][index], "max": daily["temperature_2m_max"][index],
        "min": daily["temperature_2m_min"][index], "rain": daily["precipitation_probability_max"][index] or 0,
    }


def rain_verdict(chance):
    if chance >= 60:
        return "Yes, rain is likely"
    if chance >= 30:
        return "Maybe, there's some chance of rain"
    return "Rain is unlikely"


@skill("rain", r"\bwill it rain\b", r"\bumbrella\b", r"\bis it (?:going to )?rain(?:ing)?\b", r"\b(?:rain|raining)\b.*\b(?:today|tomorrow)\b",
       r"\bchance of rain\b", category='time and weather', help=['will it rain tomorrow', 'do I need an umbrella'])
def rain(ctx, command, match):
    place = geo.current_place(ctx, command)
    tomorrow = "tomorrow" in command
    day = _day(forecast(place), 1 if tomorrow else 0)
    when = "tomorrow" if tomorrow else "today"
    reply = f"{rain_verdict(day['rain'])} in {place.name} {when}: {day['rain']:.0f} percent chance."
    if day["rain"] >= 30:
        reply += " Better carry an umbrella."
    return reply


@skill("weather", r"\bweather\b", r"\btemperature\b", r"\bforecast\b", r"\bhow (?:hot|cold|humid) is it\b",
       r"\bis it (?:hot|cold|humid)\b", category='time and weather', help=["what's the weather", 'weather in Mumbai tomorrow'])
def weather(ctx, command, match):
    place = geo.current_place(ctx, command)
    data = forecast(place)
    if re.search(r"\btomorrow\b", command):
        day = _day(data, 1)
        return (f"Tomorrow in {place.name}: {describe(day['code'])}, between {day['min']:.0f} and {day['max']:.0f} degrees, "
                f"with a {day['rain']:.0f} percent chance of rain.")
    now, today = data["current"], _day(data, 0)
    return (f"Right now in {place.name} it's {now['temperature_2m']:.0f} degrees with {describe(now['weather_code'])}. "
            f"It feels like {now['apparent_temperature']:.0f}, humidity is {now['relative_humidity_2m']:.0f} percent "
            f"and wind is {now['wind_speed_10m']:.0f} kilometres an hour. "
            f"Today's high is {today['max']:.0f} and low {today['min']:.0f}, with a {today['rain']:.0f} percent chance of rain.")
