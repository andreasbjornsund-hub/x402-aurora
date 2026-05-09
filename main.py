"""
Aurora — Norwegian Weather Intelligence

x402 micropayment API wrapping MET Norway's free weather services into an
agent-friendly shape. Pay per query with USDC on Base.

Endpoints (free):
  GET /                       — landing page (HTML or JSON)
  GET /health                 — health check
  GET /api-status             — uptime + cache shape
  GET /cities                 — list of supported Norwegian city names
  GET /services.json          — agent-readable services manifest
  GET /llms.txt               — LLMs.txt for AI crawlers
  GET /robots.txt             — robots policy
  GET /.well-known/x402.json  — x402 agent-discovery manifest

Endpoints (paid, USDC on Base):
  GET /forecast               — $0.005: 48h forecast for lat/lon (+ sunrise)
  GET /forecast/city          — $0.005: same, by Norwegian city name
  GET /marine                 — $0.01:  marine/ocean forecast for lat/lon
  GET /alerts                 — $0.005: active MET weather alerts (optional county filter)

Data: MET Norway (free, no API key required). Per their ToS we identify
ourselves with a User-Agent and stay under their soft 20 req/sec limit.
"""

import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import cities
import met_client
from met_client import MetError
import parsers

from cdp_auth import create_cdp_auth_provider

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServer

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────

SERVICE_ID = "aurora"
SERVICE_NAME = "Aurora — Norwegian Weather Intelligence"
SERVICE_DESCRIPTION = (
    "Weather forecasts, marine data, and alerts for Norway and Scandinavian waters. "
    "Powered by MET Norway. Pay per query with USDC via x402."
)
SERVICE_CATEGORY = "weather"

EVM_ADDRESS = os.getenv("EVM_ADDRESS")
EVM_NETWORK: Network = "eip155:8453"  # Base mainnet
FACILITATOR_URL = os.getenv("FACILITATOR_URL", "https://x402.org/facilitator")
SITE_URL = os.getenv("SITE_URL", "https://x402-aurora.fly.dev")

USDC_BASE_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Cache TTLs (seconds). MET updates locationforecast hourly; marine & alerts
# evolve more slowly. Sunrise is deterministic per day.
TTL_FORECAST = int(os.getenv("TTL_FORECAST", str(30 * 60)))   # 30 min
TTL_MARINE = int(os.getenv("TTL_MARINE", str(60 * 60)))       # 1 hour
TTL_ALERTS = int(os.getenv("TTL_ALERTS", str(5 * 60)))        # 5 min
TTL_SUNRISE = int(os.getenv("TTL_SUNRISE", str(12 * 60 * 60)))  # 12 h

if not EVM_ADDRESS:
    raise ValueError("Set EVM_ADDRESS in .env")

# ── FastAPI app ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await _http.aclose()


