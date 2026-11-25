"""The request list's rules, the same as Node's at siren-node 5e19832:
``app_id`` keeps to one app on the plain list, a page of it and
``needs_attention`` (ListAppRequests at Siren feat/apps 76d5bbdf83), never
beside ``id_prefix``. ``limit`` and ``before`` page ``needs_attention`` too,
which Siren reads from pkt/apps-attention-paging and an older Siren ignores."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from siren.apps import AppRequests

BEFORE = "apr_" + "c" * 32
APP_ID = "apps.requests.list() takes an app_id of a whole number of 1 or more, an app's id, and got "


def test_the_list_reads_app_id():
    assert AppRequests.LIST_FILTERS == ("status", "limit", "before", "id_prefix", "app_id")


@pytest.mark.parametrize(
    ("params", "query"),
    [
        ({"app_id": 12}, {"appId": "12"}),
        ({"app_id": 12, "limit": 100, "before": BEFORE}, {"appId": "12", "limit": "100", "before": BEFORE}),
        ({"app_id": 12, "status": "needs_attention"}, {"appId": "12", "status": "needs_attention"}),
    ],
)
def test_app_id_keeps_to_one_app(client, mocked_api, params, query):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    assert client.apps.requests.list(**params) == []
    assert dict(route.calls.last.request.url.params) == query


def test_needs_attention_pages_with_limit_and_before(client, mocked_api):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    client.apps.requests.list(status="needs_attention", limit=100, before=BEFORE)
    assert dict(route.calls.last.request.url.params) == {"status": "needs_attention", "limit": "100", "before": BEFORE}


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"app_id": 0}, APP_ID + "0."),
        ({"app_id": 1.5}, APP_ID + "1.5."),
        ({"app_id": "12"}, APP_ID + "'12'."),
        ({"app_id": True}, APP_ID + "True."),
        (
            {"app_id": 12, "id_prefix": "apr_9f2c"},
            "apps.requests.list() takes id_prefix alone. Siren ignores status, limit, before and app_id beside it, "
            "and answers at most two matches.",
        ),
        ({"id_prefix": "apr_9f2"}, "apps.requests.list() takes an id_prefix of apr_ and 4 to 32 hex characters, and got 'apr_9f2'."),
        ({"limit": 0}, "apps.requests.list() takes a limit of a whole number from 1 to 100, and got 0."),
        ({"before": "apr_x"}, "apps.requests.list() before needs a request id of apr_ and 32 lowercase hex characters, and got 'apr_x'."),
    ],
)
def test_a_bad_list_is_refused_before_sending_in_nodes_words(client, mocked_api, params, message):
    route = mocked_api.get("/apps/requests").mock(return_value=httpx.Response(200, json={"requests": []}))
    with pytest.raises(ValueError) as refused:
        client.apps.requests.list(**params)
    assert str(refused.value) == message
    assert not route.called


def test_the_readme_shows_app_id():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "client.apps.requests.list(app_id=12" in readme
