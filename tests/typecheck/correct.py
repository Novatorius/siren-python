"""The same function spelled right, which mypy --strict must pass, with the
other forms on() takes. tests/test_typecheck.py runs it."""

from typing import Union

import siren

answers = siren.Siren(api_key="sk_test").apps.request_handler("whsec_test")


@answers.on("distributionResolver", "split-by-score")
def split(request: siren.AppAsk[siren.DistributionResolverQuestion]) -> siren.DistributionResolverAnswer:
    pool = request.data["pool"]
    return {"outcome": "allocated", "units": pool["units"], "shares": {}}


@answers.on(siren.FeatureKind.PAYOUT_METHOD)
def pay(request: siren.AppAsk[siren.PayoutMethodQuestion]) -> Union[siren.PayoutMethodAnswer, siren.AppAnswerLater]:
    if request.data["collaborator"]["email"] is None:
        return answers.later()
    return {"outcome": "paid", "reference": f"TX-{request.data['payout']['id']}"}


@answers.on("postConversionAction")
def tell(request: siren.AppTell[siren.PostConversionActionTell]) -> None:
    reward = request.data["reward"]
    print(0 if reward is None else reward["value"])


answers.on(
    "metricFilter",
    "refund-check",
    lambda request: {"outcome": "allow"} if request.data["sale"] is None else {"outcome": "suppress", "reason": "Refunded."},
)
answers.on("someKindFromANewerSiren", lambda request: {"outcome": "done"})