app = FastAPI(
    title=SERVICE_NAME,
    description=SERVICE_DESCRIPTION,
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# ── x402 payment middleware ─────────────────────────────────────────

import json as _json

cdp_auth = None
if "cdp.coinbase.com" in FACILITATOR_URL:
    cdp_auth = create_cdp_auth_provider()
facilitator_config = FacilitatorConfig(url=FACILITATOR_URL, auth_provider=cdp_auth)
facilitator = HTTPFacilitatorClient(facilitator_config)

_CAIP2_TO_V1 = {"eip155:8453": "base", "eip155:84532": "base-sepolia"}


def _v2_payload_to_v1(payload_dict: dict) -> dict:
    v1 = {"x402Version": 1}
    v1["scheme"] = payload_dict.get("scheme", "exact")
    raw_net = payload_dict.get("network", EVM_NETWORK)
    v1["network"] = _CAIP2_TO_V1.get(raw_net, raw_net)
    v1["payload"] = payload_dict.get("payload", payload_dict)
    return v1


def _v2_requirements_to_v1(req_dict: dict) -> dict:
    raw_net = req_dict.get("network", EVM_NETWORK)
    extra = req_dict.get("extra", {})
    if isinstance(extra, str):
        try:
            extra = _json.loads(extra)
        except Exception:
            extra = {}
    v1 = {
        "scheme": req_dict.get("scheme", "exact"),
        "network": _CAIP2_TO_V1.get(raw_net, raw_net),
        "maxAmountRequired": req_dict.get("amount", req_dict.get("maxAmountRequired", "0")),
        "resource": req_dict.get("resource", ""),
        "description": req_dict.get("description", ""),
        "mimeType": req_dict.get("mimeType", req_dict.get("mime_type", "application/json")),
        "asset": req_dict.get("asset", ""),
        "payTo": req_dict.get("payTo", req_dict.get("pay_to", "")),
        "maxTimeoutSeconds": req_dict.get("maxTimeoutSeconds", req_dict.get("max_timeout_seconds", 300)),
        "extra": extra,
    }
    extensions = req_dict.get("extensions", {})
    bazaar = extensions.get("bazaar", {})
    if bazaar.get("info"):
        v1["outputSchema"] = bazaar["info"]
    return v1


_orig_verify = facilitator._verify_http
_orig_settle = facilitator._settle_http


async def _v1_verify(version, payload_dict, requirements_dict):
    v1_payload = _v2_payload_to_v1(payload_dict)
    v1_reqs = _v2_requirements_to_v1(requirements_dict)
    return await _orig_verify(1, v1_payload, v1_reqs)


async def _v1_settle(version, payload_dict, requirements_dict):
    v1_payload = _v2_payload_to_v1(payload_dict)
    v1_reqs = _v2_requirements_to_v1(requirements_dict)
    return await _orig_settle(1, v1_payload, v1_reqs)


facilitator._verify_http = _v1_verify
facilitator._settle_http = _v1_settle

server = x402ResourceServer(facilitator)
server.register(EVM_NETWORK, ExactEvmServerScheme())

# ── Endpoint catalog ────────────────────────────────────────────────

ENDPOINT_CATALOG: list[dict] = [
    {
        "method": "GET",
        "path": "/forecast",
        "route_pattern": "GET /forecast",
        "description": "48-hour weather forecast for any Norwegian/Scandinavian coordinates. Includes current conditions, hourly series, daily highs/lows, and sunrise/sunset.",
        "price_usd": "$0.005",
        "amount_atomic": "5000",
        "query_params": {"lat": 59.9139, "lon": 10.7522},
        "path_params": {},
        "output_example": {
            "location": {"lat": 59.91, "lon": 10.75},
            "current": {"temp": 12.3, "wind_speed": 5.2, "wind_dir": "SW", "symbol": "cloudy"},
            "hourly": [{"time": "2026-05-09T08:00Z", "temp": 12.3, "precip_mm": 0.0, "symbol": "cloudy"}],
            "daily_summary": [{"date": "2026-05-09", "high": 15.2, "low": 8.1, "precip_total": 2.3, "dominant_symbol": "lightrain"}],
            "sun": {"sunrise": "2026-05-09T04:32:00+02:00", "sunset": "2026-05-09T21:38:00+02:00"},
        },
    },
    {
        "method": "GET",
        "path": "/forecast/city",
        "route_pattern": "GET /forecast/city",
        "description": "Same as /forecast but takes a Norwegian city name. ~50 cities pre-loaded with fuzzy matching.",
        "price_usd": "$0.005",
        "amount_atomic": "5000",
        "query_params": {"name": "oslo"},
        "path_params": {},
        "output_example": {
            "city": "oslo",
            "location": {"lat": 59.9139, "lon": 10.7522},
            "current": {"temp": 12.3, "wind_speed": 5.2, "wind_dir": "SW", "symbol": "cloudy"},
            "hourly": [],
            "daily_summary": [],
            "sun": {"sunrise": "2026-05-09T04:32:00+02:00", "sunset": "2026-05-09T21:38:00+02:00"},
        },
    },
    {
        "method": "GET",
        "path": "/marine",
        "route_pattern": "GET /marine",
        "description": "Marine/ocean forecast for any coordinates in Norwegian/Scandinavian waters: wave height, period, direction, water temperature, currents.",
        "price_usd": "$0.01",
        "amount_atomic": "10000",
        "query_params": {"lat": 60.0, "lon": 4.0},
        "path_params": {},
        "output_example": {
            "location": {"lat": 60.0, "lon": 4.0},
            "current": {"wave_height_m": 1.4, "wave_direction": "SW", "water_temp": 8.6, "current_speed_ms": 0.21},
            "hourly": [{"time": "2026-05-09T08:00Z", "wave_height_m": 1.5}],
        },
    },
    {
        "method": "GET",
        "path": "/alerts",
        "route_pattern": "GET /alerts",
        "description": "Active MET weather alerts. Optional ?county= filter (text-match on county/area name).",
        "price_usd": "$0.005",
        "amount_atomic": "5000",
        "query_params": {"county": "rogaland"},
        "path_params": {},
        "output_example": {
            "active_alerts": [
                {"event_type": "Strong wind", "severity": "Yellow", "area": "Rogaland", "headline": "Kraftige vindkast", "valid_from": "2026-05-09T09:00:00Z", "valid_to": "2026-05-09T18:00:00Z"}
            ],
            "count": 1,
        },
    },
    {
        "method": "GET",
        "path": "/cities",
        "route_pattern": None,
        "description": "List of supported Norwegian cities (free).",
        "price_usd": None,
        "amount_atomic": None,
        "query_params": {},
        "path_params": {},
        "output_example": {"count": 50, "cities": [{"name": "oslo", "lat": 59.9139, "lon": 10.7522}]},
    },
    {
        "method": "GET",
        "path": "/health",
        "route_pattern": None,
        "description": "Service health check.",
        "price_usd": None,
        "amount_atomic": None,
        "query_params": {},
        "path_params": {},
        "output_example": {"status": "ok"},
    },
    {
        "method": "GET",
        "path": "/api-status",
        "route_pattern": None,
        "description": "Operational status — uptime and MET cache shape.",
        "price_usd": None,
        "amount_atomic": None,
        "query_params": {},
        "path_params": {},
        "output_example": None,
    },
]


def _bazaar_info(entry: dict) -> dict:
    inp = {"type": "http", "method": entry["method"]}
    if entry["query_params"]:
        inp["queryParams"] = entry["query_params"]
    if entry["path_params"]:
        inp["pathParams"] = entry["path_params"]
    return {
        "info": {
            "input": inp,
            "output": {"type": "json", "example": entry["output_example"]},
        },
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"input": {"type": "object"}, "output": {"type": "object"}},
        },
    }


