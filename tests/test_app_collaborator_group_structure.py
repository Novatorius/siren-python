"""Slice C16-7: the collaborator group structure kind, and a chain and a tree
answer, which the request handler and the SDK's own checks send unchanged.
The question is the slice's example from the distributor side, in the
envelope of Siren's own distribution resolver request
(tests/fixtures/apps-feature-contracts/requests.json), signed here with a
test secret at the current time."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, get_type_hints

import pytest

import siren
from siren import (
    AppCollaboratorGroupChain,
    AppCollaboratorGroupMember,
    AppCollaboratorGroupStructureAnswer,
    AppCollaboratorGroupStructureDistributorQuestion,
    AppCollaboratorGroupStructureProgramQuestion,
    AppCollaboratorGroupTree,
    FeatureKind,
    AppRequest,
)

from .conftest import API_KEY

SECRET = "ab" * 32
CONTRACTS = json.loads((Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
API_BASE = CONTRACTS["apiBase"]
REQUEST_ID = "apr_b79943ff8ca353ce5cf5312bb3fa66c7"

QUESTION: AppCollaboratorGroupStructureDistributorQuestion = {
    "group": {"id": 4, "name": "Northeast team"},
    "members": [
        {"collaboratorId": 42, "name": "Riley Rep", "status": "active", "metadata": {"rank": 3}},
        {"collaboratorId": 57, "name": "Sam Lead", "status": "active", "metadata": {"rank": 2}},
        {"collaboratorId": 63, "name": "Jo Manager", "status": "active", "metadata": {"rank": 1}},
    ],
    "for": {"type": "distributor", "id": 2},
    "triggeringCollaborator": {"id": 42, "name": "Riley Rep"},
    "cause": {"id": "9f2c4e1a7b3d5c60", "type": None, "causeId": None},
    "distributor": {"id": 2, "name": "Partner leaderboard"},
    "distribution": {"id": 21, "periodEnd": "2026-12-01T00:00:00+00:00"},
    "metricType": "referredSiteVisit",
    "distributorMetricTypeId": 55,
    "opportunityId": 615,
    "points": None,
    "sale": None,
}

PROGRAM_QUESTION: AppCollaboratorGroupStructureProgramQuestion = {
    "group": {"id": 4, "name": "Northeast team"},
    "members": QUESTION["members"],
    "for": {"type": "program", "id": 3},
    "triggeringCollaborator": {"id": 42, "name": "Riley Rep"},
    "cause": {"id": "9f2c4e1a7b3d5c60", "event": "referredSiteVisit", "occurredAt": "2026-11-17T12:20:00+00:00"},
    "program": {"id": 3, "name": "Partner program"},
    "engagementType": "referredSiteVisit",
    "programEngagementTypeId": 55,
    "args": {"pointsAtLayer1": "10"},
    "opportunity": {"id": 615, "customerId": None},
}

CHAIN: AppCollaboratorGroupChain = {"outcome": "structure", "shape": "chain", "order": [63, 57, 42]}
TREE: AppCollaboratorGroupTree = {"outcome": "structure", "shape": "tree", "parents": {"42": 57, "57": 63, "63": None}}


def body_for(key: str, question: Optional[Dict[str, object]] = None) -> str:
    request = dict(CONTRACTS["kinds"]["distributionResolver"]["request"])
    request["feature"] = {"kind": "collaboratorGroupStructure", "key": key, "id": "app:11"}
    request["data"] = question if question is not None else QUESTION
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
    assert FeatureKind.COLLABORATOR_GROUP_STRUCTURE == "collaboratorGroupStructure"
    assert "collaboratorGroupStructure" in [kind.value for kind in FeatureKind]
    assert siren.AppCollaboratorGroupStructureAnswer is AppCollaboratorGroupStructureAnswer
    assert siren.AppCollaboratorGroupStructureProgramQuestion is AppCollaboratorGroupStructureProgramQuestion
    assert "AppCollaboratorGroupStructureProgramQuestion" in siren.__all__


def test_each_member_carries_its_status_at_the_trigger():
    assert get_type_hints(AppCollaboratorGroupMember)["status"] is str


def test_the_program_question_holds_the_group_keys_and_the_engagement_calculation_keys():
    from siren import AppEngagementCalculationQuestion, AppCollaboratorGroupStructureQuestion

    program_keys = set(AppCollaboratorGroupStructureProgramQuestion.__annotations__)
    group_keys = set(AppCollaboratorGroupStructureQuestion.__annotations__)
    engagement_keys = set(AppEngagementCalculationQuestion.__annotations__)
    assert group_keys <= program_keys
    assert engagement_keys <= program_keys
    assert set(PROGRAM_QUESTION) == program_keys


def test_the_handler_posts_a_chain_and_a_tree_as_the_functions_return_them():
    import respx

    seen: List[BaseException] = []

    def by_rank(request: AppRequest) -> AppCollaboratorGroupStructureAnswer:
        members = sorted(request.data["members"], key=lambda member: member["metadata"]["rank"])
        return {"outcome": "structure", "shape": "chain", "order": [member["collaboratorId"] for member in members]}

    def by_lead(request: AppRequest) -> AppCollaboratorGroupStructureAnswer:
        members = request.data["members"]
        parents: Dict[str, Optional[int]] = {}
        for index, member in enumerate(members):
            parents[str(member["collaboratorId"])] = members[index + 1]["collaboratorId"] if index + 1 < len(members) else None
        return {"outcome": "structure", "shape": "tree", "parents": parents}

    with siren.Siren(api_key=API_KEY, base_url=API_BASE) as client, respx.mock(base_url=API_BASE, assert_all_called=False) as api:
        route = api.post(url__regex=r"/apps/requests/[^/]+/answer$").respond(200, json={"status": "accepted"})
        answers = client.apps.request_handler(SECRET, on_error=lambda error, subject=None: seen.append(error))
        answers.on("collaboratorGroupStructure", "by-rank", by_rank)
        answers.on("collaboratorGroupStructure", "by-lead", by_lead)

        for key, question in [("by-rank", None), ("by-lead", PROGRAM_QUESTION)]:
            body = body_for(key, question)
            reply = answers(body, sign(body))
            assert reply.status == 200, reply.body
            reply.after()

        assert seen == []
        assert [json.loads(call.request.content) for call in route.calls] == [CHAIN, TREE]


@pytest.mark.parametrize("answer", [CHAIN, TREE], ids=["a chain, top first", "a tree, by parent"])
def test_an_answer_of_each_shape_is_sent_as_it_is(client, mocked_api, answer):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/answer").respond(200, json={"status": "accepted", "duplicate": False})

    receipt = client.apps.requests.answer(REQUEST_ID, answer)

    assert receipt.status == "accepted"
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == answer
