"""Pass 5: a key passed to ``app_request_from_record()`` wins over the saved
one, as in Node, and ``needs_attention`` alone is still sent as before."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from siren.testing import app_request_from_record

RECORD = (Path(__file__).parent / "fixtures" / "apps-cli" / "request.json").read_text()
SECRET = "ab" * 32


def _feature_key(raw_body: bytes) -> str:
    return json.loads(raw_body)["feature"]["key"]


def test_a_passed_key_wins_over_the_saved_one():
    assert _feature_key(app_request_from_record(RECORD, key="other-key", secret=SECRET).raw_body) == "other-key"


def test_the_saved_key_is_used_when_none_is_passed():
    saved = json.loads(RECORD)["featureKey"]
    assert _feature_key(app_request_from_record(RECORD, secret=SECRET).raw_body) == saved


def test_needs_attention_alone_is_still_sent(client, mocked_api):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    assert client.apps.requests.list(status="needs_attention") == []
    assert route.calls.last.request.url.params == httpx.QueryParams({"status": "needs_attention"})
