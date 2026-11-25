"""``apps.requests.list(id_prefix=...)`` finds requests by the start of their
id, as ListAppRequests.php on Siren feat/apps 76d5bbdf83 reads ``idPrefix``:
``apr_`` and 4 to 32 hex characters, at most 2 of the organization's matches,
newest first, with the other filters ignored."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

ROW = {"id": "apr_9f2c" + "0" * 28, "feature": "app:12", "status": "applied"}
REFUSAL = "apps.requests.list() takes an id_prefix of apr_ and 4 to 32 hex characters, the start of a request id"
ALONE = "apps.requests.list() finds by id_prefix alone. Siren ignores status, limit and before beside it, so leave them out."


def test_id_prefix_reaches_the_query_and_returns_the_matches(client, mocked_api):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": [ROW], "total": 1}))
    assert client.apps.requests.list(id_prefix="apr_9F2c") == [ROW]
    assert route.calls.last.request.url.params == httpx.QueryParams({"idPrefix": "apr_9F2c"})


def test_two_rows_mean_the_prefix_matches_more_than_one(client, mocked_api):
    mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": [ROW, ROW], "total": 7}))
    assert len(client.apps.requests.list(id_prefix="apr_9f2c")) == 2


@pytest.mark.parametrize("prefix", ["apr_9f2", "apr_" + "a" * 33, "apr_9f2g", "9f2c4e1a", 9])
def test_a_bad_prefix_is_refused_before_sending(client, mocked_api, prefix):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    with pytest.raises(ValueError) as refused:
        client.apps.requests.list(id_prefix=prefix)
    assert str(refused.value).startswith(REFUSAL)
    assert not route.called


@pytest.mark.parametrize("other", [{"limit": 5}, {"before": "apr_" + "c" * 32}, {"status": "applied"}])
def test_another_param_beside_the_prefix_is_refused(client, mocked_api, other):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    with pytest.raises(ValueError) as refused:
        client.apps.requests.list(id_prefix="apr_9f2c", **other)
    assert str(refused.value) == ALONE
    assert not route.called


def test_the_readme_shows_id_prefix():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert 'client.apps.requests.list(id_prefix="apr_9f2c")' in readme
