"""The payout method's question and answer types, held to Siren's
AppPayoutQuestion builder (lib/Apps/Service/Payouts/AppPayoutQuestion.php on
Siren's feat/apps) through its fixture, and to Node's PayoutMethodQuestion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Union, get_type_hints

import siren

CONTRACTS = json.loads((Path(__file__).resolve().parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
PAYOUT = CONTRACTS["kinds"]["payoutMethod"]


def test_the_payout_types_are_exported():
    for name in ("PayoutMethodQuestion", "PayoutMethodPaidAnswer", "PayoutMethodFailedAnswer", "PayoutMethodAnswer"):
        assert name in siren.__all__, name
        assert getattr(siren, name) is getattr(siren.types, name)


def test_the_question_has_the_keys_siren_sends():
    hints = get_type_hints(siren.PayoutMethodQuestion)
    assert set(hints) == {"payout", "amount", "collaborator"} == set(PAYOUT["request"]["data"])
    assert hints["amount"] is siren.AppAmount
    assert set(siren.AppAmount.__annotations__) == set(PAYOUT["request"]["data"]["amount"])


def test_the_answer_is_paid_with_a_reference_or_failed_with_a_reason():
    assert siren.PayoutMethodAnswer == Union[siren.PayoutMethodPaidAnswer, siren.PayoutMethodFailedAnswer]
    assert get_type_hints(siren.PayoutMethodPaidAnswer) == {"outcome": Literal["paid"], "reference": str}
    assert get_type_hints(siren.PayoutMethodFailedAnswer) == {"outcome": Literal["failed"], "reason": str}
    assert set(siren.PayoutMethodPaidAnswer.__annotations__) == set(PAYOUT["answer"])
