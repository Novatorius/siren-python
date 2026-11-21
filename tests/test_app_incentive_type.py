"""Slice C16-6: the incentive type kind, and the answer the request handler's
sample builds, which passes Siren's answer check for the kind.

The question is Siren's own fixture (tests/Fixtures/apps-feature-contracts/
requests.json on Siren's pkt/apps-c16-6), the same file this SDK copies.
``refusal_of()`` holds the rules of Siren's
AppIncentiveTypeQuestion::validate(), in its order, and Siren's
AppFeatureContractsTest runs the fixture's answer through validate() itself."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import siren
from siren import AppItemKind

CONTRACTS = json.loads(
    (Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text()
)
REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"


def refusal_of(question: dict, answer: dict) -> str | None:
    """Siren's AppIncentiveTypeQuestion::validate(): why it refuses the answer with 422, or None."""
    outcome = answer.get("outcome")
    if outcome == "none":
        return None if set(answer) == {"outcome"} else "extra"
    if outcome != "award":
        return "outcome"
    if answer.get("units") != question["program"]["units"]:
        return "units"
    pool = answer.get("pool")
    if not isinstance(pool, int) or isinstance(pool, bool) or pool < 0:
        return "pool"
    ids = answer.get("engagementIds")
    if not isinstance(ids, list) or ids == []:
        return "engagementIds"
    asked = [engagement["engagementId"] for engagement in question["engagements"]]
    seen: set = set()
    for engagement_id in ids:
        if not isinstance(engagement_id, int) or isinstance(engagement_id, bool) or engagement_id not in asked:
            return f"not asked {engagement_id}"
        if engagement_id in seen:
            return f"twice {engagement_id}"
        seen.add(engagement_id)
    return None if set(answer) <= {"outcome", "units", "pool", "engagementIds"} else "extra"


def pay_on_closed_deals(request):
    """The sample's function, as README and test_answer_handler_samples.py write it."""
    sale, ids = request.data["transaction"], [e["engagementId"] for e in request.data["engagements"]]
    if not sale or sale["units"] != request.data["program"]["units"]:
        return {"outcome": "none"}  # A lead earns nothing here.
    return {"outcome": "award", "units": sale["units"], "pool": sale["total"] // 10, "engagementIds": ids}


def answer_of(data: dict) -> dict:
    # As the handler posts it, JSON encoded.
    return json.loads(json.dumps(pay_on_closed_deals(SimpleNamespace(data=data))))


def test_the_kind_is_the_value_siren_stores_and_its_types_are_public():
    assert AppItemKind.INCENTIVE_TYPE == "incentiveType"
    for name in ["AppIncentiveTypeQuestion", "AppIncentiveTypeEngagement", "AppIncentiveTypeAwardAnswer", "AppIncentiveTypeNoneAnswer", "AppIncentiveTypeAnswer"]:
        assert name in siren.__all__
        assert getattr(siren, name) is not None


def test_the_sample_answer_is_the_fixtures_and_passes_sirens_check():
    kind = CONTRACTS["kinds"]["incentiveType"]
    data = kind["request"]["data"]

    answer = answer_of(data)

    assert refusal_of(data, answer) is None
    assert answer == kind["answer"]


def test_the_sample_awards_nothing_for_a_lead():
    data = copy.deepcopy(CONTRACTS["kinds"]["incentiveType"]["request"]["data"])
    data["transaction"] = None
    data["binding"] = {"type": "crm_lead", "id": "L-1"}

    answer = answer_of(data)

    assert answer == {"outcome": "none"}
    assert refusal_of(data, answer) is None


def test_the_rules_siren_checks_refuse_other_units_a_negative_pool_and_an_unasked_or_repeated_engagement():
    data = CONTRACTS["kinds"]["incentiveType"]["request"]["data"]
    award = {"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": [880]}

    assert refusal_of(data, {**award, "units": "EUR"}) == "units"
    assert refusal_of(data, {**award, "pool": -1}) == "pool"
    assert refusal_of(data, {**award, "engagementIds": []}) == "engagementIds"
    assert refusal_of(data, {**award, "engagementIds": [880, 999]}) == "not asked 999"
    assert refusal_of(data, {**award, "engagementIds": [880, 880]}) == "twice 880"
    assert refusal_of(data, {"outcome": "none", "pool": 0}) == "extra"
    assert refusal_of(data, {**award, "outcome": "rewards"}) == "outcome"


def test_the_answer_is_sent_as_it_is(client, mocked_api):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})
    answer = {"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": [880, 881]}

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer
