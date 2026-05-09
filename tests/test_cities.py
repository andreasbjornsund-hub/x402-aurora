"""Tests for city geocoding."""


def test_exact_match(cities_module):
    result = cities_module.lookup("oslo")
    assert result is not None
    name, lat, lon = result
    assert name == "oslo"
    assert 59.0 < lat < 60.5
    assert 10.0 < lon < 11.5


def test_case_insensitive(cities_module):
    assert cities_module.lookup("OSLO") is not None
    assert cities_module.lookup("Oslo") is not None
    assert cities_module.lookup("  bergen  ") is not None


def test_norwegian_chars(cities_module):
    assert cities_module.lookup("tromsø") is not None
    assert cities_module.lookup("ålesund") is not None
    # ASCII fallbacks are also indexed
    assert cities_module.lookup("tromso") is not None
    assert cities_module.lookup("alesund") is not None


def test_fuzzy_match(cities_module):
    # close-enough typo
    result = cities_module.lookup("trondheim")
    assert result is not None and result[0] == "trondheim"
    result = cities_module.lookup("trondhem")  # missing 'i'
    assert result is not None and result[0] == "trondheim"


def test_no_match_returns_none(cities_module):
    assert cities_module.lookup("paris") is None
    assert cities_module.lookup("xyz") is None
    assert cities_module.lookup("") is None
    assert cities_module.lookup("   ") is None


def test_multi_word_city(cities_module):
    result = cities_module.lookup("mo i rana")
    assert result is not None
    assert "rana" in result[0]


def test_all_cities_returns_list(cities_module):
    cs = cities_module.all_cities()
    assert len(cs) > 30
    assert all("name" in c and "lat" in c and "lon" in c for c in cs)
    # No duplicate coordinates
    coords = [(c["lat"], c["lon"]) for c in cs]
    assert len(coords) == len(set(coords))


def test_all_cities_in_norway_bounds(cities_module, parsers_module):
    for c in cities_module.all_cities():
        assert parsers_module.in_norway(c["lat"], c["lon"]), f"{c['name']} {c['lat']},{c['lon']} out of bounds"
