"""``Apps.split_pool()``: shares of a pool in whole steps of its currency,
rounded down so the sum never passes the pool, split evenly when every weight
is 0. The two resolver answers come from Siren's feature contracts fixture
(tests/fixtures/apps-feature-contracts/requests.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from siren.apps import Apps

CONTRACTS = json.loads(
    (Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text()
)
USD = {"id": "USD", "symbol": "$", "position": "before", "name": "US Dollar", "scale": 2, "decimalPlaces": 2, "type": "monetary"}
JPY = {"id": "JPY", "symbol": "¥", "position": "before", "name": "Japanese Yen", "scale": 2, "decimalPlaces": 0, "type": "monetary"}


def pool(value: int, currency: dict = USD) -> dict:
    return {"value": value, "units": currency["id"], "currency": currency}


def test_the_distribution_resolver_fixture_gets_its_answer():
    question = CONTRACTS["kinds"]["distributionResolver"]["request"]["data"]

    shares = Apps.split_pool(question["pool"], {m["metricId"]: m["score"] for m in question["metrics"]})

    assert shares == CONTRACTS["kinds"]["distributionResolver"]["answer"]["shares"]
    assert sum(shares.values()) <= question["pool"]["value"]


def test_the_incentive_resolver_fixture_gets_its_answer_from_pairs():
    question = CONTRACTS["kinds"]["incentiveResolver"]["request"]["data"]

    rewards = Apps.split_pool(question["pool"], [(c["conversionId"], c["score"]) for c in question["conversions"]])

    assert rewards == CONTRACTS["kinds"]["incentiveResolver"]["answer"]["rewards"]


def test_a_dollar_pool_rounds_each_share_down_to_a_cent():
    shares = Apps.split_pool(pool(10000), {"a": 1, "b": 1, "c": 1})

    assert shares == {"a": 3333, "b": 3333, "c": 3333}
    assert sum(shares.values()) == 9999 <= 10000


def test_a_yen_pool_pays_whole_yen_only():
    # Scale 2 and no decimal places: a yen is 100 in the stored form.
    shares = Apps.split_pool(pool(10050, JPY), {"41": 1, "43": 2})

    assert shares == {"41": 3300, "43": 6600}
    assert all(amount % 100 == 0 for amount in shares.values())
    assert sum(shares.values()) <= 10050


def test_all_zero_weights_split_evenly():
    shares = Apps.split_pool(pool(1000), {7: 0, 8: 0, 9: 0.0})

    assert shares == {"7": 333, "8": 333, "9": 333}
    assert sum(shares.values()) <= 1000


def test_all_zero_weights_split_a_yen_pool_evenly_in_whole_yen():
    shares = Apps.split_pool(pool(1000, JPY), {7: 0, 8: 0, 9: 0})

    assert shares == {"7": 300, "8": 300, "9": 300}


def test_a_zero_weight_beside_others_gets_zero():
    assert Apps.split_pool(pool(900), {1: 0, 2: 3}) == {"1": 0, "2": 900}


def test_fractional_weights_never_pass_the_pool():
    weights = {i: 0.1 for i in range(1, 11)}

    shares = Apps.split_pool(pool(1000), weights)

    assert sum(shares.values()) <= 1000
    assert set(shares.values()) == {100}


def test_no_weights_give_no_shares():
    assert Apps.split_pool(pool(1000), {}) == {}


def test_a_pool_without_a_currency_steps_by_one():
    assert Apps.split_pool({"value": 10, "units": "points"}, {1: 1, 2: 2}) == {"1": 3, "2": 6}


@pytest.mark.parametrize("weight", [-1, -0.5, float("nan"), float("inf"), float("-inf")])
def test_a_negative_or_non_finite_weight_is_refused(weight):
    with pytest.raises(ValueError, match="split_pool"):
        Apps.split_pool(pool(1000), {1: 1, 2: weight})


@pytest.mark.parametrize("weight", [True, "3", None])
def test_a_weight_that_is_not_a_number_is_refused(weight):
    with pytest.raises(ValueError, match="split_pool"):
        Apps.split_pool(pool(1000), {1: weight})


@pytest.mark.parametrize(
    "bad_pool",
    [None, {"units": "USD"}, {"value": -1}, {"value": 10.5}, {"value": True}, {"value": 10, "currency": {"scale": "2"}}],
)
def test_a_pool_without_a_whole_value_is_refused(bad_pool):
    with pytest.raises(ValueError, match="split_pool"):
        Apps.split_pool(bad_pool, {1: 1})


@pytest.mark.parametrize("weights", ["ab", [(1, 1), (1, 2)], [(1,)], [(True, 1)]])
def test_weights_that_are_not_ids_and_numbers_are_refused(weights):
    with pytest.raises(ValueError, match="split_pool"):
        Apps.split_pool(pool(1000), weights)


def test_it_is_reachable_from_a_client(client):
    assert client.apps.split_pool(pool(100), {1: 1}) == {"1": 100}
