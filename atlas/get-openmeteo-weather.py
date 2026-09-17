#!/usr/bin/env python3

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# DEF-039: Open-Meteo's geocoder indexes place names worldwide with no
# locale awareness -- "Hawaii" (a US state, no exact single-point entry
# in a place database) top-ranked a small town in Guatemala over any
# actual Hawaii location, and "Santa Cruz" top-ranked Santa Cruz de
# Tenerife, Spain over Santa Cruz, California, even though the latter
# IS present a few positions down. This homelab and its owner are US-
# based (see ATLAS_DEFAULT_WEATHER_PLACE's own "San Francisco" default),
# so prefer a US match when one exists among the top candidates instead
# of blindly trusting the API's own top-1 ranking.
WEATHER_COUNTRY_BIAS = os.environ.get("ATLAS_WEATHER_COUNTRY_BIAS", "US")

# DEF-039: a bare US state name has no single-point entry in a place-name
# database at all ("Hawaii" has no exact match in the US among Open-Meteo's
# indexed places, only approximate ones like "Hawaiian Gardens, CA" -- a
# real place, but nowhere near Hawaii). The country bias above fixes an
# ambiguous CITY name colliding with a foreign place (Santa Cruz); it
# cannot fix a state name having no city-database entry of its own. Map a
# bare full state name to its largest city before geocoding runs at all.
US_STATE_REPRESENTATIVE_CITY = {
    "alabama": "Birmingham, Alabama", "alaska": "Anchorage, Alaska",
    "arizona": "Phoenix, Arizona", "arkansas": "Little Rock, Arkansas",
    "california": "Los Angeles, California", "colorado": "Denver, Colorado",
    "connecticut": "Hartford, Connecticut", "delaware": "Wilmington, Delaware",
    "florida": "Miami, Florida", "georgia": "Atlanta, Georgia",
    "hawaii": "Honolulu, Hawaii", "idaho": "Boise, Idaho",
    "illinois": "Chicago, Illinois", "indiana": "Indianapolis, Indiana",
    "iowa": "Des Moines, Iowa", "kansas": "Wichita, Kansas",
    "kentucky": "Louisville, Kentucky", "louisiana": "New Orleans, Louisiana",
    "maine": "Portland, Maine", "maryland": "Baltimore, Maryland",
    "massachusetts": "Boston, Massachusetts", "michigan": "Detroit, Michigan",
    "minnesota": "Minneapolis, Minnesota", "mississippi": "Jackson, Mississippi",
    "missouri": "Kansas City, Missouri", "montana": "Billings, Montana",
    "nebraska": "Omaha, Nebraska", "nevada": "Las Vegas, Nevada",
    "new hampshire": "Manchester, New Hampshire", "new jersey": "Newark, New Jersey",
    "new mexico": "Albuquerque, New Mexico", "new york": "New York City, New York",
    "north carolina": "Charlotte, North Carolina", "north dakota": "Fargo, North Dakota",
    "ohio": "Columbus, Ohio", "oklahoma": "Oklahoma City, Oklahoma",
    "oregon": "Portland, Oregon", "pennsylvania": "Philadelphia, Pennsylvania",
    "rhode island": "Providence, Rhode Island", "south carolina": "Charleston, South Carolina",
    "south dakota": "Sioux Falls, South Dakota", "tennessee": "Nashville, Tennessee",
    "texas": "Houston, Texas", "utah": "Salt Lake City, Utah",
    "vermont": "Burlington, Vermont", "virginia": "Virginia Beach, Virginia",
    "washington": "Seattle, Washington", "west virginia": "Charleston, West Virginia",
    "wisconsin": "Milwaukee, Wisconsin", "wyoming": "Cheyenne, Wyoming",
    "district of columbia": "Washington, DC",
}

WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def fetch_json(url: str, params: dict) -> dict:
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "homelab-ai-weather/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def select_geocode_result(results: list[dict], country_bias: str) -> dict:
    """Pick the best geocoding candidate: a country_bias match if one
    exists among the returned results, else the API's own top choice.
    Pure/network-free so this selection logic can be unit-tested directly.
    """
    if not results:
        raise ValueError("no geocoding results to select from")
    if country_bias:
        for result in results:
            if result.get("country_code") == country_bias:
                return result
    return results[0]


def geocode(place: str) -> dict:
    place = US_STATE_REPRESENTATIVE_CITY.get(place.strip().lower(), place)
    payload = fetch_json(
        GEOCODE_URL,
        {
            "name": place,
            "count": 10,
            "language": "en",
            "format": "json",
        },
    )
    results = payload.get("results") or []
    if not results:
        raise SystemExit(f"No geocoding result found for: {place}")
    return select_geocode_result(results, WEATHER_COUNTRY_BIAS)


def get_weather(lat: float, lon: float, timezone: str = "auto") -> dict:
    return fetch_json(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "weather_code",
                    "cloud_cover",
                    "wind_speed_10m",
                    "wind_direction_10m",
                    "is_day",
                ]
            ),
            "timezone": timezone,
            "forecast_days": 1,
        },
    )


# H2: a supported explicit period/date needs the daily forecast endpoint, not
# the current-conditions one -- 16 days is Open-Meteo's own free-tier daily
# forecast ceiling, so anything beyond it is genuinely out of range, not an
# arbitrary local limit.
MAX_FORECAST_DAYS = 16


