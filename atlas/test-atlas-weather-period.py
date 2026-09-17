#!/usr/bin/env python3
"""H2 tests: weather period/place separation must never silently return
current conditions for a future-day request, and must fail closed on an
unsupported or out-of-range date rather than substituting today's data.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "tools" / "ai"
sys.path.insert(0, str(AI_DIR))

SPEC = importlib.util.spec_from_file_location("atlas_runner_weather_period", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)

WEATHER_SPEC = importlib.util.spec_from_file_location(
    "atlas_openmeteo_weather", AI_DIR / "get-openmeteo-weather.py"
)
weather_tool = importlib.util.module_from_spec(WEATHER_SPEC)
assert WEATHER_SPEC and WEATHER_SPEC.loader
WEATHER_SPEC.loader.exec_module(weather_tool)


def main() -> None:
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone.utc)

    # -- infer_weather_period(): each named period resolves to the right
    # calendar date, and current-vs-tomorrow are mechanically different --
    assert runner.infer_weather_period("What is the weather right now?", now) == ("current", None)
    assert runner.infer_weather_period("What is the weather today?", now) == ("today", dt.date(2026, 9, 11))
    assert runner.infer_weather_period("Do I need a jacket tonight?", now) == ("tonight", dt.date(2026, 9, 11))
    tomorrow_period, tomorrow_date = runner.infer_weather_period("What is the weather tomorrow?", now)
    assert tomorrow_period == "tomorrow"
    assert tomorrow_date == dt.date(2026, 9, 12)
    assert tomorrow_date != now.date(), "current and tomorrow must resolve to different dates"
    date_period, date_value = runner.infer_weather_period("What is the weather on October 5th?", now)
    assert date_period == "date" and date_value == dt.date(2026, 10, 5)
    unparsable_period, unparsable_date = runner.infer_weather_period("What is the weather on Blorpday 5th?", now)
    assert unparsable_period == "date" and unparsable_date is None

    # -- run_weather(): a "date" period that failed to parse fails closed
    # before ever calling the adapter, rather than falling back to "current" --
    with mock.patch.object(runner, "run_json_tool") as json_tool_mock:
        result = runner.run_weather("What is the weather on Blorpday 5th in Chicago?")
    json_tool_mock.assert_not_called()
    assert result["result_status"] == "invalid" and result["error_type"] == "parse_failed"

    # -- run_weather(): "tomorrow" calls the adapter with an explicit
    # --period/--date pair, not just the bare place -- expected date is
    # computed the same way run_weather() itself computes it (real wall
    # clock), not hardcoded, since run_weather() doesn't accept an injected
    # "now" and this test shouldn't mutate the global datetime module to
    # fake one.
    expected_tomorrow = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat()
    with mock.patch.object(
        runner, "run_json_tool",
        return_value={
            "result_status": "ok", "error_type": None, "requested_period": "tomorrow",
            "resolved_date": expected_tomorrow, "resolved_name": "Chicago", "country": "United States",
            "weather_text": "Overcast", "temperature_max_c": 22.5, "temperature_min_c": 16.4,
            "source": "Open-Meteo",
        },
    ) as json_tool_mock:
        result = runner.run_weather("What is the weather in Chicago tomorrow?")
    command = json_tool_mock.call_args[0][0]
    assert "--period" in command and command[command.index("--period") + 1] == "tomorrow"
    assert "--date" in command and command[command.index("--date") + 1] == expected_tomorrow
    assert result["result_status"] == "ok"
    assert "Tomorrow" in result["answer"] and "22.5" in result["answer"] and "16.4" in result["answer"]

    # -- run_weather(): "current" (the default, no period word) does not
    # pass --period/--date at all -- byte-identical to the pre-H2 call shape --
    with mock.patch.object(
        runner, "run_json_tool",
        return_value={
            "result_status": "ok", "error_type": None, "resolved_name": "Chicago",
            "country": "United States", "weather_text": "Clear sky",
            "temperature_c": 22.1, "apparent_temperature_c": 22.9, "source": "Open-Meteo",
        },
    ) as json_tool_mock:
        result = runner.run_weather("What is the weather in Chicago?")
    command = json_tool_mock.call_args[0][0]
    assert "--period" not in command and "--date" not in command
    assert result["result_status"] == "ok" and "Chicago" in result["answer"]

    # -- run_weather(): the adapter itself reporting an out-of-range date is
    # surfaced honestly, never silently treated as ok --
    with mock.patch.object(
        runner, "run_json_tool",
        return_value={
            "result_status": "invalid", "error_type": "unsupported_period",
            "message": "I don't have a forecast for 2026-10-11; the daily forecast only covers "
                       "today through the next 15 days.",
            "resolved_name": "Chicago", "country": "United States", "source": "Open-Meteo",
        },
    ):
        result = runner.run_weather("What is the weather in Chicago on October 11th?")
    assert result["result_status"] == "invalid" and result["error_type"] == "unsupported_period"
    assert "forecast" in result["answer"].lower()

    # -- get-openmeteo-weather.py's own daily-summary builder: correct index
    # selection, and fail-closed (never "ok") when the date isn't present --
    location = {"name": "Chicago", "admin1": "Illinois", "country": "United States",
                "latitude": 41.85, "longitude": -87.65}
    daily_payload = {
        "timezone": "America/Chicago",
        "daily": {
            "time": ["2026-09-11", "2026-09-12", "2026-09-13"],
            "weather_code": [1, 3, 61],
            "temperature_2m_max": [25.4, 31.4, 20.0],
            "temperature_2m_min": [15.7, 18.3, 12.0],
            "precipitation_sum": [0.0, 0.0, 4.2],
            "precipitation_probability_max": [1, 3, 70],
        },
    }
    today_summary = weather_tool.build_daily_summary("Chicago", location, "today", "2026-09-11", daily_payload)
    assert today_summary["result_status"] == "ok"
    assert today_summary["weather_text"] == "Mainly clear" and today_summary["temperature_max_c"] == 25.4
    tomorrow_summary = weather_tool.build_daily_summary("Chicago", location, "tomorrow", "2026-09-12", daily_payload)
    assert tomorrow_summary["weather_text"] == "Overcast" and tomorrow_summary["temperature_max_c"] == 31.4
    assert today_summary["weather_text"] != tomorrow_summary["weather_text"], (
        "current-vs-tomorrow must be mechanically distinguishable, not coincidentally different"
    )
    out_of_range_summary = weather_tool.build_daily_summary("Chicago", location, "date", "2026-10-11", daily_payload)
    assert out_of_range_summary["result_status"] == "invalid"
    assert out_of_range_summary["error_type"] == "unsupported_period"
    assert "message" in out_of_range_summary

    # -- DEF-039: select_geocode_result() prefers a country-bias match over
    # the API's own top-ranked result, confirmed against the exact shape
    # Open-Meteo actually returns (Santa Cruz de Tenerife ranked first,
    # Santa Cruz California several positions down). Pure/network-free. --
    santa_cruz_results = [
        {"name": "Santa Cruz de Tenerife", "country_code": "ES", "country": "Spain"},
        {"name": "Santa Cruz do Sul", "country_code": "BR", "country": "Brazil"},
        {"name": "Santa Cruz", "country_code": "PH", "country": "Philippines"},
        {"name": "Santa Cruz", "country_code": "US", "country": "United States", "admin1": "California"},
    ]
    picked = weather_tool.select_geocode_result(santa_cruz_results, "US")
    assert picked["country_code"] == "US" and picked.get("admin1") == "California", picked

    # No country_bias match among results -- falls back to the API's own
    # top choice rather than raising or returning nothing.
    no_us_results = [{"name": "Somewhere", "country_code": "FR", "country": "France"}]
    assert weather_tool.select_geocode_result(no_us_results, "US") == no_us_results[0]

    # Empty country_bias (opted out) always takes the top result.
    assert weather_tool.select_geocode_result(santa_cruz_results, "") == santa_cruz_results[0]

    # -- DEF-039: a bare US state name maps to its largest city before
    # geocoding ever runs -- covers the classic "Georgia" (state vs.
    # country) and "Hawaii" (no exact single-point match in the US at all)
    # collisions. Case-insensitive; a real city name must pass through
    # unchanged. --
    assert weather_tool.US_STATE_REPRESENTATIVE_CITY["hawaii"] == "Honolulu, Hawaii"
    assert weather_tool.US_STATE_REPRESENTATIVE_CITY["georgia"] == "Atlanta, Georgia"
    assert "chicago" not in weather_tool.US_STATE_REPRESENTATIVE_CITY

    print("PASS: H2 weather period/place separation, live-adapter fail-closed behavior, and unit coverage")


if __name__ == "__main__":
    main()
