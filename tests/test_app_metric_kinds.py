"""Slice C16-1: the metric calculation and metric filter kinds, and an answer
of each shape, which the SDK's own checks let through unchanged."""

from __future__ import annotations

import json
from typing import List, Literal, Optional, get_type_hints

import pytest

import siren
from siren import AppItemKind

REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"


def test_the_kinds_match_the_values_siren_stores():
    assert sorted(kind.value for kind in AppItemKind) == [
        "collaboratorGroupStructure",
        "customEvent",
        "distributionResolver",
        "engagementCalculation",
        "engagementFilter",
        "incentiveResolver",
        "incentiveType",
        "metricCalculation",
        "metricFilter",
        "payoutMethod",
        "postAllocationAction",
        "postConversionAction",
        "programGroupSorter",
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


CONTRACTS = json.loads(
    (__import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text()
)


def _data(kind):
    return CONTRACTS["kinds"][kind]["request"]["data"]


def test_the_metric_types_are_exported():
    for name in (
        "AppMetricCause",
        "AppMetricSale",
        "AppMetricCalculationQuestion",
        "AppMetricCalculationAnswer",
        "AppMetricFilterQuestion",
        "AppMetricFilterAnswer",
    ):
        assert name in siren.__all__, name
        assert getattr(siren, name) is getattr(siren.types, name)


def test_the_calculation_question_has_the_keys_siren_sends():
    # Siren's AppMetricQuestion::forCalculation(), and Node's AppMetricCalculationQuestion.
    keys = set(siren.AppMetricCalculationQuestion.__annotations__)
    assert keys == {
        "distributor",
        "distribution",
        "metricType",
        "distributorMetricTypeId",
        "triggeringCollaborator",
        "opportunityId",
        "points",
        "cause",
        "sale",
    }
    assert keys == set(_data("metricCalculation"))


def test_the_filter_question_has_the_keys_siren_sends():
    # Siren's AppMetricQuestion::forFilter(), and Node's AppMetricFilterQuestion.
    keys = set(siren.AppMetricFilterQuestion.__annotations__)
    assert keys == {"distributor", "distribution", "collaborator", "metricType", "opportunityId", "points", "cause", "sale"}
    assert keys == set(_data("metricFilter"))


def test_the_cause_and_the_sale_have_the_keys_siren_sends():
    hints = get_type_hints(siren.AppMetricCause)
    assert hints == {"id": str, "type": Optional[str], "causeId": Optional[str]}
    assert set(hints) == set(_data("metricFilter")["cause"])
    sale = get_type_hints(siren.AppMetricSale)
    assert sale == {"transactionId": int, "conversionId": Optional[int], "excludedLineIds": List[int]}
    assert set(sale) == set(_data("metricFilter")["sale"])
    question = get_type_hints(siren.AppMetricFilterQuestion)
    assert question["cause"] is siren.AppMetricCause
    assert question["sale"] == Optional[siren.AppMetricSale]
    assert question["opportunityId"] == Optional[int]
    assert question["points"] == Optional[int]


def test_the_answers_have_the_keys_siren_reads():
    assert set(siren.AppMetricCalculationAnswer.__annotations__) == set(CONTRACTS["kinds"]["metricCalculation"]["answer"])
    assert get_type_hints(siren.AppMetricCalculationAnswer)["outcome"] == Literal["scores"]
    assert set(siren.AppMetricFilterAnswer.__annotations__) == {"outcome", "reason"}
