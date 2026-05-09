"""MET Norway upstream client — fetches forecasts, marine, alerts, sunrise.

MET is free and key-less but requires a User-Agent identifying the app
(per https://api.met.no/doc/TermsOfService). Their soft rate limit is
20 req/sec; we bound ourselves at that with a semaphore.

Caching is keyed by URL+params and TTL'd; transient errors are not cached.
"""
import asyncio
import time
from collections import OrderedDict
from typing import Any

import httpx


USER_AGENT = "x402agent-aurora/1.0 github.com/andreasbjornsund-hub"

LOCATION_FORECAST = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
NOWCAST = "https://api.met.no/weatherapi/nowcast/2.0/complete"
OCEANFORECAST = "https://api.met.no/weatherapi/oceanforecast/2.0/complete"
METALERTS = "https://api.met.no/weatherapi/metalerts/2.0/current.json"
SUNRISE = "https://api.met.no/weatherapi/sunrise/3.0/sun"

# Per MET's request — be polite, don't hammer.
_MET_SEMAPHORE = asyncio.Semaphore(20)

# Bounded LRU+TTL cache. Keys are (path, frozenset of params); values are
# (expires_at, response_json).
_cache: OrderedDict[tuple[str, frozenset], tuple[float, Any]] = OrderedDict()
_CACHE_MAX = 5000


def _cache_get(key: tuple[str, frozenset]) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, data = entry
    if expires_at < time.time():
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return data


def _cache_put(key: tuple[str, frozenset], data: Any, ttl: float) -> None:
    _cache[key] = (time.time() + ttl, data)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def cache_stats() -> dict:
    now = time.time()
    fresh = sum(1 for (exp, _) in _cache.values() if exp >= now)
    return {"entries": len(_cache), "fresh": fresh, "max": _CACHE_MAX}


def reset_cache() -> None:
    _cache.clear()


class MetError(Exception):
    """Raised when MET returns an error or the response is unparseable."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"MET {status_code}: {message}")


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    ttl: float = 1800.0,
) -> tuple[Any, bool]:
    """Fetch a MET endpoint with TTL caching. Returns (data, cache_hit).

    Raises MetError on non-2xx responses. Cache key is (url, params).
    """
    key = (url, frozenset((params or {}).items()))
    cached = _cache_get(key)
    if cached is not None:
        return cached, True

    async with _MET_SEMAPHORE:
        resp = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})

    if resp.status_code != 200:
        raise MetError(resp.status_code, resp.text[:200])
    data = resp.json()
    _cache_put(key, data, ttl)
    return data, False
