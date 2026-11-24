"""The review's two typos, which mypy --strict must catch: a misspelled
question field and a misspelled outcome. tests/test_typecheck.py runs it."""

import siren

answers = siren.Siren(api_key="sk_test").apps.request_handler("whsec_test")


@answers.on("distributionResolver", "split-by-score")
def split(request: siren.AppAsk[siren.DistributionResolverQuestion]) -> siren.DistributionResolverAnswer:
    pool = request.data["pools"]  # typo: pool
    return {"outcome": "allocate", "units": pool["units"], "shares": {}}  # typo: allocated