def _build_paid_routes(catalog: list[dict]) -> dict[str, RouteConfig]:
    return {
        e["route_pattern"]: RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=EVM_ADDRESS,
                    price=e["price_usd"],
                    network=EVM_NETWORK,
                ),
            ],
            mime_type="application/json",
            description=e["description"],
            extensions={"bazaar": _bazaar_info(e)},
        )
        for e in catalog
        if e["route_pattern"] is not None
    }


routes = _build_paid_routes(ENDPOINT_CATALOG)
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

# ── HTTP client (shared, reused across MET calls) ───────────────────

_http = httpx.AsyncClient(timeout=30, headers={"Accept": "application/json"})

# ── Discovery / metadata endpoints ──────────────────────────────────

_PROCESS_START_TS = time.time()


@app.get("/")
async def landing(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and os.path.isfile("static/index.html"):
        return FileResponse("static/index.html")
    return {
        "service": SERVICE_NAME,
        "version": "0.1.0",
        "description": SERVICE_DESCRIPTION,
        "endpoints": {
            e["path"]: f"{e['description']} ({e['price_usd']} USDC)" if e["price_usd"]
            else f"{e['description']} (free)"
            for e in ENDPOINT_CATALOG
        }
        | {"/.well-known/x402.json": "Agent discovery"},
        "payment": "x402 protocol — USDC on Base network",
        "data_source": "MET Norway (https://api.met.no)",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_ID, "timestamp": int(time.time())}


@app.get("/api-status")
async def api_status():
    return {
        "status": "ok",
        "service": SERVICE_ID,
        "version": "0.1.0",
        "uptime_seconds": int(time.time() - _PROCESS_START_TS),
        "upstream": "met.no",
        "cache": met_client.cache_stats(),
    }


@app.get("/cities")
async def list_cities():
    cs = cities.all_cities()
    return {"count": len(cs), "cities": cs}


@app.get("/services.json")
async def services_manifest():
    return {
        "id": SERVICE_ID,
        "name": SERVICE_NAME,
        "description": SERVICE_DESCRIPTION,
        "category": SERVICE_CATEGORY,
        "x402Version": 2,
        "networks": [EVM_NETWORK],
        "website": SITE_URL,
        "endpoints": [
            {
                "method": e["method"],
                "path": e["path"],
                "description": e["description"],
                "price": e["price_usd"] or "$0.00",
                "currency": "USDC",
            }
            for e in ENDPOINT_CATALOG
        ],
    }


@app.get("/.well-known/x402.json")
async def x402_manifest():
    return {
        "x402Version": 2,
        "service": {
            "id": SERVICE_ID,
            "name": SERVICE_NAME,
            "description": SERVICE_DESCRIPTION,
            "category": SERVICE_CATEGORY,
            "website": SITE_URL,
            "documentation": f"{SITE_URL}/llms.txt",
            "servicesManifest": f"{SITE_URL}/services.json",
        },
        "payment": {
            "schemes": ["exact"],
            "networks": [EVM_NETWORK],
            "asset": {
                "symbol": "USDC",
                "decimals": 6,
                "address": USDC_BASE_MAINNET,
                "chain": "Base",
            },
            "payTo": EVM_ADDRESS,
            "facilitator": FACILITATOR_URL,
        },
        "endpoints": [
            {
                "method": e["method"],
                "path": e["path"],
                "description": e["description"],
                "accepts": [
                    {
                        "scheme": "exact",
                        "network": EVM_NETWORK,
                        "asset": "USDC",
                        "amount": e["amount_atomic"],
                        "amountDisplay": e["price_usd"],
                        "payTo": EVM_ADDRESS,
                    }
                ] if e["amount_atomic"] else [],
                "input": {
                    "type": "http",
                    "method": e["method"],
                    **({"queryParams": e["query_params"]} if e["query_params"] else {}),
                    **({"pathParams": e["path_params"]} if e["path_params"] else {}),
                },
                "output": (
                    {"type": "json", "example": e["output_example"]}
                    if e["output_example"] is not None
                    else {"type": "json"}
                ),
            }
            for e in ENDPOINT_CATALOG
        ],
    }


@app.get("/llms.txt")
async def llms_txt():
    lines = [
        f"# {SERVICE_NAME}",
        f"> {SERVICE_DESCRIPTION}",
        "",
        "## Endpoints",
    ]
    for e in ENDPOINT_CATALOG:
        price = f"{e['price_usd']} USDC" if e["price_usd"] else "Free"
        lines.append(f"- {e['method']} {e['path']} — {price} — {e['description']}")
    lines += [
        "",
        "## Payment",
        "- Protocol: x402 (HTTP 402 micropayments)",
        "- Currency: USDC on Base",
        "- No API keys or accounts needed",
        "- Agent discovery: GET /.well-known/x402.json",
        "",
        "## Source data",
        "- MET Norway (https://api.met.no) — free, no API key required",
        "- We respect their soft 20 req/sec limit and identify ourselves with a User-Agent header",
        "- Forecasts cached for 30 min, marine for 1 hr, alerts for 5 min",
        "",
        "## Coverage",
        "- Coordinates: Norway, Svalbard, Jan Mayen, plus Scandinavian waters (lat 55-81, lon -10 to 35)",
        "- Cities: ~50 Norwegian municipalities by population (see GET /cities)",
        "",
        "## Links",
        f"- Website: {SITE_URL}",
        f"- Services manifest: {SITE_URL}/services.json",
        "",
    ]
    return PlainTextResponse("\n".join(lines), media_type="text/plain")


@app.get("/robots.txt")
async def robots_txt():
    return PlainTextResponse(
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "# AI crawlers\n"
        "User-agent: GPTBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: ClaudeBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: PerplexityBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: Google-Extended\n"
        "Allow: /\n",
        media_type="text/plain",
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _validate_coords(lat: float, lon: float) -> None:
    if not parsers.in_norway(lat, lon):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Coordinates ({lat}, {lon}) are outside coverage area "
                f"(lat {parsers.NORWAY_LAT_MIN}-{parsers.NORWAY_LAT_MAX}, "
                f"lon {parsers.NORWAY_LON_MIN}-{parsers.NORWAY_LON_MAX})"
            ),
        )


