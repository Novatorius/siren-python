"""``apps.requests.list()`` pages with Siren's ``limit`` (1 to 100, 50 when
absent) and ``before`` (a request id: only older requests), as
ListAppRequests.php on Siren feat/apps 0383186437 reads them.
"""

from __future__ import annotations

import httpx
import pytest

import siren
from siren.apps import AppRequests

BEFORE = "apr_" + "c" * 32


def test_the_list_takes_limit_and_before():
    assert {"limit", "before"} <= set(AppRequests.LIST_FILTERS)
    assert AppRequests.MAX_LIST_LIMIT == 100


def test_limit_and_before_reach_the_query(client, mocked_api):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    assert client.apps.requests.list(status="applied", limit=20, before=BEFORE) == []
    assert route.calls.last.request.url.params == httpx.QueryParams({"status": "applied", "limit": "20", "before": BEFORE})


def test_a_list_with_no_params_sends_no_query(client, mocked_api):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    client.apps.requests.list()
    assert route.calls.last.request.url.params == httpx.QueryParams({})


@pytest.mark.parametrize(
    ("params", "words"),
    [
        ({"limit": 0}, "whole number from 1 to 100"),
        ({"limit": 101}, "whole number from 1 to 100"),
        ({"limit": "20"}, "whole number from 1 to 100"),
        ({"limit": True}, "whole number from 1 to 100"),
        ({"before": "req_1"}, "needs a request id"),
    ],
)
def test_a_bad_page_is_refused_before_sending(client, mocked_api, params, words):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    with pytest.raises(ValueError, match=words):
        client.apps.requests.list(**params)
    assert not route.called


def test_the_readme_shows_paging():
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "<!-- readme-test: requests-paging -->" in readme
    assert "before=" in readme and "limit=" in readme
