"""A function whose annotation names another kind's question, which mypy
--strict must refuse. tests/test_typecheck.py runs it."""

import siren

answers = siren.Siren(api_key="sk_test").apps.request_handler("whsec_test")


@answers.on("distributionResolver", "split-by-score")  # wrong kind's question
def split(request: siren.AppAsk[siren.PayoutMethodQuestion]) -> siren.PayoutMethodAnswer:
    return {"outcome": "paid", "reference": "TX-1"}
