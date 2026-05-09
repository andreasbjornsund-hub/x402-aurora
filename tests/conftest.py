"""Shared pytest fixtures for Aurora."""
import os
import sys

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="session")
def main_module():
    os.environ.setdefault("EVM_ADDRESS", "0xTEST0000000000000000000000000000000000")
    os.chdir(REPO_ROOT)
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import main
    return main


@pytest.fixture
def met_module(main_module):
    import met_client
    return met_client


@pytest.fixture
def parsers_module(main_module):
    import parsers
    return parsers


@pytest.fixture
def cities_module(main_module):
    import cities
    return cities


@pytest.fixture(autouse=True)
def reset_met_cache(main_module):
    """Clear the MET cache before every test so cached data from one test
    doesn't leak into the next."""
    import met_client
    met_client.reset_cache()
    yield


class FakeMetResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json_data


class FakeMetClient:
    """Stub for httpx.AsyncClient: returns canned responses keyed by url substring."""

    def __init__(self):
        self.responses: dict[str, FakeMetResponse] = {}
        self.calls: list[tuple[str, dict]] = []

    def stub(self, url_contains: str, status_code: int, json_data=None):
        self.responses[url_contains] = FakeMetResponse(status_code, json_data)

    async def get(self, url: str, params=None, headers=None):
        self.calls.append((url, params or {}))
        for needle, resp in self.responses.items():
            if needle in url:
                return resp
        return FakeMetResponse(404, {"error": f"unstubbed {url}"})

    async def aclose(self):
        pass


@pytest.fixture
def fake_met(main_module, monkeypatch):
    fake = FakeMetClient()
    monkeypatch.setattr(main_module, "_http", fake)
    return fake
