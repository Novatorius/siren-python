from __future__ import annotations

import pytest
import respx

import siren

API_KEY = "sk_live_" + "a" * 64
BASE_URL = "https://api.sirenaffiliates.com/siren/v1"


@pytest.fixture
def client():
    with siren.Siren(api_key=API_KEY) as instance:
        yield instance


@pytest.fixture
def mocked_api():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        yield mock


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make retry backoff instantaneous, but record requested delays."""
    delays = []
    monkeypatch.setattr(
        "siren._client.time.sleep", lambda seconds: delays.append(seconds)
    )
    return delays
