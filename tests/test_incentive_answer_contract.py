"""Slice C16-2: the answer the request handler's incentive resolver sample
builds with ``split_pool()`` passes Siren's answer check for the kind.

The question is Siren's own fixture (tests/Fixtures/apps-feature-contracts/
requests.json on Siren's pkt/apps-c16-2), the same file this SDK copies.
``refusal_of()`` holds the rules of Siren's AppRewardQuestion::validate(), in
its order, and Siren's AppFeatureContractsTest runs the fixture's answer
through validate() itself. This test checks the sample's answer equals that
answer and meets the same rules, so a change on either side fails here."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from siren.apps import Apps

CONTRACTS = json.loads(
    (Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text()
)


def refusal_of(question: dict, answer: dict) -> str | None:
    """Siren's AppRewardQuestion::validate(): why it refuses the answer with 422, or None."""
    if answer.get("outcome") != "rewards":
        return "outcome"
    if answer.get("units") != question["pool"]["units"]:
        return "units"
    rewards = answer.get("rewards")
    if not isinstance(rewards, dict):
        return "rewards"
    asked = [conversion["conversionId"] for conversion in question["conversions"]]
    for key, value in rewards.items():
        if not str(key).isdigit() or int(key) not in asked or str(int(key)) != str(key):
            return f"unknown {key}"
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            return f"not whole {key}"
        if value is not None and value > question["pool"]["value"]:
            return f"above the pool {key}"
    for conversion_id in asked:
        if str(conversion_id) not in rewards and conversion_id not in rewards:
            return f"missing {conversion_id}"
    return None


def test_the_split_pool_sample_answer_passes_sirens_check():
    kind = CONTRACTS["kinds"]["incentiveResolver"]
    data = kind["request"]["data"]

    # The sample's function, section 5.4, as the README and
    # test_answer_handler_samples.py write it.
    def split_the_reward(request):
        pool, conversions = request.data["pool"], request.data["conversions"]
        rewards = Apps.split_pool(pool, {c["conversionId"]: c["score"] for c in conversions})
        return {"outcome": "rewards", "units": pool["units"], "rewards": rewards}

    # As the handler posts it, JSON encoded.
    answer = json.loads(json.dumps(split_the_reward(SimpleNamespace(data=data))))

    assert refusal_of(data, answer) is None
    assert answer == kind["answer"]

    answer["rewards"]["1042"] = data["pool"]["value"] + 1
    assert refusal_of(data, answer) == "above the pool 1042"
