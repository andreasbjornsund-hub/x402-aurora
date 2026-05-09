"""Tests for the MET response parsers."""


def _forecast_payload():
    return {
        "geometry": {"type": "Point", "coordinates": [10.75, 59.91, 50]},
        "properties": {
            "timeseries": [
                {
                    "time": "2026-05-09T08:00:00Z",
                    "data": {
                        "instant": {"details": {
                            "air_temperature": 12.3,
                            "wind_speed": 5.2,
                            "wind_from_direction": 225,
                            "relative_humidity": 65,
                            "air_pressure_at_sea_level": 1013,
                        }},
                        "next_1_hours": {
                            "summary": {"symbol_code": "cloudy"},
                            "details": {"precipitation_amount": 0.0},
                        },
                        "next_6_hours": {
                            "summary": {"symbol_code": "lightrain"},
                            "details": {"precipitation_amount": 0.5},
                        },
                    },
                },
                {
                    "time": "2026-05-09T14:00:00Z",
                    "data": {
                        "instant": {"details": {
                            "air_temperature": 15.2,
                            "wind_speed": 4.0,
                            "wind_from_direction": 180,
                        }},
                        "next_1_hours": {
                            "summary": {"symbol_code": "lightrain"},
                            "details": {"precipitation_amount": 0.3},
                        },
                        "next_6_hours": {
                            "summary": {"symbol_code": "lightrain"},
                            "details": {"precipitation_amount": 1.8},
                        },
                    },
                },
            ]
        },
    }


def test_parse_forecast_basic_shape(parsers_module):
    parsed = parsers_module.parse_forecast(_forecast_payload())
    assert parsed["location"]["lat"] == 59.91
    assert parsed["location"]["lon"] == 10.75
    assert parsed["current"]["temp"] == 12.3
    assert parsed["current"]["wind_dir"] == "SW"
    assert parsed["current"]["symbol"] == "cloudy"
    assert len(parsed["hourly"]) == 2
    assert parsed["hourly"][0]["temp"] == 12.3


def test_parse_forecast_daily_summary(parsers_module):
    parsed = parsers_module.parse_forecast(_forecast_payload())
    days = parsed["daily_summary"]
    assert len(days) == 1
    day = days[0]
    assert day["date"] == "2026-05-09"
    assert day["high"] == 15.2
    assert day["low"] == 12.3
    assert day["dominant_symbol"] == "lightrain"


def test_parse_forecast_empty(parsers_module):
    parsed = parsers_module.parse_forecast({"geometry": {"coordinates": []}, "properties": {}})
    assert parsed["current"] is None
    assert parsed["hourly"] == []
    assert parsed["daily_summary"] == []


def test_wind_dir_compass(parsers_module):
    f = parsers_module._wind_dir_compass
    assert f(0) == "N"
    assert f(90) == "E"
    assert f(180) == "S"
    assert f(270) == "W"
    assert f(45) == "NE"
    assert f(225) == "SW"
    assert f(None) == ""


def test_in_norway(parsers_module):
    assert parsers_module.in_norway(59.91, 10.75)   # Oslo
    assert parsers_module.in_norway(78.22, 15.62)   # Svalbard
    assert not parsers_module.in_norway(48.85, 2.35)  # Paris
    assert not parsers_module.in_norway(0, 0)         # Atlantic null island


def test_parse_marine_basic(parsers_module):
    payload = {
        "geometry": {"coordinates": [4.0, 60.0]},
        "properties": {
            "timeseries": [
                {
                    "time": "2026-05-09T08:00:00Z",
                    "data": {"instant": {"details": {
                        "sea_surface_wave_height": 1.4,
                        "sea_surface_wave_from_direction": 225,
                        "sea_water_temperature": 8.6,
                        "sea_water_speed": 0.21,
                    }}},
                }
            ]
        },
    }
    parsed = parsers_module.parse_marine(payload)
    assert parsed["location"]["lat"] == 60.0
    assert parsed["current"]["wave_height_m"] == 1.4
    assert parsed["current"]["wave_direction"] == "SW"
    assert parsed["current"]["water_temp"] == 8.6
    assert len(parsed["hourly"]) == 1


def test_parse_alerts_passthrough(parsers_module):
    payload = {"features": [
        {"properties": {
            "event": "Strong wind",
            "severity": "Yellow",
            "area": "Rogaland",
            "title": "Kraftige vindkast",
            "description": "Sterk sørvestlig vind",
            "onset": "2026-05-09T09:00:00Z",
            "expires": "2026-05-09T18:00:00Z",
        }},
    ]}
    parsed = parsers_module.parse_alerts(payload)
    assert parsed["count"] == 1
    a = parsed["active_alerts"][0]
    assert a["severity"] == "Yellow"
    assert a["area"] == "Rogaland"


def test_parse_alerts_county_filter(parsers_module):
    payload = {"features": [
        {"properties": {"area": "Rogaland", "event": "Wind"}},
        {"properties": {"area": "Oslo", "event": "Snow"}},
    ]}
    assert parsers_module.parse_alerts(payload, county="rogaland")["count"] == 1
    assert parsers_module.parse_alerts(payload, county="oslo")["count"] == 1
    assert parsers_module.parse_alerts(payload, county="nordland")["count"] == 0
    assert parsers_module.parse_alerts(payload)["count"] == 2


def test_parse_sunrise(parsers_module):
    payload = {"properties": {
        "sunrise": {"time": "2026-05-09T04:32:00+02:00"},
        "sunset": {"time": "2026-05-09T21:38:00+02:00"},
    }}
    parsed = parsers_module.parse_sunrise(payload)
    assert parsed["sunrise"].startswith("2026-05-09T04:32")
    assert parsed["sunset"].startswith("2026-05-09T21:38")