def get_daily_weather(lat: float, lon: float, timezone: str = "auto") -> dict:
    return fetch_json(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join([
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "precipitation_probability_max",
            ]),
            "timezone": timezone,
            "forecast_days": MAX_FORECAST_DAYS,
        },
    )


def build_current_summary(place: str, location: dict, weather: dict) -> dict:
    current = weather.get("current", {})
    return {
        "result_status": "ok",
        "error_type": None,
        "requested_period": "current",
        "resolved_date": (current.get("time") or "")[:10] or None,
        "query": place,
        "resolved_name": location.get("name"),
        "admin1": location.get("admin1"),
        "country": location.get("country"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "timezone": weather.get("timezone"),
        "time": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "relative_humidity_pct": current.get("relative_humidity_2m"),
        "precipitation_mm": current.get("precipitation"),
        "cloud_cover_pct": current.get("cloud_cover"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "wind_direction_deg": current.get("wind_direction_10m"),
        "weather_code": current.get("weather_code"),
        "weather_text": WEATHER_CODES.get(current.get("weather_code"), "Unknown"),
        "is_day": current.get("is_day"),
        "source": "Open-Meteo",
    }


def build_daily_summary(place: str, location: dict, period: str, target_date: str, daily_payload: dict) -> dict:
    """H2: use forecast fields, not current-conditions fields, for a future
    day, and fail closed (never "ok") when the requested date falls outside
    Open-Meteo's own daily forecast window rather than silently substituting
    today's conditions.
    """
    daily = daily_payload.get("daily") or {}
    times = daily.get("time") or []
    if target_date not in times:
        return {
            "result_status": "invalid",
            "error_type": "unsupported_period",
            "message": (
                f"I don't have a forecast for {target_date}; the daily forecast only "
                f"covers today through the next {MAX_FORECAST_DAYS - 1} days."
            ),
            "requested_period": period,
            "resolved_date": target_date,
            "query": place,
            "resolved_name": location.get("name"),
            "country": location.get("country"),
            "source": "Open-Meteo",
        }
    index = times.index(target_date)
    weather_code = (daily.get("weather_code") or [None])[index]
    return {
        "result_status": "ok",
        "error_type": None,
        "requested_period": period,
        "resolved_date": target_date,
        "query": place,
        "resolved_name": location.get("name"),
        "admin1": location.get("admin1"),
        "country": location.get("country"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "timezone": daily_payload.get("timezone"),
        "weather_code": weather_code,
        "weather_text": WEATHER_CODES.get(weather_code, "Unknown"),
        "temperature_max_c": (daily.get("temperature_2m_max") or [None])[index],
        "temperature_min_c": (daily.get("temperature_2m_min") or [None])[index],
        "precipitation_sum_mm": (daily.get("precipitation_sum") or [None])[index],
        "precipitation_probability_max_pct": (daily.get("precipitation_probability_max") or [None])[index],
        "source": "Open-Meteo",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("place", nargs="+", help="City or place name, for example: Shanghai")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    parser.add_argument(
        "--period", choices=["current", "today", "tonight", "tomorrow", "date"], default="current",
        help="H2: 'current' (default) is instantaneous conditions; the others report "
             "that day's forecast and require --date.",
    )
    parser.add_argument("--date", help="ISO date (YYYY-MM-DD), required when --period is not 'current'")
    args = parser.parse_args()

    if args.period != "current" and not args.date:
        json.dump(
            {
                "result_status": "invalid",
                "error_type": "parse_failed",
                "message": "--date is required when --period is not 'current'.",
            },
            sys.stdout, indent=2,
        )
        sys.stdout.write("\n")
        return

    place = " ".join(args.place)
    location = geocode(place)
    if args.period == "current":
        weather = get_weather(location["latitude"], location["longitude"])
        summary = build_current_summary(place, location, weather)
    else:
        daily_payload = get_daily_weather(location["latitude"], location["longitude"])
        summary = build_daily_summary(place, location, args.period, args.date, daily_payload)

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    if summary["result_status"] != "ok":
        print(summary.get("message", "Forecast unavailable."))
        return

    print(f"Location: {summary['resolved_name']}, {summary.get('admin1')}, {summary['country']}")
    if args.period == "current":
        print(f"Observed/forecast time: {summary['time']} ({summary['timezone']})")
        print(
            f"Conditions: {summary['weather_text']} | "
            f"{summary['temperature_c']} C feels like {summary['apparent_temperature_c']} C"
        )
        print(
            f"Humidity: {summary['relative_humidity_pct']}% | "
            f"Cloud cover: {summary['cloud_cover_pct']}% | "
            f"Precipitation: {summary['precipitation_mm']} mm"
        )
        print(f"Wind: {summary['wind_speed_kmh']} km/h at {summary['wind_direction_deg']} deg")
    else:
        print(f"Forecast for {summary['resolved_date']} ({args.period}): {summary['weather_text']}")
        print(f"High {summary['temperature_max_c']} C / Low {summary['temperature_min_c']} C")
        print(
            f"Precipitation sum: {summary['precipitation_sum_mm']} mm | "
            f"Chance: {summary['precipitation_probability_max_pct']}%"
        )
    print("Source: Open-Meteo")


if __name__ == "__main__":
    main()
