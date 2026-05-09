"""Parsers that turn MET Norway responses into the agent-friendly shape.

These are pure functions over plain dicts — easy to test without httpx.
"""
from datetime import datetime, timezone
from collections import defaultdict


def _wind_dir_compass(deg: float | None) -> str:
    if deg is None:
        return ""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[round(deg / 22.5) % 16]


def _instant(details: dict) -> dict:
    return {
        "temp": details.get("air_temperature"),
        "wind_speed": details.get("wind_speed"),
        "wind_dir_deg": details.get("wind_from_direction"),
        "wind_dir": _wind_dir_compass(details.get("wind_from_direction")),
        "humidity": details.get("relative_humidity"),
        "pressure": details.get("air_pressure_at_sea_level"),
    }


def parse_forecast(data: dict, hourly_limit: int = 48) -> dict:
    """Turn a MET locationforecast/2.0/compact response into Aurora's shape.

    Returns: location, current, hourly[], daily_summary[].
    """
    props = data.get("properties", {})
    geom = data.get("geometry", {})
    coords = geom.get("coordinates", [None, None])
    location = {
        "lat": coords[1] if len(coords) > 1 else None,
        "lon": coords[0] if len(coords) > 0 else None,
        "altitude_m": coords[2] if len(coords) > 2 else None,
    }

    series = props.get("timeseries", [])
    if not series:
        return {"location": location, "current": None, "hourly": [], "daily_summary": []}

    # Current = first point's instant data
    first = series[0]
    first_data = first.get("data", {})
    first_instant = first_data.get("instant", {}).get("details", {})
    next_1h = first_data.get("next_1_hours", {})
    next_1h_summary = next_1h.get("summary", {})
    current = _instant(first_instant)
    current["symbol"] = next_1h_summary.get("symbol_code", "")
    current["time"] = first.get("time", "")

    # Hourly — up to hourly_limit points
    hourly: list[dict] = []
    for point in series[:hourly_limit]:
        pdata = point.get("data", {})
        instant = pdata.get("instant", {}).get("details", {})
        n1 = pdata.get("next_1_hours", {})
        n1_summary = n1.get("summary", {})
        n1_details = n1.get("details", {})
        hourly.append({
            "time": point.get("time", ""),
            "temp": instant.get("air_temperature"),
            "precip_mm": n1_details.get("precipitation_amount", 0.0),
            "wind_speed": instant.get("wind_speed"),
            "wind_dir": _wind_dir_compass(instant.get("wind_from_direction")),
            "symbol": n1_summary.get("symbol_code", ""),
        })

    # Daily summary — aggregate by date
    by_day: dict[str, list[dict]] = defaultdict(list)
    by_day_symbols: dict[str, list[str]] = defaultdict(list)
    for point in series:
        try:
            ts = datetime.fromisoformat(point["time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        date_str = ts.astimezone(timezone.utc).date().isoformat()
        pdata = point.get("data", {})
        instant = pdata.get("instant", {}).get("details", {})
        n6 = pdata.get("next_6_hours", {})
        n6_details = n6.get("details", {})
        n6_summary = n6.get("summary", {})
        by_day[date_str].append({
            "temp": instant.get("air_temperature"),
            "precip_mm": n6_details.get("precipitation_amount"),
        })
        sym = n6_summary.get("symbol_code")
        if sym:
            by_day_symbols[date_str].append(sym)

    daily_summary = []
    for date_str in sorted(by_day.keys())[:10]:
        rows = by_day[date_str]
        temps = [r["temp"] for r in rows if r["temp"] is not None]
        precips = [r["precip_mm"] for r in rows if r["precip_mm"] is not None]
        symbols = by_day_symbols.get(date_str, [])
        daily_summary.append({
            "date": date_str,
            "high": round(max(temps), 1) if temps else None,
            "low": round(min(temps), 1) if temps else None,
            "precip_total": round(sum(precips), 1) if precips else 0.0,
            "dominant_symbol": _most_common(symbols) if symbols else "",
        })

    return {"location": location, "current": current, "hourly": hourly, "daily_summary": daily_summary}


def parse_marine(data: dict, hourly_limit: int = 24) -> dict:
    """Turn a MET oceanforecast/2.0/complete response into Aurora's shape."""
    props = data.get("properties", {})
    geom = data.get("geometry", {})
    coords = geom.get("coordinates", [None, None])
    location = {
        "lat": coords[1] if len(coords) > 1 else None,
        "lon": coords[0] if len(coords) > 0 else None,
    }

    series = props.get("timeseries", [])
    if not series:
        return {"location": location, "current": None, "hourly": []}

    first = series[0]
    first_instant = first.get("data", {}).get("instant", {}).get("details", {})
    current = {
        "wave_height_m": first_instant.get("sea_surface_wave_height"),
        "wave_period_s": first_instant.get("sea_surface_wave_period_at_variance_spectral_density_maximum"),
        "wave_direction_deg": first_instant.get("sea_surface_wave_from_direction"),
        "wave_direction": _wind_dir_compass(first_instant.get("sea_surface_wave_from_direction")),
        "water_temp": first_instant.get("sea_water_temperature"),
        "current_speed_ms": first_instant.get("sea_water_speed"),
        "current_direction_deg": first_instant.get("sea_water_to_direction"),
        "time": first.get("time", ""),
    }

    hourly: list[dict] = []
    for point in series[:hourly_limit]:
        det = point.get("data", {}).get("instant", {}).get("details", {})
        hourly.append({
            "time": point.get("time", ""),
            "wave_height_m": det.get("sea_surface_wave_height"),
            "wave_direction": _wind_dir_compass(det.get("sea_surface_wave_from_direction")),
            "water_temp": det.get("sea_water_temperature"),
            "current_speed_ms": det.get("sea_water_speed"),
        })

    return {"location": location, "current": current, "hourly": hourly}


def parse_alerts(data: dict, county: str | None = None) -> dict:
    """Turn the MetAlerts CAP-JSON feed into Aurora's flat shape."""
    features = data.get("features", []) if isinstance(data, dict) else []
    out: list[dict] = []
    for feat in features:
        props = feat.get("properties", {})
        # Filter by county if requested. MetAlerts uses geographicDomain or
        # area names; this is a coarse text match.
        if county:
            counties = props.get("county") or props.get("area") or ""
            if isinstance(counties, list):
                joined = " ".join(str(c) for c in counties).lower()
            else:
                joined = str(counties).lower()
            if county.lower() not in joined:
                continue
        out.append({
            "event_type": props.get("eventAwarenessName") or props.get("event") or "",
            "severity": props.get("severity") or props.get("awarenessLevel") or "",
            "area": props.get("area") or props.get("county") or "",
            "headline": props.get("title") or props.get("headline") or "",
            "description": props.get("description") or "",
            "valid_from": props.get("eventEndingTime") and props.get("eventStartingTime") or props.get("onset", ""),
            "valid_to": props.get("expires") or props.get("eventEndingTime") or "",
        })
    return {"active_alerts": out, "count": len(out)}


def parse_sunrise(data: dict) -> dict:
    """Pull the day's sunrise/sunset from MET sunrise/3.0/sun."""
    props = data.get("properties", {})
    sunrise = props.get("sunrise") or {}
    sunset = props.get("sunset") or {}
    return {
        "sunrise": sunrise.get("time", ""),
        "sunset": sunset.get("time", ""),
    }


def _most_common(xs: list[str]) -> str:
    if not xs:
        return ""
    counts: dict[str, int] = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


# Norway/Scandinavia coordinate bounds (generous — includes Svalbard, Jan Mayen,
# coastal Sweden/Denmark/Finland for offshore use).
NORWAY_LAT_MIN = 55.0
NORWAY_LAT_MAX = 81.0
NORWAY_LON_MIN = -10.0
NORWAY_LON_MAX = 35.0


def in_norway(lat: float, lon: float) -> bool:
    return NORWAY_LAT_MIN <= lat <= NORWAY_LAT_MAX and NORWAY_LON_MIN <= lon <= NORWAY_LON_MAX