def _set_cache_header(response: Response, cache_hit: bool) -> None:
    response.headers["X-Cache"] = "HIT" if cache_hit else "MISS"


async def _fetch_sun(lat: float, lon: float) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        data, _ = await met_client.fetch(
            _http,
            met_client.SUNRISE,
            params={"lat": lat, "lon": lon, "date": today},
            ttl=TTL_SUNRISE,
        )
        return parsers.parse_sunrise(data)
    except MetError:
        return {"sunrise": "", "sunset": ""}


# ── Paid endpoints ──────────────────────────────────────────────────


@app.get("/forecast")
async def forecast(
    response: Response,
    lat: float = Query(..., description="Latitude (WGS84)"),
    lon: float = Query(..., description="Longitude (WGS84)"),
):
    _validate_coords(lat, lon)
    try:
        data, hit = await met_client.fetch(
            _http,
            met_client.LOCATION_FORECAST,
            params={"lat": lat, "lon": lon},
            ttl=TTL_FORECAST,
        )
    except MetError as e:
        raise HTTPException(status_code=503, detail=f"MET upstream unavailable: {e.message}")
    parsed = parsers.parse_forecast(data)
    parsed["sun"] = await _fetch_sun(lat, lon)
    _set_cache_header(response, hit)
    return parsed


