"""API keys and reconciliation readers."""

from __future__ import annotations

import json

import siren


class TestApiKeys:
    def test_create_sends_payload_and_returns_raw_key_once(self, client, mocked_api):
        route = mocked_api.post("/api-keys").respond(
            201,
            json={
                "id": 3,
                "keyPrefix": "sk_live_a1b2c3d4",
                "label": "Production server",
                "status": "active",
                "scopes": ["events:write"],
                "createdAt": "2026-07-11T00:00:00Z",
                "rawKey": "sk_live_" + "f" * 64,
            },
        )

        key = client.api_keys.create(label="Production server", scopes=["events:write"])

        body = json.loads(route.calls.last.request.content)
        assert body == {"label": "Production server", "scopes": ["events:write"]}
        assert key.id == 3
        assert key.raw_key == "sk_live_" + "f" * 64
        assert key.key_prefix == "sk_live_a1b2c3d4"

    def test_create_omits_scopes_for_full_access(self, client, mocked_api):
        route = mocked_api.post("/api-keys").respond(201, json={"id": 4, "label": "CI"})

        client.api_keys.create(label="CI")

        assert json.loads(route.calls.last.request.content) == {"label": "CI"}

    def test_list_returns_bare_array_and_has_no_raw_key(self, client, mocked_api):
        mocked_api.get("/api-keys").respond(
            200,
            json=[
                {"id": 1, "keyPrefix": "sk_live_aaaa", "label": "One", "status": "active"},
                {"id": 2, "keyPrefix": "sk_live_bbbb", "label": "Two", "status": "revoked"},
            ],
        )

        keys = client.api_keys.list()

        assert [k.id for k in keys] == [1, 2]
        assert all(k.raw_key is None for k in keys)

    def test_revoke(self, client, mocked_api):
        route = mocked_api.delete("/api-keys/2").respond(200)

        assert client.api_keys.revoke(2) is None
        assert route.called


class TestReconciliationReaders:
    def test_bare_array_body_with_pagination_params_and_count_header(
        self, client, mocked_api
    ):
        route = mocked_api.get("/conversions").respond(
            200,
            json=[{"id": 1}],
            headers={"x-siren-estimated-count": "51"},
        )

        page = client.conversions.list(page=2, per_page=50)

        request = route.calls.last.request
        assert request.url.params["page"] == "2"
        assert request.url.params["perPage"] == "50"
        assert page.data == [{"id": 1}]
        assert page.page == 2  # echoes the request arg
        assert page.per_page == 50  # echoes the request arg
        assert page.total == 51  # from x-siren-estimated-count header

    def test_total_none_when_count_header_absent(self, client, mocked_api):
        mocked_api.get("/conversions").respond(200, json=[{"id": 1}])

        page = client.conversions.list()

        assert page.data == [{"id": 1}]
        assert page.total is None
        assert page.page is None
        assert page.per_page is None

    def test_extra_filters_pass_through_as_camel_case(self, client, mocked_api):
        route = mocked_api.get("/payouts").respond(200, json=[])

        client.payouts.list(status="paid", collaborator_id=88)

        params = route.calls.last.request.url.params
        assert params["status"] == "paid"
        assert params["collaboratorId"] == "88"

    def test_each_reader_hits_its_endpoint(self, client, mocked_api):
        for name, path in [
            ("conversions", "/conversions"),
            ("transactions", "/transactions"),
            ("obligations", "/obligations"),
            ("payouts", "/payouts"),
        ]:
            route = mocked_api.get(path).respond(200, json=[])
            getattr(client, name).list()
            assert route.called, path

    def test_list_page_is_iterable_and_sized(self, client, mocked_api):
        mocked_api.get("/transactions").respond(
            200,
            json=[{"id": 1}, {"id": 2}],
            headers={"x-siren-estimated-count": "2"},
        )

        page = client.transactions.list()

        assert len(page) == 2
        assert list(page) == [{"id": 1}, {"id": 2}]
        assert page[0]["id"] == 1

    def test_no_params_sends_no_query_string(self, client, mocked_api):
        route = mocked_api.get("/obligations").respond(200, json=[])

        client.obligations.list()

        assert str(route.calls.last.request.url.query, "ascii") == ""


class TestPublicSurface:
    def test_namespaces_exist(self, client):
        assert hasattr(client.events, "sale")
        assert hasattr(client.events, "refund")
        assert hasattr(client.events, "site_visited")
        assert hasattr(client.events, "ingest")
        assert hasattr(client.webhooks, "construct_event")
        assert hasattr(client.webhooks, "verify_signature")
        assert hasattr(client.webhooks.subscriptions, "create")
        assert hasattr(client.webhooks.subscriptions, "list")
        assert hasattr(client.webhooks.subscriptions, "delete")
        assert hasattr(client.api_keys, "create")
        assert hasattr(client.api_keys, "list")
        assert hasattr(client.api_keys, "revoke")
        for reader in ("conversions", "transactions", "obligations", "payouts"):
            assert hasattr(getattr(client, reader), "list")

    def test_version(self):
        assert siren.__version__ == "0.1.0"
