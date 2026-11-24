"""Slice C16-5: the program group structure kind, and the answer the request
handler's sample builds, which passes Siren's answer check for the kind.

The question is Siren's own fixture (tests/Fixtures/apps-feature-contracts/
requests.json on Siren's pkt/apps-c16-5), the same file this SDK copies.
``refusal_of()`` holds the rules of Siren's AppGroupOrderQuestion::validate(),
in its order, and Siren's AppFeatureContractsTest runs the fixture's answer
through validate() itself."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import siren
from siren import FeatureKind

CONTRACTS = json.loads(
    (Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text()
)
REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"


def refusal_of(question: dict, answer: dict) -> str | None:
    """Siren's AppGroupOrderQuestion::validate(): why it refuses the answer with 422, or None."""
    if answer.get("outcome") != "sorted":
        return "outcome"
    order = answer.get("programIds")
    if not isinstance(order, list):
        return "programIds"
    asked = [program["id"] for program in question["programs"]]
    seen: set = set()
    for program_id in order:
        if not isinstance(program_id, int) or isinstance(program_id, bool):
            return "not whole"
        if program_id not in asked:
            return f"not asked {program_id}"
        if program_id in seen:
            return f"twice {program_id}"
        seen.add(program_id)
    for program_id in asked:
        if program_id not in seen:
            return f"left out {program_id}"
    return None


def pick_the_program(request):
    """The sample's function, as README and test_answer_handler_samples.py write it."""
    # Try the program whose best engagement scores highest first.
    best = {p["id"]: max([0] + [e["score"] for e in p["engagements"]]) for p in request.data["programs"]}
    return {"outcome": "sorted", "programIds": sorted(best, key=best.get, reverse=True)}


def answer_of(data: dict) -> dict:
    # As the handler posts it, JSON encoded.
    return json.loads(json.dumps(pick_the_program(SimpleNamespace(data=data))))


def test_the_kind_is_the_value_siren_stores():
    assert FeatureKind.PROGRAM_GROUP_SORTER == "programGroupSorter"
    assert siren.AppGroupOrderQuestion.__name__ == "AppGroupOrderQuestion"


def test_the_sample_answer_is_the_fixtures_and_passes_sirens_check():
    kind = CONTRACTS["kinds"]["programGroupSorter"]
    data = kind["request"]["data"]

    answer = answer_of(data)

    assert refusal_of(data, answer) is None
    assert answer == kind["answer"]


def test_a_tie_keeps_the_groups_order_and_a_program_with_no_engagement_comes_last():
    data = copy.deepcopy(CONTRACTS["kinds"]["programGroupSorter"]["request"]["data"])
    data["programs"] = [
        {"id": 4, "name": "Quiet", "engagements": []},
        {"id": 12, "name": "Resellers", "engagements": [{"engagementId": 880, "collaboratorId": 42, "score": 40, "createdDate": "2026-11-02T09:15:00+00:00"}]},
        {"id": 9, "name": "Affiliates", "engagements": [{"engagementId": 881, "collaboratorId": 57, "score": 40, "createdDate": "2026-11-09T16:40:00+00:00"}]},
    ]

    answer = answer_of(data)

    assert answer["programIds"] == [12, 9, 4]
    assert refusal_of(data, answer) is None


def test_the_rules_siren_checks_refuse_a_missing_a_repeated_and_an_unasked_program():
    data = CONTRACTS["kinds"]["programGroupSorter"]["request"]["data"]

    assert refusal_of(data, {"outcome": "sorted", "programIds": [9]}) == "left out 12"
    assert refusal_of(data, {"outcome": "sorted", "programIds": [9, 12, 9]}) == "twice 9"
    assert refusal_of(data, {"outcome": "sorted", "programIds": [9, 12, 40]}) == "not asked 40"
    assert refusal_of(data, {"outcome": "allocated", "programIds": [9, 12]}) == "outcome"


def test_the_answer_is_sent_as_it_is(client, mocked_api):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})
    answer = {"outcome": "sorted", "programIds": [9, 12]}

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer
