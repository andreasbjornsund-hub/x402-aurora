"""End-to-end-ish tests for the public HTTP handlers (with MET stubbed)."""
import pytest
from fastapi import HTTPException


def _forecast_payload():
    return {
        "geometry": {"coordinates": [10.75, 59.91, 50]},
        "properties": {"timeseries": [
            {
                "time": "2026-05-09T08:00:00Z",
                "data": {
                    "instant": {"details": {"air_temperature": 12.3, "wind_speed": 5.2, "wind_from_direction": 225}},
                    "next_1_hours": {"summary": {"symbol_code": "cloudy"}, "details": {"precipitation_amount": 0.0}},
                    "next_6_hours": {"summary": {"symbol_code": "lightrain"}, "details": {"precipitation_amount": 0.5}},
                },
            },
        ]},
    }


def _marine_payload():
    return {
        "geometry": {"coordinates": [4.0, 60.0]},
        "properties": {"timeseries": [
            {"time": "2026-05-09T08:00:00Z", "data": {"instant": {"details": {
                "sea_surface_wave_height": 1.4,
                "sea_surface_wave_from_direction": 200,
                "sea_water_temperature": 8.6,
            }}}},
        ]},
    }


def _sunrise_payload():
    return {"properties": {
        "sunrise": {"time": "2026-05-09T04:32:00+02:00"},
        "sunset": {"time": "2026-05-09T21:38:00+02:00"},
    }}


# ── Free endpoints ──────────────────────────────────────────────────


async def test_health(main_module):
    r = await main_module.health()
    assert r["status"] == "ok"
    assert r["service"] == "aurora"


async def test_api_status_includes_cache(main_module):
    r = await main_module.api_status()
    assert r["service"] == "aurora"
    assert r["upstream"] == "met.no"
    for k in ("entries", "fresh", "max"):
        assert k in r["cache"]


async def test_cities_endpoint(main_module):
    r = await main_module.list_cities()
    assert r["count"] > 30
    assert any(c["name"] == "oslo" for c in r["cities"])


# ── Manifest contract ───────────────────────────────────────────────


async def test_x402_manifest_paid_endpoints(main_module):
    import os
    r = await main_module.x402_manifest()
    assert r["x402Version"] == 2
    assert r["payment"]["payTo"] == os.environ["EVM_ADDRESS"]
    paid = [e for e in r["endpoints"] if e["accepts"]]
    paths_paid = {e["path"] for e in paid}
    assert paths_paid == {"/forecast", "/forecast/city", "/marine", "/alerts"}


async def test_atomic_amounts_match_price_usd(main_module):
    for e in main_module.ENDPOINT_CATALOG:
        if e["price_usd"] is None:
            continue
        usd = float(e["price_usd"].replace("$", ""))
        expected = str(int(round(usd * 10**6)))
        assert e["amount_atomic"] == expected, (
            f"{e['path']}: ${usd} → {expected} expected, got {e['amount_atomic']}"
        )


async def test_llms_txt_lists_all_endpoints(main_module):
    r = await main_module.llms_txt()
    body = r.body.decode()
    for e in main_module.ENDPOINT_CATALOG:
        assert e["path"] in body


# ── Paid handlers (MET stubbed) ─────────────────────────────────────


async def test_forecast_happy_path(main_module, fake_met):
    fake_met.stub("locationforecast", 200, _forecast_payload())
    fake_met.stub("sunrise", 200, _sunrise_payload())
    from fastapi import Response
    resp = Response()
    out = await main_module.forecast(response=resp, lat=59.91, lon=10.75)
    assert out["current"]["temp"] == 12.3
    assert out["sun"]["sunrise"].startswith("2026-05-09")
    assert resp.headers["X-Cache"] == "MISS"


async def test_forecast_cache_hit_on_second_call(main_module, fake_met):
    fake_met.stub("locationforecast", 200, _forecast_payload())
    fake_met.stub("sunrise", 200, _sunrise_payload())
    from fastapi import Response
    r1 = Response()
    await main_module.forecast(response=r1, lat=59.91, lon=10.75)
    r2 = Response()
    await main_module.forecast(response=r2, lat=59.91, lon=10.75)
    assert r2.headers["X-Cache"] == "HIT"


async def test_forecast_rejects_out_of_bounds(main_module):
    from fastapi import Response
    with pytest.raises(HTTPException) as exc:
        await main_module.forecast(response=Response(), lat=48.85, lon=2.35)  # Paris
    assert exc.value.status_code == 400


async def test_forecast_503_on_met_error(main_module, fake_met):
    fake_met.stub("locationforecast", 502)
    from fastapi import Response
    with pytest.raises(HTTPException) as exc:
        await main_module.forecast(response=Response(), lat=59.91, lon=10.75)
    assert exc.value.status_code == 503


async def test_forecast_city_resolves_name(main_module, fake_met):
    fake_met.stub("locationforecast", 200, _forecast_payload())
    fake_met.stub("sunrise", 200, _sunrise_payload())
    from fastapi import Response
    out = await main_module.forecast_by_city(response=Response(), name="OSLO")
    assert out["city"] == "oslo"
    assert out["current"]["temp"] == 12.3


async def test_forecast_city_404_for_unknown(main_module):
    from fastapi import Response
    with pytest.raises(HTTPException) as exc:
        await main_module.forecast_by_city(response=Response(), name="paris")
    assert exc.value.status_code == 404


async def test_marine_happy_path(main_module, fake_met):
    fake_met.stub("oceanforecast", 200, _marine_payload())
    from fastapi import Response
    out = await main_module.marine(response=Response(), lat=60.0, lon=4.0)
    assert out["current"]["wave_height_m"] == 1.4


async def test_marine_404_inland(main_module, fake_met):
    fake_met.stub("oceanforecast", 404)
    from fastapi import Response
    with pytest.raises(HTTPException) as exc:
        await main_module.marine(response=Response(), lat=60.79, lon=10.69)  # Hamar (inland)
    assert exc.value.status_code == 404


async def test_alerts_happy_path(main_module, fake_met):
    fake_met.stub("metalerts", 200, {"features": [
        {"properties": {"area": "Rogaland", "event": "Wind", "severity": "Yellow"}},
    ]})
    from fastapi import Response
    out = await main_module.alerts(response=Response(), county=None)
    assert out["count"] == 1


async def test_alerts_county_filter(main_module, fake_met):
    fake_met.stub("metalerts", 200, {"features": [
        {"properties": {"area": "Rogaland", "event": "Wind"}},
        {"properties": {"area": "Oslo", "event": "Snow"}},
    ]})
    from fastapi import Response
    out = await main_module.alerts(response=Response(), county="oslo")
    assert out["count"] == 1
