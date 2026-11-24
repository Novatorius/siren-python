"""DX pass 2, carried item 2: ``simulate()`` checks an ask's answer the way
Siren's answer route does, so a test sees Siren's 422 before Siren does. The
error is a ``ValidationError`` with ``code`` ``app_answer_invalid``,
``status_code`` 422 and Siren's own words.

Each case below names the Siren rule it pins, on Siren feat/apps
(6d2abd936a). The live handler is unchanged: it posts, and Siren's 422 reaches
``on_error``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import siren
from siren import testing

CONTRACTS = json.loads((Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
SECRET = "cd" * 32
ASKS = sorted(kind for kind, entry in CONTRACTS["kinds"].items() if entry["answer"] is not None)


def simulate(kind, answer, data=None):
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)
    answers.on(kind, "my-feature", lambda request: answer)
    return answers.simulate(testing.app_request(kind, "my-feature", secret=SECRET, data=data))


def refusal(kind, answer, data=None):
    result = simulate(kind, answer, data)
    assert result.status == 200 and result.answer is None, "a refused answer is not shown as posted"
    error = result.error
    assert isinstance(error, siren.ValidationError), error
    assert error.code == siren.ErrorCode.APP_ANSWER_INVALID == "app_answer_invalid"
    assert error.status_code == 422
    return str(error)


@pytest.mark.parametrize("kind", ASKS)
def test_sirens_own_answer_for_each_kind_passes(kind):
    result = simulate(kind, CONTRACTS["kinds"][kind]["answer"])
    assert result.error is None and result.answer == CONTRACTS["kinds"][kind]["answer"]


# lib/Apps/Service/Services/AppRequestService.php, answer(): the size limit,
# measured on json_encode() of the decoded answer, which escapes / and every
# character past ASCII.
def test_an_answer_over_16384_bytes_as_siren_encodes_it():
    reason = "é" * 2800  # 5600 bytes as UTF-8, 16800 as Siren's é escapes
    assert refusal("metricFilter", {"outcome": "suppress", "reason": reason}) == "The answer is larger than 16384 bytes of JSON."


DISTRIBUTION = [
    # lib/Apps/Service/Resolvers/AppDistributionQuestion.php, unfitness(), rules 1 to 9.
    ({"outcome": "allocate", "units": "USD", "shares": {}}, "outcome must be allocated"),
    ({"outcome": "allocated", "units": "usd", "shares": {}}, "units must be USD, the units of the pool"),
    ({"outcome": "allocated", "units": "USD", "shares": 10000}, "shares must be an object of metric id to amount"),
    ({"outcome": "allocated", "units": "USD", "shares": {"41": 6000, "99": 0}}, "shares names metric 99, which this distribution does not hold"),
    ({"outcome": "allocated", "units": "USD", "shares": {"041": 6000, "43": 0}}, "shares names metric 041, which this distribution does not hold"),
    ({"outcome": "allocated", "units": "USD", "shares": [6000, 4000]}, "shares names metric 0, which this distribution does not hold"),
    ({"outcome": "allocated", "units": "USD", "shares": {"41": 60.5, "43": 0}}, "the share for metric 41 must be a whole number of the smallest unit"),
    ({"outcome": "allocated", "units": "USD", "shares": {"41": True, "43": 0}}, "the share for metric 41 must be a whole number of the smallest unit"),
    ({"outcome": "allocated", "units": "USD", "shares": {"41": -1, "43": 0}}, "the share for metric 41 must not be below 0"),
    ({"outcome": "allocated", "units": "USD", "shares": {"41": 6000}}, "shares has no amount for metric 43, and every metric needs one, 0 included"),
    ({"outcome": "allocated", "units": "USD", "shares": {"41": 6000, "43": 4001}}, "the shares add up to 10001, more than the pool of 10000"),
]


@pytest.mark.parametrize("answer, words", DISTRIBUTION)
def test_distribution_resolver(answer, words):
    assert refusal("distributionResolver", answer) == words


def test_distribution_resolver_whole_currency_steps():
    # unfitness(), rule 7: a yen pool, scale 2 and 0 decimal places, steps by 100.
    data = testing.sample_data("distributionResolver")
    data["pool"].update({"units": "JPY", "currency": {"id": "JPY", "scale": 2, "decimalPlaces": 0}})
    answer = {"outcome": "allocated", "units": "JPY", "shares": {"41": 6050, "43": 0}}
    assert refusal("distributionResolver", answer, data) == "the share for metric 41 must be a multiple of 100, as JPY amounts have no smaller part"


REWARDS = [
    # lib/Apps/Service/Rewards/AppRewardQuestion.php, validate().
    ({"outcome": "reward"}, 'The answer\'s outcome must be "rewards".'),
    ({"outcome": "rewards", "units": "EUR", "rewards": {}}, 'The answer\'s units must be "USD", the units of the pool Siren offered.'),
    ({"outcome": "rewards", "units": "USD", "rewards": [900, 600]}, "The answer's rewards must be an object with one key per conversion id in the question."),
    ({"outcome": "rewards", "units": "USD", "rewards": {"0": 900, "1": 600}}, "The answer's rewards must be an object with one key per conversion id in the question."),
    ({"outcome": "rewards", "units": "USD", "rewards": {"7": 900}}, "The answer names conversion 7, which the question does not hold."),
    ({"outcome": "rewards", "units": "USD", "rewards": {"1042": -5, "1043": 0}}, "The reward for conversion 1042 must be null or a whole number at or above 0, in the stored integer form."),
    ({"outcome": "rewards", "units": "USD", "rewards": {"1042": 1501, "1043": 0}}, "The reward for conversion 1042 is 1501, more than the pool of 1500 Siren offered."),
    ({"outcome": "rewards", "units": "USD", "rewards": {"1042": 900}}, "The answer has no reward for conversion 1043. Give every conversion of the question a key, null for one the app did not choose."),
]


@pytest.mark.parametrize("answer, words", REWARDS)
def test_attribution_model(answer, words):
    assert refusal("incentiveResolver", answer) == words


def test_attribution_model_takes_null_for_a_conversion_it_did_not_choose():
    assert simulate("incentiveResolver", {"outcome": "rewards", "units": "USD", "rewards": {"1042": 900, "1043": None}}).error is None


INCENTIVE_TYPE = [
    # lib/Apps/Service/IncentiveTypes/AppIncentiveTypeQuestion.php, validate().
    ({"outcome": "none", "pool": 0}, 'An answer of "none" carries nothing else, and this one has pool.'),
    ({"outcome": "awarded"}, 'The answer\'s outcome must be "award" or "none".'),
    ({"outcome": "award", "units": "EUR"}, 'The answer\'s units must be "USD", the program\'s units.'),
    ({"outcome": "award", "units": "USD", "pool": 15.5, "engagementIds": [880]}, "The answer's pool must be a whole number at or above 0, in the stored integer form."),
    ({"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": []}, "The answer's engagementIds must be a list of at least one engagement id from the question."),
    ({"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": [880, 7]}, "The answer names engagement 7, which the question does not hold."),
    ({"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": [880, "881"]}, "The answer names engagement 881, which the question does not hold."),
    ({"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": [880, 880]}, "The answer names engagement 880 more than once."),
    ({"outcome": "award", "units": "USD", "pool": 1500, "engagementIds": [880], "note": "x"}, "The answer has fields Siren does not read: note."),
]


@pytest.mark.parametrize("answer, words", INCENTIVE_TYPE)
def test_incentive_structure(answer, words):
    assert refusal("incentiveType", answer) == words


def test_incentive_structure_takes_none():
    assert simulate("incentiveType", {"outcome": "none"}).error is None


CALCULATIONS = [
    # lib/Apps/Service/Handlers/AppMetricCalculationAnswerHandler.php and
    # lib/Apps/Service/Engagements/AppEngagementCalculationAnswerHandler.php, validate().
    ({"results": "x"}, "results must be a list of {collaboratorId, score}."),
    ({"results": [{"collaboratorId": i, "score": 1} for i in range(1, 52)]}, "results may hold at most 50 entries, and this holds 51."),
    ({"results": [{"collaboratorId": 0, "score": 1}]}, "results[0].collaboratorId must be a collaborator id."),
    ({"results": [{"collaboratorId": 42, "score": 1}, {"collaboratorId": 57, "score": -1}]}, "results[1].score must be a whole number of 0 or more."),
    ({"results": [{"collaboratorId": 42, "score": 1}, {"collaboratorId": 42, "score": 2}]}, "results names collaborator 42 more than once."),
]


@pytest.mark.parametrize("kind, outcome", [("metricCalculation", "scores"), ("engagementCalculation", "engagements")])
@pytest.mark.parametrize("rest, words", CALCULATIONS)
def test_calculations(kind, outcome, rest, words):
    assert refusal(kind, {"outcome": outcome, **rest}) == words


@pytest.mark.parametrize("kind, outcome", [("metricCalculation", "scores"), ("engagementCalculation", "engagements")])
def test_calculations_name_their_outcome(kind, outcome):
    assert refusal(kind, {"outcome": "score", "results": []}) == f"outcome must be {outcome}."
    assert simulate(kind, {"outcome": outcome, "results": []}).error is None


FILTERS = [
    # lib/Apps/Service/Handlers/AppMetricFilterAnswerHandler.php and
    # lib/Apps/Service/Engagements/AppEngagementFilterAnswerHandler.php, validate().
    ({"outcome": "block"}, "outcome must be allow or suppress."),
    ({"outcome": "suppress"}, "A suppress answer gives a reason, in plain text."),
    ({"outcome": "suppress", "reason": "   "}, "A suppress answer gives a reason, in plain text."),
    ({"outcome": "suppress", "reason": "r" * 201}, "reason may be at most 200 characters, and this is 201."),
    ({"outcome": "suppress", "reason": "It was <b>refunded</b>."}, "reason must be plain text, with no markup or control characters."),
    ({"outcome": "suppress", "reason": "Refunded.\nTwice."}, "reason must be plain text, with no markup or control characters."),
]


@pytest.mark.parametrize("kind", ["metricFilter", "engagementFilter"])
@pytest.mark.parametrize("answer, words", FILTERS)
def test_filters(kind, answer, words):
    assert refusal(kind, answer) == words


GROUP_ORDER = [
    # lib/Apps/Service/GroupOrders/AppGroupOrderQuestion.php, validate().
    ({"outcome": "sort"}, "outcome must be sorted."),
    ({"outcome": "sorted", "programIds": {"9": 1}}, "programIds must be a list of the question's program ids."),
    ({"outcome": "sorted", "programIds": [9, "12"]}, "Each program id is a whole number."),
    ({"outcome": "sorted", "programIds": [9, 12, 4]}, "Program 4 is not one of the programs Siren asked about."),
    ({"outcome": "sorted", "programIds": [9, 9, 12]}, "Program 9 is listed more than once."),
    ({"outcome": "sorted", "programIds": [9]}, "The answer leaves out program 12. List every program Siren asked about."),
]


@pytest.mark.parametrize("answer, words", GROUP_ORDER)
def test_program_group_structure(answer, words):
    assert refusal("programGroupSorter", answer) == words


TRANSACTION_FILTER = [
    # lib/Apps/Service/TransactionFilters/AppTransactionFilterQuestion.php, validate().
    ({"outcome": "filter"}, 'The answer\'s outcome must be "filtered".'),
    ({"outcome": "filtered", "include": 5501}, "The answer's include must be a list of line ids from the question."),
    ({"outcome": "filtered", "include": [5501.0]}, "Each id in the answer's include must be a whole number, a line id from the question."),
    ({"outcome": "filtered", "include": [5503]}, "The answer includes line 5503, which is not a line of the sale Siren asked about."),
    ({"outcome": "filtered", "include": [5501, 5501]}, "The answer includes line 5501 more than once."),
]


@pytest.mark.parametrize("answer, words", TRANSACTION_FILTER)
def test_transaction_filter(answer, words):
    assert refusal("transactionFilter", answer) == words


def test_transaction_filter_takes_an_empty_list_and_an_empty_object():
    # array_is_list([]) is true, and PHP decodes {} as [].
    assert simulate("transactionFilter", {"outcome": "filtered", "include": []}).error is None
    assert simulate("transactionFilter", {"outcome": "filtered", "include": {}}).error is None


GROUP_STRUCTURE = [
    # lib/Apps/Service/Handlers/AppGroupStructureAnswerHandler.php, problem(),
    # chainProblem() and treeProblem().
    ({"outcome": "structured"}, "outcome must be structure."),
    ({"outcome": "structure", "shape": "ring"}, "shape must be chain or tree."),
    ({"outcome": "structure", "shape": "chain", "order": {"1": 63}}, "order must be a list of the members' collaborator ids, top first."),
    ({"outcome": "structure", "shape": "chain", "order": [63, "57", 42]}, "order[1] must be a collaborator id."),
    ({"outcome": "structure", "shape": "chain", "order": [63, 57, 9]}, "order[2] names collaborator 9, who is not a member in the question."),
    ({"outcome": "structure", "shape": "chain", "order": [63, 63, 57, 42]}, "order names collaborator 63 more than once."),
    ({"outcome": "structure", "shape": "chain", "order": [63, 42]}, "order leaves out member 57. List every member of the question once."),
    ({"outcome": "structure", "shape": "tree", "parents": "63"}, "parents must be an object of each member's collaborator id to its parent's, or null at the top."),
    ({"outcome": "structure", "shape": "tree", "parents": {"9": None}}, "parents names 9, which is not a member in the question."),
    ({"outcome": "structure", "shape": "tree", "parents": [None, 63]}, "parents names 0, which is not a member in the question."),
    ({"outcome": "structure", "shape": "tree", "parents": {"63": None, "57": "63"}}, 'parents["57"] must be a member\'s collaborator id or null.'),
    ({"outcome": "structure", "shape": "tree", "parents": {"63": None, "57": 9}}, 'parents["57"] names collaborator 9, who is not a member in the question.'),
    ({"outcome": "structure", "shape": "tree", "parents": {"63": 63}}, 'parents["63"] names the member as their own parent.'),
    ({"outcome": "structure", "shape": "tree", "parents": {"63": None, "57": 63}}, "parents leaves out member 42. Name every member of the question once, with null at the top."),
    ({"outcome": "structure", "shape": "tree", "parents": {"63": None, "57": 42, "42": 57}}, "parents form a cycle through member 57. Every member must reach the top."),
]


@pytest.mark.parametrize("answer, words", GROUP_STRUCTURE)
def test_collaborator_group_structure(answer, words):
    assert refusal("collaboratorGroupStructure", answer) == words


def test_collaborator_group_structure_takes_a_tree():
    tree = {"outcome": "structure", "shape": "tree", "parents": {"63": None, "57": 63, "42": 57}}
    assert simulate("collaboratorGroupStructure", tree).error is None


PAYOUT = [
    # lib/Apps/Service/Payouts/AppPayoutQuestion.php, validate().
    ({"outcome": "sent"}, 'The outcome is "paid" or "failed".'),
    ({"outcome": "paid"}, 'A paid answer names the payment\'s reference in your system, as "reference".'),
    ({"outcome": "paid", "reference": " "}, 'A paid answer names the payment\'s reference in your system, as "reference".'),
    ({"outcome": "paid", "reference": "R" * 192}, "A reference is at most 191 characters."),
    ({"outcome": "failed", "reason": 7}, 'A failed answer says why in plain words, as "reason".'),
    ({"outcome": "failed", "reason": "x" * 501}, "A reason is at most 500 characters."),
]


@pytest.mark.parametrize("answer, words", PAYOUT)
def test_payout_method(answer, words):
    assert refusal("payoutMethod", answer) == words


def test_payout_method_takes_a_failed_answer():
    assert simulate("payoutMethod", {"outcome": "failed", "reason": "The account is closed."}).error is None


def test_the_live_handler_still_posts_and_leaves_the_refusal_to_siren(mocked_api):
    # The check runs in simulate() only. The handler posts, as before, and
    # Siren's own 422 reaches on_error.
    route = mocked_api.post(url__regex=r"/apps/requests/apr_[0-9a-f]{32}/answer").respond(
        422, json={"error": {"code": "app_answer_invalid", "message": "outcome must be allow or suppress."}}
    )
    seen = []
    with siren.Siren(api_key="sk_test") as client:
        answers = client.apps.request_handler(SECRET, on_error=lambda error, request: seen.append(error))
        answers.on("metricFilter", "my-feature", lambda request: {"outcome": "block"})
        request = testing.app_request("metricFilter", "my-feature", secret=SECRET)
        answers(request.raw_body, request.headers).after()

    assert route.call_count == 1
    [error] = seen
    assert isinstance(error, siren.ValidationError) and error.code == "app_answer_invalid"
