"""Slice C16-4: the engagement calculation and engagement filter kinds, and an
answer of each shape, which the SDK's own checks let through unchanged. The
shapes are Siren's AppEngagementCalculationAnswerHandler and
AppEngagementFilterAnswerHandler on its pkt/apps-c16-4."""

from __future__ import annotations

import json

import pytest

import siren
from siren import AppItemKind

REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"


def test_the_kinds_match_the_values_siren_stores():
    assert AppItemKind.ENGAGEMENT_CALCULATION == "engagementCalculation"
    assert AppItemKind.ENGAGEMENT_FILTER == "engagementFilter"
    assert siren.AppEngagementCalculationQuestion.__annotations__.keys() >= {"program", "args", "opportunity", "cause"}
    assert siren.AppEngagementFilterQuestion.__annotations__.keys() >= {"program", "collaborator", "opportunity", "cause"}


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "engagements", "results": [{"collaboratorId": 42, "score": 10}, {"collaboratorId": 57, "score": 2}]},
        {"outcome": "engagements", "results": []},
        {"outcome": "allow"},
        {"outcome": "suppress", "reason": "The visitor works for the partner."},
    ],
    ids=["calculation credits two", "calculation credits nobody", "filter allows", "filter suppresses"],
)
def test_an_answer_of_each_shape_is_sent_as_it_is(client, mocked_api, answer):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer
