"""Slice C16-3: the transaction filter kind, and its answer of the lines to
include, which the request handler and the SDK's own checks send unchanged.
The question is the slice's example, in the envelope of Siren's own
distribution resolver request
(tests/fixtures/apps-feature-contracts/requests.json), with the incentive
resolver request's transaction from the same file, since both carry the same
currency, signed here with a test secret at the current time. The README's
handler sample runs here as written."""

from __future__ import annotations

import hashlib
import hmac
import json
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

import siren
from siren import (
    FeatureKind,
    AppRequest,
    TransactionFilterAnswer,
    TransactionFilterQuestion,
)

from .conftest import API_KEY

ROOT = Path(__file__).resolve().parents[1]
SECRET = "ab" * 32
CONTRACTS = json.loads((Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
API_BASE = CONTRACTS["apiBase"]
REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"

QUESTION: TransactionFilterQuestion = {
    "for": {"type": "program", "id": 3, "name": "Partner program"},
    "transaction": CONTRACTS["kinds"]["incentiveResolver"]["request"]["data"]["transaction"],
    "lines": [
        {"id": 5501, "type": "lineItem", "name": "Annual plan", "quantity": 1, "value": 12000, "productId": "prod_annual"},
        {"id": 5502, "type": "shipping", "name": "Shipping", "quantity": 1, "value": 800, "productId": None},
    ],
    "distribution": None,
    "refundDistribution": None,
}

DISTRIBUTOR_QUESTION: TransactionFilterQuestion = {
    **QUESTION,
    "for": {"type": "distributor", "id": 2, "name": "Partner leaderboard"},
    "distribution": {"id": 21},
    "refundDistribution": {"id": 22},
}

LINE_ITEMS: TransactionFilterAnswer = {"outcome": "filtered", "include": [5501]}
NOTHING: TransactionFilterAnswer = {"outcome": "filtered", "include": []}

# The README's handler sample, word for word.
README_SAMPLE = textwrap.dedent(
    """\
    @answers.on("transactionFilter", "plan-lines")
    def plan_lines(request):
        lines = request.data["lines"]
        return {"outcome": "filtered", "include": [line["id"] for line in lines if line["type"] == "lineItem"]}
    """
)


def body_for(key: str, data: Any) -> str:
    request = dict(CONTRACTS["kinds"]["distributionResolver"]["request"])
    request["feature"] = {"kind": "transactionFilter", "key": key, "id": "app:11"}
    request["data"] = data
    return json.dumps(request)


def sign(body: str) -> Dict[str, str]:
    """The headers WebhookSignature::headers() sends, signed now."""
    t = int(time.time())
    v1 = hmac.new(SECRET.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Siren-Event": "app.request",
        "X-Siren-Timestamp": str(t),
        "X-Siren-Signature-V2": f"t={t},v1={v1}",
    }


def test_the_kind_is_named_with_the_value_siren_stores():
    assert FeatureKind.TRANSACTION_FILTER == "transactionFilter"
    assert "transactionFilter" in [kind.value for kind in FeatureKind]
    assert siren.TransactionFilterAnswer is TransactionFilterAnswer


def test_the_readme_carries_the_sample_word_for_word():
    assert "```python\n" + README_SAMPLE + "```" in (ROOT / "README.md").read_text()


def test_the_handler_posts_the_lines_each_function_returns():
    import respx

    seen: List[BaseException] = []

    def nothing_late(request: AppRequest) -> TransactionFilterAnswer:
        late = request.data["distribution"] is not None and request.data["refundDistribution"] is not None
        return {"outcome": "filtered", "include": [] if late else [line["id"] for line in request.data["lines"]]}

    with siren.Siren(api_key=API_KEY, base_url=API_BASE) as client, respx.mock(base_url=API_BASE, assert_all_called=False) as api:
        route = api.post(url__regex=r"/apps/requests/[^/]+/answer$").respond(200, json={"status": "accepted"})
        answers = client.apps.request_handler(SECRET, on_error=lambda error, subject=None: seen.append(error))
        exec(README_SAMPLE, {"answers": answers})
        answers.on("transactionFilter", "nothing-late", nothing_late)

        for key, data in [("plan-lines", QUESTION), ("nothing-late", DISTRIBUTOR_QUESTION)]:
            body = body_for(key, data)
            reply = answers(body, sign(body))
            assert reply.status == 200, reply.body
            reply.after()

        assert seen == []
        assert [json.loads(call.request.content) for call in route.calls] == [LINE_ITEMS, NOTHING]


@pytest.mark.parametrize("answer", [LINE_ITEMS, NOTHING], ids=["the lines to include", "an empty list"])
def test_an_answer_is_sent_as_it_is(client, mocked_api, answer):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer
