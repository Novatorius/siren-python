"""DX pass 3, types and discoverability (review/sdk-dx/pass-3-review.md and
pass-3-builder-spec.md in the run folder).

- Finding 4: each kind's types are named from the kind, the same in all three
  SDKs, and the kind's hover text follows one template.
- Finding 1: the request is generic, and every fixed shape Siren builds is a
  TypedDict with Literal outcomes and reasons. tests/test_typecheck.py runs
  mypy on it.
- Finding 3: the types allow what Siren's builders send: a conversion with
  no reward, a sale with no currency, and engagement ``args`` sent as ``[]``.
- Finding 5: one error class for what the developer fixes, the same four
  fields from ``simulate``, and a resend hint that names a route that works.
- Parity: ``apps.requests`` lists, reads and resends requests, as Node's does.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import typing
from pathlib import Path
from typing import Any, Dict, get_type_hints

import httpx
import pytest

import siren
from siren import testing
from siren.apps import Apps
from siren.testing import AppTestRequest

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = json.loads((Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
SECRET = "cd" * 32
REQUEST_ID = "apr_" + "b339" * 8

# The builder spec's table: every SDK names each kind's types this way.
KIND_TYPES = {
    "postConversionAction": ["PostConversionActionTell"],
    "postAllocationAction": ["PostAllocationActionTell"],
    "distributionResolver": ["DistributionResolverQuestion", "DistributionResolverAnswer"],
    "incentiveResolver": ["IncentiveResolverQuestion", "IncentiveResolverAnswer"],
    "incentiveType": ["IncentiveTypeQuestion", "IncentiveTypeAnswer"],
    "metricCalculation": ["MetricCalculationQuestion", "MetricCalculationAnswer"],
    "metricFilter": ["MetricFilterQuestion", "MetricFilterAnswer"],
    "transactionFilter": ["TransactionFilterQuestion", "TransactionFilterAnswer"],
    "engagementCalculation": ["EngagementCalculationQuestion", "EngagementCalculationAnswer"],
    "engagementFilter": ["EngagementFilterQuestion", "EngagementFilterAnswer"],
    "programGroupSorter": ["ProgramGroupSorterQuestion", "ProgramGroupSorterAnswer"],
    "collaboratorGroupStructure": [
        "CollaboratorGroupStructureQuestion",
        "CollaboratorGroupStructureAnswer",
        "CollaboratorGroupStructureDistributorQuestion",
        "CollaboratorGroupStructureProgramQuestion",
        "CollaboratorGroupStructureChainAnswer",
        "CollaboratorGroupStructureTreeAnswer",
    ],
    "payoutMethod": [
        "PayoutMethodQuestion",
        "PayoutMethodAnswer",
        "PayoutMethodPaidAnswer",
        "PayoutMethodFailedAnswer",
    ],
}

OLD_NAMES = [
    "AppConversionActionData", "AppAllocationActionData", "AppDistributionQuestion", "AppDistributionAnswer",
    "AppRewardQuestion", "AppRewardAnswer", "AppRewardConversion", "AppIncentiveTypeQuestion",
    "AppIncentiveTypeAnswer", "AppIncentiveTypeAwardAnswer", "AppIncentiveTypeNoneAnswer",
    "AppMetricCalculationQuestion", "AppMetricCalculationAnswer", "AppMetricFilterQuestion",
    "AppMetricFilterAnswer", "AppEngagementCalculationQuestion", "AppEngagementCalculationAnswer",
    "AppEngagementFilterQuestion", "AppEngagementFilterAnswer", "AppGroupOrderQuestion", "AppGroupOrderAnswer",
    "AppGroupOrderProgram", "AppGroupOrderEngagement", "AppTransactionFilterQuestion",
    "AppTransactionFilterAnswer", "AppCollaboratorGroupStructureQuestion", "AppCollaboratorGroupStructureAnswer",
    "AppCollaboratorGroupStructureDistributorQuestion", "AppCollaboratorGroupStructureProgramQuestion",
    "AppCollaboratorGroupChain", "AppCollaboratorGroupTree", "AppPayoutQuestion", "AppPayoutAnswer",
    "AppPayoutPaid", "AppPayoutFailed",
]


def handler(**options):
    return siren.Siren(api_key="sk_test").apps.request_handler(SECRET, **options)


def generator():
    spec = importlib.util.spec_from_file_location("feature_kinds", ROOT / "scripts" / "feature_kinds.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Finding 4 ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(KIND_TYPES))
def test_each_kinds_types_are_named_from_the_kind(kind):
    for name in KIND_TYPES[kind]:
        assert hasattr(siren, name), f"siren.{name} is missing"
        assert name in siren.__all__


def test_the_table_covers_every_kind_siren_sends():
    assert sorted(KIND_TYPES) == sorted(siren.APP_REQUEST_KINDS)


@pytest.mark.parametrize("name", OLD_NAMES)
def test_no_old_type_name_is_kept(name):
    assert not hasattr(siren, name)
    assert not hasattr(siren.types, name)


def test_the_hover_sentence_follows_the_one_template():
    source = " ".join((ROOT / "src" / "siren" / "types.py").read_text().split())
    assert (
        'INCENTIVE_RESOLVER = "incentiveResolver" '
        "\"\"\"Attribution model. Siren's code calls it the incentive resolver."
    ) in source
    assert "Siren's own term" not in source
    assert "An ask: Siren waits for the app to answer." in source
    assert "A tell: Siren lets the app know." in source


def test_the_generator_writes_the_hover_sentence():
    entry = {
        "kind": "incentiveResolver",
        "term": "incentive resolver",
        "name": "Attribution model",
        "description": "Siren asks the app.",
        "mode": "ask",
        "direction": "in",
    }
    words = " ".join(" ".join(line.strip().strip('"') for line in generator().docstring(entry).splitlines()).split())
    assert words == (
        "Attribution model. Siren's code calls it the incentive resolver. Siren asks the app. "
        "An ask: Siren waits for the app to answer."
    ).strip()


def test_the_generated_code_matches_the_snapshot():
    assert generator().main(["--check"]) == 0


def test_on_says_a_feature_kind():
    assert "or an\n" not in (siren.AppRequestHandler.on.__doc__ or "")
    assert "a :class:`~siren.FeatureKind`" in " ".join((siren.AppRequestHandler.on.__doc__ or "").split())


# Finding 1 ---------------------------------------------------------------------


def _loose(tp: Any) -> bool:
    return tp == Dict[str, Any] or tp == dict or tp is Any


def _walk(tp: Any, path: str, seen: set, found: list) -> None:
    origin = typing.get_origin(tp)
    if origin is not None:
        for arg in typing.get_args(tp):
            _walk(arg, path, seen, found)
        return
    if isinstance(tp, type) and typing.is_typeddict(tp):
        if tp in seen:
            return
        seen.add(tp)
        for field, hint in get_type_hints(tp).items():
            where = f"{tp.__name__}.{field}"
            if _loose(hint):
                found.append(where)
            _walk(hint, where, seen, found)


def test_every_fixed_shape_is_a_typed_dict():
    # Siren sends a member's metadata as the group stores it, which has no
    # fixed shape (AppGroupStructureQuestion::members()).
    allowed = {"AppCollaboratorGroupMember.metadata"}
    found: list = []
    seen: set = set()
    for names in KIND_TYPES.values():
        for name in names:
            _walk(getattr(siren, name), name, seen, found)
    assert sorted(set(found) - allowed) == []


def test_scores_are_whole_numbers():
    # Engagement::getScore() and Metric::getScore() return int on Siren feat/apps.
    assert get_type_hints(siren.types.AppDistributionMetric)["score"] is int
    assert get_type_hints(siren.types.IncentiveResolverConversion)["score"] is int
    assert get_type_hints(siren.types.AppIncentiveTypeEngagement)["score"] is int
    assert get_type_hints(siren.types.ProgramGroupSorterEngagement)["score"] is int


@pytest.mark.parametrize(
    "name, outcomes",
    [
        ("DistributionResolverAnswer", {"allocated"}),
        ("IncentiveResolverAnswer", {"rewards"}),
        ("IncentiveTypeAnswer", {"award", "none"}),
        ("MetricCalculationAnswer", {"scores"}),
        ("MetricFilterAnswer", {"allow", "suppress"}),
        ("TransactionFilterAnswer", {"filtered"}),
        ("EngagementCalculationAnswer", {"engagements"}),
        ("EngagementFilterAnswer", {"allow", "suppress"}),
        ("ProgramGroupSorterAnswer", {"sorted"}),
        ("CollaboratorGroupStructureAnswer", {"structure"}),
        ("PayoutMethodAnswer", {"paid", "failed"}),
    ],
)
def test_every_outcome_is_a_literal(name, outcomes):
    tp = getattr(siren, name)
    members = typing.get_args(tp) if typing.get_origin(tp) is typing.Union else (tp,)
    seen = set()
    for member in members:
        hint = get_type_hints(member)["outcome"]
        assert typing.get_origin(hint) is typing.Literal, f"{member.__name__}.outcome is {hint}"
        seen.update(typing.get_args(hint))
    assert seen == outcomes


def test_question_reasons_are_literals():
    for name in ("IncentiveResolverQuestion", "IncentiveTypeQuestion"):
        hint = get_type_hints(getattr(siren, name))["reason"]
        assert typing.get_args(hint) == ("sale", "refund")


def test_the_request_is_generic_with_an_ask_and_a_tell():
    assert issubclass(siren.AppAsk, siren.AppRequest)
    assert issubclass(siren.AppTell, siren.AppRequest)
    assert siren.AppAsk[siren.DistributionResolverQuestion] is not None
    ask = Apps.verify_request(**_signed("distributionResolver"))
    tell = Apps.verify_request(**_signed("postConversionAction"))
    assert isinstance(ask, siren.AppAsk) and ask.is_ask
    assert isinstance(tell, siren.AppTell) and not tell.is_ask


def test_later_returns_the_public_marker_type():
    assert isinstance(handler().later(), siren.AppAnswerLater)


# Finding 3 ---------------------------------------------------------------------


def _signed(kind: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    request = testing.app_request(kind, "k", secret=SECRET, data=data)
    return {"raw_body": request.raw_body, "headers": request.headers, "secret": SECRET}


def test_a_conversion_with_no_reward_is_allowed():
    # AppActionSubjects::forConversion() sends reward null when the conversion has no obligation.
    hint = get_type_hints(siren.PostConversionActionTell)["reward"]
    assert type(None) in typing.get_args(hint)


def test_a_sale_with_no_currency_is_allowed():
    # AppTransactionFilterQuestion::build(): 'currency' => $first === null ? null : ...
    question = get_type_hints(siren.TransactionFilterQuestion)["transaction"]
    assert type(None) in typing.get_args(get_type_hints(question)["currency"])


@pytest.mark.parametrize("args", [[], {}])
def test_empty_engagement_args_read_as_an_empty_dict(args):
    data = testing.sample_data("engagementCalculation")
    data["args"] = args
    request = Apps.verify_request(**_signed("engagementCalculation", data))
    assert request.data["args"] == {}
    assert isinstance(request.data["args"], dict)


def test_set_engagement_args_are_kept():
    request = Apps.verify_request(**_signed("engagementCalculation"))
    assert request.data["args"] == {"weight": "2"}


def test_a_program_group_structure_ask_with_empty_args_reads_them_as_a_dict():
    data = testing.sample_data("collaboratorGroupStructure")
    data["for"] = {"type": "program", "id": 3}
    data["args"] = []
    request = Apps.verify_request(**_signed("collaboratorGroupStructure", data))
    assert request.data["args"] == {}


def test_an_answer_to_empty_args_runs_in_simulate():
    answers = handler()
    seen = []

    @answers.on("engagementCalculation")
    def weigh(request):
        seen.append(request.data["args"].get("weight", "1"))
        return {"outcome": "engagements", "results": []}

    data = testing.sample_data("engagementCalculation")
    data["args"] = []
    result = answers.simulate(testing.app_request("engagementCalculation", "k", secret=SECRET, data=data))
    assert result.error is None
    assert seen == ["1"]


# Finding 5 ---------------------------------------------------------------------


def test_app_handler_error_is_a_siren_error():
    assert issubclass(siren.AppHandlerError, siren.SirenError)
    assert not issubclass(siren.AppHandlerError, ValueError)


@pytest.mark.parametrize(
    "register",
    [
        lambda answers: answers.on("distributionResolvr", lambda request: None),
        lambda answers: answers.on("", lambda request: None),
        lambda answers: answers.on("distributionResolver", "", lambda request: None),
        lambda answers: answers.on("distributionResolver", "k", "not a function"),
        lambda answers: answers.on_event("", lambda event: None),
    ],
)
def test_a_registration_mistake_raises_app_handler_error(register):
    with pytest.raises(siren.AppHandlerError):
        register(handler())


def test_a_second_function_for_one_kind_and_key_raises_app_handler_error():
    answers = handler().on("distributionResolver", "k", lambda request: None)
    with pytest.raises(siren.AppHandlerError, match="already has a function"):
        answers.on("distributionResolver", "k", lambda request: None)


def test_a_bad_option_raises_app_handler_error():
    with pytest.raises(siren.AppHandlerError):
        handler(extra_kinds="newKind")
    with pytest.raises(siren.AppHandlerError):
        handler(on_error="log")


def test_a_bad_argument_to_simulate_raises_app_handler_error():
    with pytest.raises(siren.AppHandlerError, match="simulate"):
        handler().simulate({"not": "a request"})


def test_a_forgotten_return_is_an_app_handler_error():
    answers = handler().on("distributionResolver", lambda request: None)
    result = answers.simulate(testing.app_request("distributionResolver", "k", secret=SECRET))
    assert isinstance(result.error, siren.AppHandlerError)
    assert "returned nothing" in str(result.error)


def test_an_answer_the_handler_would_not_post_is_an_app_handler_error():
    answers = handler().on("distributionResolver", lambda request: {"units": "USD"})
    result = answers.simulate(testing.app_request("distributionResolver", "k", secret=SECRET))
    assert isinstance(result.error, siren.AppHandlerError)


def test_simulate_returns_the_same_four_fields_as_node_and_php():
    assert [field.name for field in dataclasses.fields(siren.AppSimulation)] == ["status", "body", "answer", "error"]


def test_simulate_answers_none_after_later_and_beside_a_refusal():
    answers = handler()
    answers.on("distributionResolver", "later", lambda request: answers.later())
    answers.on("distributionResolver", "refused", lambda request: {"outcome": "allocated", "units": "USD", "shares": {}})
    later = answers.simulate(testing.app_request("distributionResolver", "later", secret=SECRET))
    refused = answers.simulate(testing.app_request("distributionResolver", "refused", secret=SECRET))
    assert later.answer is None and later.error is None and later.body == {}
    assert refused.answer is None and isinstance(refused.error, siren.ValidationError)


def test_the_resend_hint_names_the_command_and_not_a_list_the_ask_is_not_on():
    # Siren Cloud offers Send again only once a request needs attention
    # (apps.ts resendAction), while POST /apps/requests/{id}/resend resends a
    # pending ask (ResendAppRequest.php), so the hint names the command.
    import siren.app_requests as module

    request = Apps.verify_request(**_signed("distributionResolver"))
    hint = module._next_send(ZeroDivisionError(), request, "ask")
    assert "Needs attention" not in hint
    assert hint.endswith(f"its answer window closes. Resend it sooner with `siren apps resend {request.id}`.")


# Parity: listing, reading and resending requests --------------------------------

ROW = {
    "id": REQUEST_ID,
    "appId": 3,
    "feature": "app:12",
    "kind": "distributionResolver",
    "mode": "ask",
    "subject": {"type": "distribution", "id": "21"},
    "status": "needs_attention",
    "sendCount": 3,
    "answerBy": "2026-11-24T18:00:00+00:00",
    "applyLease": None,
    "answeredBy": None,
    "answeredAt": None,
    "lastError": "The function failed.",
    "dropsAt": None,
    "createdAt": "2026-11-24T15:00:00+00:00",
}


def test_list_reads_the_orgs_requests(client, mocked_api):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": [ROW]}))
    assert client.apps.requests.list() == [ROW]
    assert route.calls.last.request.url.params == httpx.QueryParams({})


def test_list_filters_by_status(client, mocked_api):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    assert client.apps.requests.list(status="needs_attention") == []
    assert route.calls.last.request.url.params["status"] == "needs_attention"


def test_list_refuses_a_status_siren_does_not_know(client, mocked_api):
    with pytest.raises(ValueError, match="needs_attention"):
        client.apps.requests.list(status="stuck")


def test_list_refuses_a_body_with_no_list(client, mocked_api):
    mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"rows": []}))
    with pytest.raises(siren.ApiError):
        client.apps.requests.list()


def test_retrieve_reads_one_request_with_its_data_and_answer(client, mocked_api):
    detail = {**ROW, "data": {"pool": {}}, "answer": None, "answeredByUser": None}
    mocked_api.get(f"/apps/requests/{REQUEST_ID}").mock(return_value=httpx.Response(200, json=detail))
    assert client.apps.requests.retrieve(REQUEST_ID) == detail


def test_retrieve_refuses_another_requests_body(client, mocked_api):
    other = {**ROW, "id": "apr_" + "0" * 32, "data": {}, "answer": None, "answeredByUser": None}
    mocked_api.get(f"/apps/requests/{REQUEST_ID}").mock(return_value=httpx.Response(200, json=other))
    with pytest.raises(siren.ApiError):
        client.apps.requests.retrieve(REQUEST_ID)


def test_resend_posts_once_and_returns_the_request(client, mocked_api):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/resend").mock(return_value=httpx.Response(200, json=ROW))
    assert client.apps.requests.resend(REQUEST_ID) == ROW
    assert route.call_count == 1


def test_resend_is_not_retried(client, mocked_api):
    route = mocked_api.post(f"/apps/requests/{REQUEST_ID}/resend").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(siren.SirenError):
        client.apps.requests.resend(REQUEST_ID)
    assert route.call_count == 1


def test_resend_raises_conflict_for_a_request_siren_will_not_send(client, mocked_api):
    refusal = {"error": {"code": "app_request_not_resendable", "message": "It was applied.", "context": {}}}
    mocked_api.post(f"/apps/requests/{REQUEST_ID}/resend").mock(return_value=httpx.Response(409, json=refusal))
    with pytest.raises(siren.ConflictError):
        client.apps.requests.resend(REQUEST_ID)


@pytest.mark.parametrize("method", ["retrieve", "resend"])
def test_a_bad_request_id_is_refused_before_sending(client, mocked_api, method):
    with pytest.raises(ValueError, match="apr_"):
        getattr(client.apps.requests, method)("req_1")


def test_the_statuses_are_sirens():
    # lib/Apps/Core/Models/AppRequest.php on Siren feat/apps.
    assert siren.APP_REQUEST_STATUSES == (
        "pending", "sent", "delivered", "answered", "applied", "needs_attention", "dropped",
    )


def test_the_readme_drops_needs_attention_from_the_hint_and_shows_the_new_surface():
    readme = " ".join((ROOT / "README.md").read_text().split())
    assert "Resend it sooner from Needs attention" not in readme
    assert "resend it sooner from Needs attention" not in readme
    assert "Resend it sooner with `siren apps resend apr_b339...`." in readme
    assert "client.apps.requests.list(status=" in readme and "client.apps.requests.resend(" in readme
    assert "AppAsk[DistributionResolverQuestion]" in readme
    assert "| `AppHandlerError` |" in readme
