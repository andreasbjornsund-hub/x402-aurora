"""Norwegian city geocoding — top 50 cities by population.

Coordinates are city-center; resolution is fine for weather (MET grid is
1 km square in mainland Norway).
"""
from difflib import get_close_matches


# Top ~50 Norwegian cities + a few iconic places. Lat/lon WGS84.
CITIES: dict[str, tuple[float, float]] = {
    "oslo": (59.9139, 10.7522),
    "bergen": (60.3913, 5.3221),
    "stavanger": (58.9700, 5.7331),
    "trondheim": (63.4305, 10.3951),
    "drammen": (59.7440, 10.2045),
    "fredrikstad": (59.2181, 10.9298),
    "sandnes": (58.8517, 5.7361),
    "kristiansand": (58.1599, 8.0182),
    "tromso": (69.6492, 18.9553),
    "tromsø": (69.6492, 18.9553),
    "sarpsborg": (59.2839, 11.1098),
    "skien": (59.2096, 9.6088),
    "ålesund": (62.4722, 6.1495),
    "alesund": (62.4722, 6.1495),
    "sandefjord": (59.1310, 10.2167),
    "haugesund": (59.4138, 5.2680),
    "tønsberg": (59.2674, 10.4076),
    "tonsberg": (59.2674, 10.4076),
    "moss": (59.4347, 10.6589),
    "porsgrunn": (59.1404, 9.6561),
    "bodø": (67.2804, 14.4040),
    "bodo": (67.2804, 14.4040),
    "arendal": (58.4612, 8.7723),
    "hamar": (60.7945, 11.0680),
    "ytrebygda": (60.2980, 5.2240),
    "larvik": (59.0531, 10.0276),
    "halden": (59.1265, 11.3877),
    "lillehammer": (61.1153, 10.4663),
    "molde": (62.7375, 7.1591),
    "harstad": (68.7984, 16.5418),
    "askøy": (60.4084, 5.1389),
    "askoy": (60.4084, 5.1389),
    "kongsberg": (59.6692, 9.6498),
    "gjøvik": (60.7957, 10.6916),
    "gjovik": (60.7957, 10.6916),
    "horten": (59.4170, 10.4828),
    "kristiansund": (63.1109, 7.7280),
    "narvik": (68.4385, 17.4272),
    "elverum": (60.8819, 11.5621),
    "kongsvinger": (60.1903, 12.0036),
    "alta": (69.9689, 23.2716),
    "rana": (66.3128, 14.1428),
    "mo i rana": (66.3128, 14.1428),
    "mosjøen": (65.8377, 13.1934),
    "mosjoen": (65.8377, 13.1934),
    "egersund": (58.4513, 5.9985),
    "førde": (61.4520, 5.8552),
    "forde": (61.4520, 5.8552),
    "kirkenes": (69.7273, 30.0451),
    "vadsø": (70.0729, 29.7497),
    "vadso": (70.0729, 29.7497),
    "hammerfest": (70.6634, 23.6821),
    "ski": (59.7195, 10.8388),
    "lillestrøm": (59.9560, 11.0494),
    "lillestrom": (59.9560, 11.0494),
    "asker": (59.8333, 10.4396),
    "bærum": (59.8939, 10.5469),
    "barum": (59.8939, 10.5469),
    "longyearbyen": (78.2232, 15.6267),
    "svalbard": (78.2232, 15.6267),
    "ullensaker": (60.1430, 11.1740),
    "lørenskog": (59.9269, 10.9661),
    "lorenskog": (59.9269, 10.9661),
    "rælingen": (59.9333, 11.0500),
    "ralingen": (59.9333, 11.0500),
}


def lookup(name: str) -> tuple[str, float, float] | None:
    """Resolve a Norwegian city name to (canonical_name, lat, lon).

    Tries exact (case-insensitive) match first, then fuzzy match against
    the canonical names. Returns None if no good match.
    """
    if not name:
        return None
    key = name.strip().lower()
    if key in CITIES:
        lat, lon = CITIES[key]
        return (key, lat, lon)
    # Fuzzy fallback — only accept matches with high similarity (cutoff 0.75)
    matches = get_close_matches(key, CITIES.keys(), n=1, cutoff=0.75)
    if matches:
        lat, lon = CITIES[matches[0]]
        return (matches[0], lat, lon)
    return None


def all_cities() -> list[dict]:
    """Return a deduplicated list of city entries (canonical names only)."""
    seen_coords: set[tuple[float, float]] = set()
    out: list[dict] = []
    for name, (lat, lon) in CITIES.items():
        if (lat, lon) in seen_coords:
            continue
        seen_coords.add((lat, lon))
        out.append({"name": name, "lat": lat, "lon": lon})
    return sorted(out, key=lambda c: c["name"])
