"""A signed body that is not an app request of version 1 gets 400 with words
that name the first field that fails and the rule it breaks."""

from __future__ import annotations

import json

import pytest

import siren
from siren import testing

SECRET = "cd" * 32
GOOD = json.loads(testing.app_request("metricFilter", "refund-check", secret=SECRET, id="apr_" + "c" * 32).raw_body)


def signed(body: object) -> testing.AppTestRequest:
    """``body`` signed as Siren signs, whatever its shape."""
    raw = json.dumps(body).encode()
    return testing.AppTestRequest.signed(raw, SECRET)


def without(*names: str) -> dict:
    return {key: value for key, value in GOOD.items() if key not in names}


@pytest.mark.parametrize(
    "body, words",
    [
        ([1, 2], "The signed body must be a JSON object."),
        ({**GOOD, "version": 2}, "The request's `version` must be 1."),
        ({**GOOD, "version": True}, "The request's `version` must be 1."),
        ({**GOOD, "id": "apr_x1"}, "The request's `id` must be `apr_` followed by 32 lowercase hex characters."),
        ({**GOOD, "type": "conversion.approved"}, "The request's `type` must be `app.request`."),
        ({**GOOD, "mode": "maybe"}, "The request's `mode` must be `tell` or `ask`."),
        ({**GOOD, "app": ""}, "The request's `app` must be the app's key, as text."),
        ({**GOOD, "item": "metricFilter"}, "The request's `item` must be an object with `kind`, `key` and `id`."),
        ({**GOOD, "item": {"key": "k", "id": "app:1"}}, "The request's `item.kind` must be text."),
        ({**GOOD, "item": {"kind": "metricFilter", "id": "app:1"}}, "The request's `item.key` must be text."),
        ({**GOOD, "item": {"kind": "metricFilter", "key": "k"}}, "The request's `item.id` must be text."),
        (without("createdAt"), "The request's `createdAt` must be a date as text."),
        ({**GOOD, "data": [1, 2]}, "The request's `data` must be a JSON object."),
        (without("answerBy"), "An ask's `answerBy` must be a date as text."),
        (without("answerUrl"), "An ask's `answerUrl` must be the answer address as text."),
    ],
)
def test_each_rule_names_its_field(body, words):
    request = signed(body)
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)

    reply = answers(request.raw_body, request.headers)
    assert (reply.status, reply.body) == (400, {"error": words})

    with pytest.raises(siren.SignatureVerificationError) as raised:
        siren.Siren(api_key="sk_test").apps.verify_request(request.raw_body, request.headers, SECRET)
    assert str(raised.value) == words or raised.value.message == words
