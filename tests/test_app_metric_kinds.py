"""Slice C16-1: the metric calculation and metric filter kinds, and an answer
of each shape, which the SDK's own checks let through unchanged."""

from __future__ import annotations

import json

import pytest

import siren
from siren import AppItemKind

REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"


def test_the_kinds_match_the_values_siren_stores():
    assert sorted(kind.value for kind in AppItemKind) == [
        "customEvent",
        "distributionResolver",
        "incentiveResolver",
        "metricCalculation",
        "metricFilter",
        "payoutMethod",
        "postAllocationAction",
        "postConversionAction",
        "transactionFilter",
    ]
    assert AppItemKind.METRIC_CALCULATION == "metricCalculation"
    assert AppItemKind.METRIC_FILTER == "metricFilter"
    assert siren.AppItemKind is AppItemKind


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "scores", "results": [{"collaboratorId": 7, "score": 10}]},
        {"outcome": "scores", "results": []},
        {"outcome": "allow"},
        {"outcome": "suppress", "reason": "Customer already owned by a partner"},
    ],
    ids=["calculation scores", "calculation scores nobody", "filter allows", "filter suppresses"],
)
def test_an_answer_of_each_shape_is_sent_as_it_is(client, mocked_api, answer):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer
