"""The payout method ask: its question read from Siren's JSON, an answer of
each shape, which the SDK's own checks let through unchanged, a paid answer
posted from the request handler, and the 409 Siren answers once it dropped
the ask. The envelope and data are the contract fixed for the kind."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest

import siren
from siren import AppItemKind, AppPayoutAnswer, AppPayoutQuestion

from .conftest import API_KEY

SECRET = "ab" * 32
REQUEST_ID = "apr_9c60190a4501399d0ae55f041c237b39"
API_BASE = "https://cloud.example.com/siren/v1"

ENVELOPE = {
    "version": 1,
    "id": REQUEST_ID,
    "type": "app.request",
    "mode": "ask",
    "app": "acme-crm",
    "item": {"kind": "payoutMethod", "key": "acme-ledger", "id": "app:12"},
    "createdAt": "2026-11-20T10:00:00+00:00",
    "answerBy": "2026-11-21T10:00:00+00:00",
    "answerUrl": f"{API_BASE}/apps/requests/{REQUEST_ID}/answer",
    "data": {
        "payout": {"id": 88, "fulfillmentId": 31, "attempt": 1},
        "amount": {
            "value": 2500,
            "units": "USD",
            "currency": {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"},
        },
        "collaborator": {"id": 42, "name": "Ana Ruiz", "email": "ana@example.com"},
    },
}


def signed(body: str) -> dict:
    t = int(time.time())
    v1 = hmac.new(SECRET.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Siren-Signature": "sha256=" + hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest(),
        "X-Siren-Event": "app.request",
        "X-Siren-Delivery": f"{REQUEST_ID}.1",
        "X-Siren-Timestamp": str(t),
        "X-Siren-Signature-V2": f"t={t},v1={v1}",
        "Idempotency-Key": REQUEST_ID,
    }


def test_the_kind_has_the_value_siren_stores():
    assert AppItemKind.PAYOUT_METHOD == "payoutMethod"
    assert siren.AppPayoutQuestion is AppPayoutQuestion


def test_a_question_parsed_from_the_json_reads_as_the_typed_shape():
    question: AppPayoutQuestion = json.loads(json.dumps(ENVELOPE))["data"]

    assert question["payout"]["id"] == 88
    assert question["payout"]["attempt"] == 1
    assert question["amount"]["value"] == 2500
    assert question["collaborator"]["email"] == "ana@example.com"


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "paid", "reference": "acme-ledger-5521"},
        {"outcome": "failed", "reason": "The account is closed."},
    ],
    ids=["paid", "failed"],
)
def test_an_answer_of_each_shape_is_sent_as_it_is(client, mocked_api, answer: AppPayoutAnswer):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer


def test_the_handler_posts_the_paid_answer_a_payout_function_returns():
    import respx

    seen = []

    def pay(request):
        seen.append((request.data["payout"]["id"], request.data["amount"]["value"]))
        return {"outcome": "paid", "reference": f"acme-ledger-{request.data['payout']['id']}"}

    with siren.Siren(api_key=API_KEY, base_url=API_BASE) as app_client, respx.mock(base_url=API_BASE, assert_all_called=False) as api:
        route = api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})
        answers = app_client.apps.request_handler(SECRET).on("payoutMethod", "acme-ledger", pay)
        body = json.dumps(ENVELOPE)

        reply = answers(body, signed(body))
        assert reply.status == 200
        reply.after()

    assert seen == [(88, 2500)]
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {"outcome": "paid", "reference": "acme-ledger-88"}


def test_an_answer_after_siren_dropped_the_ask_is_a_conflict_sent_once(client, mocked_api):
    message = "Siren stopped waiting for this answer."
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").mock(
        return_value=httpx.Response(409, json={"error": {"message": message, "code": "app_request_dropped", "context": []}})
    )

    with pytest.raises(siren.ConflictError) as raised:
        client.apps.requests.answer(REQUEST_ID, {"outcome": "paid", "reference": "acme-ledger-5521"})

    assert raised.value.code == "app_request_dropped"
    assert route.call_count == 1
