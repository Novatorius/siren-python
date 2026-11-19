"""An app's side of Siren's exchange: checking a request Siren signed, and
answering an ask. The wire fixtures are Siren's own
(tests/Fixtures/apps-exchange in Siren, which its AppsExchangeFlowTest holds
to what Siren signs), copied unchanged."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

import siren
from siren.apps import MAX_ANSWER_BYTES

FIXTURES = Path(__file__).parent / "fixtures" / "apps-exchange"
REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def signed():
    data = fixture("signed-request.json")
    return data["body"], dict(data["headers"]), data["secret"], data["timestamp"]


def resign(body: str, headers: dict, secret: str, t: int) -> dict:
    signature = hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return {**headers, "X-Siren-Timestamp": str(t), "X-Siren-Signature-V2": f"t={t},v1={signature}"}


def test_the_fixture_verifies_and_parses(client, signed):
    body, headers, secret, t = signed

    request = client.apps.verify_request(body, headers, secret, now=t)

    assert isinstance(request, siren.AppRequest)
    assert request.id == REQUEST_ID
    assert request.mode == "ask" and request.is_ask
    assert request.app == "acme-payouts"
    assert request.item == {"kind": "payoutMethod", "key": "acme-ledger", "id": "app:12"}
    assert request.data == {"payoutId": 88, "amount": 2500, "currency": "USD"}
    assert request.answer_by == "2026-11-18T09:14:03+00:00"
    assert request.answer_url == f"https://api.siren.test/siren/v1/apps/requests/{REQUEST_ID}/answer"
    assert request.timestamp == 1794913200
    assert request.delivery_id == f"{REQUEST_ID}.1"
    assert request.raw == json.loads(body)


def test_bytes_and_headers_in_any_case_or_as_lists(client, signed):
    body, headers, secret, t = signed
    lower = {name.lower(): [value] for name, value in headers.items()}

    assert client.apps.verify_request(body.encode(), lower, secret, now=t).id == REQUEST_ID


@pytest.mark.parametrize("change", ["body", "secret"])
def test_a_tampered_body_or_another_secret_is_refused(client, signed, change):
    body, headers, secret, t = signed
    if change == "body":
        body = body.replace("2500", "25000")
    else:
        secret = "c" * 64

    with pytest.raises(siren.SignatureVerificationError, match="signature does not match"):
        client.apps.verify_request(body, headers, secret, now=t)


def test_any_signature_of_several_may_match(client, signed):
    body, headers, secret, t = signed
    v1 = headers["X-Siren-Signature-V2"].split(",", 1)[1]
    headers["X-Siren-Signature-V2"] = f"t={t},v1={'0' * 64},v1=nothex,{v1}"

    assert client.apps.verify_request(body, headers, secret, now=t).timestamp == t


@pytest.mark.parametrize(
    "change, message",
    [
        ({"X-Siren-Event": "conversion.approved"}, "This is a conversion.approved webhook, not an app request."),
        ({"X-Siren-Signature-V2": None}, "The request has no X-Siren-Signature-V2 header."),
        ({"X-Siren-Signature-V2": "sha256=abc"}, "cannot be read"),
        ({"X-Siren-Signature-V2": "t=1,t=2,v1=" + "a" * 64}, "cannot be read"),
        ({"X-Siren-Timestamp": "1794913201"}, "The X-Siren-Timestamp header differs"),
    ],
)
def test_a_bad_header_is_refused_with_the_reason(client, signed, change, message):
    body, headers, secret, t = signed
    for name, value in change.items():
        if value is None:
            headers.pop(name)
        else:
            headers[name] = value

    with pytest.raises(siren.SignatureVerificationError, match=message):
        client.apps.verify_request(body, headers, secret, now=t)


def test_a_signed_time_outside_the_tolerance_is_refused_as_a_replay(client, signed):
    body, headers, secret, t = signed

    assert client.apps.verify_request(body, headers, secret, now=t + 300).timestamp == t
    assert client.apps.verify_request(body, headers, secret, tolerance_seconds=3600, now=t - 3600).timestamp == t
    with pytest.raises(siren.SignatureVerificationError, match="more than 300 seconds"):
        client.apps.verify_request(body, headers, secret, now=t + 301)


def test_a_signed_time_ahead_of_the_clock_by_more_than_the_tolerance_is_refused(client, signed):
    body, headers, secret, t = signed

    assert client.apps.verify_request(body, headers, secret, now=t - 300).timestamp == t
    with pytest.raises(siren.SignatureVerificationError, match="more than 300 seconds"):
        client.apps.verify_request(body, headers, secret, now=t - 301)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"secret": ""},
        {"tolerance_seconds": 0},
        {"tolerance_seconds": 3601},
        {"tolerance_seconds": True},
        {"now": float("nan")},
        {"raw_body": {"parsed": True}},
    ],
)
def test_the_arguments_are_checked_first(client, signed, kwargs):
    body, headers, secret, t = signed
    call = {"raw_body": body, "headers": headers, "secret": secret, "now": t, **kwargs}

    with pytest.raises(ValueError, match=r"apps\.verify_request\(\)"):
        client.apps.verify_request(**call)


def _good_envelope() -> dict:
    return json.loads(fixture("signed-request.json")["body"])


def _without(key: str) -> dict:
    body = _good_envelope()
    body.pop(key)
    return body


@pytest.mark.parametrize(
    "envelope",
    [
        {**_good_envelope(), "version": 2},
        {**_good_envelope(), "version": True},
        {**_good_envelope(), "id": "apr_123"},
        {**_good_envelope(), "type": "conversion.approved"},
        {**_good_envelope(), "app": ""},
        {**_good_envelope(), "item": {"key": "k", "id": "app:1"}},
        {**_good_envelope(), "data": [1, 2]},
        {**_good_envelope(), "mode": "maybe"},
        _without("answerUrl"),
    ],
)
def test_a_signed_body_that_is_not_a_request_is_refused(client, signed, envelope):
    _, headers, secret, t = signed
    body = json.dumps(envelope)

    with pytest.raises(siren.SignatureVerificationError, match="not an app request of version 1"):
        client.apps.verify_request(body, resign(body, headers, secret, t), secret, now=t)


def test_a_tell_needs_no_answer_address(client, signed):
    _, headers, secret, t = signed
    tell = {**_good_envelope(), "mode": "tell"}
    tell.pop("answerBy")
    tell.pop("answerUrl")
    body = json.dumps(tell)

    request = client.apps.verify_request(body, resign(body, headers, secret, t), secret, now=t)

    assert not request.is_ask
    assert request.answer_by is None


def test_an_answer_is_posted_once_as_the_body_alone(client, mocked_api):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted"})

    receipt = client.apps.requests.answer(REQUEST_ID, {"outcome": "paid", "reference": "acme-ledger-5521"})

    assert receipt == siren.AppAnswerReceipt(status="accepted", duplicate=False)
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == {"outcome": "paid", "reference": "acme-ledger-5521"}


def test_the_fixtures_exchanges_get_the_replies_they_expect(client, mocked_api):
    for exchange in fixture("answer.json")["exchanges"]:
        mocked_api.routes.clear()
        if "code" in exchange:
            reply = httpx.Response(exchange["status"], json={"error": {"message": "Another answer was taken first.", "code": exchange["code"]}})
        else:
            reply = httpx.Response(exchange["status"], json=exchange["reply"])
        mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").mock(return_value=reply)

        if "code" in exchange:
            with pytest.raises(siren.ConflictError) as raised:
                client.apps.requests.answer(REQUEST_ID, exchange["answer"])
            assert raised.value.code == exchange["code"], exchange["name"]
        else:
            receipt = client.apps.requests.answer(REQUEST_ID, exchange["answer"])
            assert receipt.duplicate is exchange["reply"].get("duplicate", False), exchange["name"]


def test_an_answer_is_never_retried(client, mocked_api):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").mock(
        side_effect=[httpx.Response(503, json={}), httpx.Response(200, json={"status": "accepted"})]
    )

    with pytest.raises(siren.ApiError):
        client.apps.requests.answer(REQUEST_ID, {"outcome": "paid"})
    assert route.call_count == 1


@pytest.mark.parametrize(
    "request_id, answer, message",
    [
        ("88", {"outcome": "paid"}, "needs a request id"),
        (REQUEST_ID, ["paid"], "takes the answer as a mapping"),
        (REQUEST_ID, {"reference": "TX-1"}, "needs a text outcome"),
        (REQUEST_ID, {"outcome": 1}, "needs a text outcome"),
        (REQUEST_ID, {"outcome": "paid", "at": object()}, "could not write the answer as JSON"),
        (REQUEST_ID, {"outcome": "paid", "note": "a" * MAX_ANSWER_BYTES}, "at most 16384 bytes"),
    ],
)
def test_an_answer_that_cannot_be_sent_is_refused_before_sending(client, mocked_api, request_id, answer, message):
    route = mocked_api.post(url__regex=r".*/answer$").respond(200, json={"status": "accepted"})

    with pytest.raises(ValueError, match=message):
        client.apps.requests.answer(request_id, answer)
    assert route.call_count == 0


def test_an_uncoded_unauthorized_answer_gets_the_app_code(client, mocked_api):
    mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(
        401, json={"error": {"message": "Invalid API key. Make a new one under API keys."}}
    )

    with pytest.raises(siren.AuthenticationError) as raised:
        client.apps.requests.answer(REQUEST_ID, {"outcome": "paid"})

    assert raised.value.code == siren.ErrorCode.APP_ANSWER_UNAUTHORIZED == "app_answer_unauthorized"
    assert raised.value.status_code == 401
    assert "siren apps rotate-key" in raised.value.message
    assert isinstance(raised.value.__cause__, siren.AuthenticationError)


def test_an_answer_that_does_not_fit_the_kind_stays_a_validation_error(client, mocked_api):
    mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(
        422, json={"error": {"message": "outcome must be paid or failed", "code": "app_answer_invalid"}}
    )

    with pytest.raises(siren.ValidationError):
        client.apps.requests.answer(REQUEST_ID, {"outcome": "maybe"})


@pytest.mark.parametrize("reply", [{"status": "queued"}, {"status": "accepted", "duplicate": "yes"}, ["accepted"]])
def test_a_reply_that_is_not_an_accepted_answer_is_raised(client, mocked_api, reply):
    mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json=reply)

    with pytest.raises(siren.ApiError) as raised:
        client.apps.requests.answer(REQUEST_ID, {"outcome": "paid"})
    assert raised.value.data == reply
