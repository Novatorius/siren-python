"""DX pass 2, finding 1: ``on()`` refuses a kind Siren never sends, when the
function is registered, and says what to write instead. Before this, a typo
registered quietly and every real request got a 404 for hours."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import siren
from siren import FeatureKind

CONTRACTS = json.loads((Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
SECRET = "cd" * 32

# Siren feat/apps, frontend/src/platform/saas/apps/apps.ts, APP_KINDS: each
# kind with its on-screen name, a witness kept apart from the snapshot the
# SDK ships. customEvent is the one kind Siren never sends.
SIREN_APP_KINDS = [
    ("postConversionAction", "Post-conversion action"),
    ("postAllocationAction", "Post-allocation action"),
    ("payoutMethod", "Payout method"),
    ("distributionResolver", "Distribution resolver"),
    ("incentiveResolver", "Attribution model"),
    ("incentiveType", "Incentive structure"),
    ("metricCalculation", "Metric calculation"),
    ("metricFilter", "Customer filter for distributors"),
    ("transactionFilter", "Transaction filter"),
    ("engagementCalculation", "Engagement calculation"),
    ("engagementFilter", "Customer filter for programs"),
    ("programGroupSorter", "Program group structure"),
    ("collaboratorGroupStructure", "Collaborator group structure"),
    ("customEvent", "Custom event"),
]


def handler(**options):
    return siren.Siren(api_key="sk_test").apps.request_handler(SECRET, **options)


def noop(request):
    return None


def test_the_kinds_the_sdk_takes_are_the_kinds_siren_sends():
    sent = [kind for kind, _ in SIREN_APP_KINDS if kind != "customEvent"]
    assert sorted(siren.APP_REQUEST_KINDS) == sorted(sent)
    assert sorted(siren.APP_REQUEST_KINDS) == sorted(CONTRACTS["kinds"])
    assert sorted(siren.APP_REQUEST_KINDS) == sorted(member.value for member in FeatureKind if member is not FeatureKind.CUSTOM_EVENT)


@pytest.mark.parametrize("kind", [kind for kind, _ in SIREN_APP_KINDS if kind != "customEvent"])
def test_every_kind_siren_sends_registers(kind):
    answers = handler()
    assert answers.on(kind, "my-feature", noop) is answers
    assert answers.on(kind, noop) is answers


def test_an_enum_member_registers_and_is_found_by_its_string():
    seen = []
    answers = handler().on(FeatureKind.POST_CONVERSION_ACTION, "tell-the-crm", lambda request: seen.append(request.id))
    from siren import testing

    result = answers.simulate(testing.app_request("postConversionAction", "tell-the-crm", secret=SECRET))
    assert result.status == 200 and len(seen) == 1


@pytest.mark.parametrize(
    "kind, words",
    [
        ("distributionResolvr", "distributionResolvr is not a feature kind. Did you mean distributionResolver?"),
        ("DistributionResolver", "DistributionResolver is not a feature kind. Did you mean distributionResolver?"),
        ("payoutmethod", "payoutmethod is not a feature kind. Did you mean payoutMethod?"),
        ("metricFiltr", "metricFiltr is not a feature kind. Did you mean metricFilter?"),
        ("attributionModel", "The kind for Attribution model is incentiveResolver."),
        ("Attribution model", "The kind for Attribution model is incentiveResolver."),
        ("incentive structure", "The kind for Incentive structure is incentiveType."),
        ("customerFilterForPrograms", "The kind for Customer filter for programs is engagementFilter."),
        ("programGroupStructure", "The kind for Program group structure is programGroupSorter."),
        ("distribution_resolver", "Kinds are camelCase, as Siren sends them: distributionResolver."),
        ("collaborator_group_structure", "Kinds are camelCase, as Siren sends them: collaboratorGroupStructure."),
        ("customEvent", "Siren never sends a custom event. Your app sends one with events.ingest()."),
        ("conversion.approved", "conversion.approved is a webhook event. Register it with on_event()."),
        ("fulfillmentMethod", "fulfillmentMethod is the old name. Use payoutMethod."),
    ],
)
def test_a_kind_siren_never_sends_is_refused_with_what_to_write(kind, words):
    answers = handler()
    with pytest.raises(ValueError) as refused:
        answers.on(kind, "my-feature", noop)
    assert str(refused.value) == words
    # The catch-all and the decorator forms refuse it too, before any function.
    with pytest.raises(ValueError, match="kind|event|name|camelCase"):
        answers.on(kind, noop)
    with pytest.raises(ValueError, match="kind|event|name|camelCase"):
        answers.on(kind, "my-feature")


def test_a_name_far_from_every_kind_lists_the_kinds():
    with pytest.raises(ValueError) as refused:
        handler().on("refundPolicy", "my-feature", noop)
    assert str(refused.value) == "refundPolicy is not a feature kind. The kinds are: " + ", ".join(siren.APP_REQUEST_KINDS) + "."


def test_the_custom_event_member_is_refused_too():
    with pytest.raises(ValueError, match="events.ingest"):
        handler().on(FeatureKind.CUSTOM_EVENT, "my-feature", noop)


def test_extra_kinds_lets_a_kind_a_newer_siren_sends_register():
    answers = handler(extra_kinds=["refundPolicy"])
    assert answers.on("refundPolicy", "my-feature", noop) is answers
    with pytest.raises(ValueError, match="distributionResolvr is not a feature kind"):
        answers.on("distributionResolvr", "my-feature", noop)


@pytest.mark.parametrize("extra", ["refundPolicy", [""], [3], ["has.dot"]])
def test_extra_kinds_takes_a_list_of_kind_names(extra):
    with pytest.raises(ValueError, match="extra_kinds"):
        handler(extra_kinds=extra)
