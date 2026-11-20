"""Slice C16-2: the incentive resolver kind, its answer, which the SDK's own
checks let through unchanged, and the 409 Siren answers once it stopped
waiting. The answers are Siren's example in
tests/Fixtures/apps-feature-contracts/requests.json on its pkt/apps-c16-2."""

from __future__ import annotations

import json

import httpx
import pytest

import siren
from siren import AppItemKind

REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"


def test_the_kinds_match_the_values_siren_stores():
    assert sorted(kind.value for kind in AppItemKind) == [
        "customEvent",
        "distributionResolver",
        "incentiveResolver",
        "payoutMethod",
        "postAllocationAction",
        "postConversionAction",
    ]
    assert AppItemKind.INCENTIVE_RESOLVER == "incentiveResolver"
    assert siren.AppItemKind is AppItemKind


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "rewards", "units": "USD", "rewards": {"1042": 900, "1043": 600}},
        {"outcome": "rewards", "units": "USD", "rewards": {"1042": 1500, "1043": None}},
    ],
    ids=["split by score", "one collaborator earns nothing"],
)
def test_an_answer_of_each_shape_is_sent_as_it_is(client, mocked_api, answer):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer


def test_an_answer_after_siren_stopped_waiting_is_a_conflict_sent_once(client, mocked_api):
    # AppRequestService::dropped() on Siren's pkt/apps-c16-2.
    message = "Siren stopped waiting for this answer: the app didn't answer within 24 hours, so the work went on without it."
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").mock(
        return_value=httpx.Response(409, json={"error": {"message": message, "code": "app_request_dropped", "context": []}})
    )

    with pytest.raises(siren.ConflictError) as raised:
        client.apps.requests.answer(REQUEST_ID, {"outcome": "rewards", "units": "USD", "rewards": {"1042": 900, "1043": 600}})

    assert raised.value.code == "app_request_dropped"
    assert route.call_count == 1