@app.get("/forecast/city")
async def forecast_by_city(
    response: Response,
    name: str = Query(..., min_length=1, max_length=64, description="Norwegian city name"),
):
    resolved = cities.lookup(name)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=f"City '{name}' not found. See GET /cities for available names.",
        )
    canonical, lat, lon = resolved
    try:
        data, hit = await met_client.fetch(
            _http,
            met_client.LOCATION_FORECAST,
            params={"lat": lat, "lon": lon},
            ttl=TTL_FORECAST,
        )
    except MetError as e:
        raise HTTPException(status_code=503, detail=f"MET upstream unavailable: {e.message}")
    parsed = parsers.parse_forecast(data)
    parsed["city"] = canonical
    parsed["sun"] = await _fetch_sun(lat, lon)
    _set_cache_header(response, hit)
    return parsed


@app.get("/marine")
async def marine(
    response: Response,
    lat: float = Query(..., description="Latitude (WGS84)"),
    lon: float = Query(..., description="Longitude (WGS84)"),
):
    _validate_coords(lat, lon)
    try:
        data, hit = await met_client.fetch(
            _http,
            met_client.OCEANFORECAST,
            params={"lat": lat, "lon": lon},
            ttl=TTL_MARINE,
        )
    except MetError as e:
        if e.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="No marine data for these coordinates (likely inland).",
            )
        raise HTTPException(status_code=503, detail=f"MET upstream unavailable: {e.message}")
    _set_cache_header(response, hit)
    return parsers.parse_marine(data)


@app.get("/alerts")
async def alerts(
    response: Response,
    county: str | None = Query(None, max_length=64, description="Norwegian county name (optional filter)"),
):
    try:
        data, hit = await met_client.fetch(_http, met_client.METALERTS, ttl=TTL_ALERTS)
    except MetError as e:
        raise HTTPException(status_code=503, detail=f"MET upstream unavailable: {e.message}")
    _set_cache_header(response, hit)
    return parsers.parse_alerts(data, county=county)


# ── Static files ────────────────────────────────────────────────────

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
