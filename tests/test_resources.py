"""API keys and reconciliation readers."""

from __future__ import annotations

import json

import pytest

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
                "propertyId": None,
                "scopes": ["event:*"],
                "expiresAt": None,
                "lastUsedAt": None,
                "revokedAt": None,
                "dateCreated": "2026-10-19T00:00:00+00:00",
                "dateModified": "2026-10-19T00:00:00+00:00",
                "rawKey": "sk_live_" + "f" * 64,
            },
        )

        key = client.api_keys.create(label="Production server", scopes=["event:*"])

        body = json.loads(route.calls.last.request.content)
        assert body == {"label": "Production server", "scopes": ["event:*"]}
        assert key.id == 3
        assert key.raw_key == "sk_live_" + "f" * 64
        assert key.key_prefix == "sk_live_a1b2c3d4"
        # Siren's ApiKeyAdapter names the creation time dateCreated.
        assert key.created_at == "2026-10-19T00:00:00+00:00"
        assert key.revoked_at is None

    @pytest.mark.parametrize("scopes", [None, [], "event:*"])
    def test_create_requires_scopes(self, client, mocked_api, scopes):
        # Siren's CreateApiKey answers 400 "At least one scope is required."
        # when scopes is missing or empty, and has done so since before this
        # SDK's 0.1.0. The SDK used to promise that omitting scopes gave full
        # access, which was never true, so it now refuses before sending.
        route = mocked_api.post("/api-keys").respond(201, json={"id": 4})

        with pytest.raises((ValueError, TypeError)):
            client.api_keys.create(label="CI", scopes=scopes)

        assert not route.called

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

    def test_list_pages_with_number_and_offset(self, client, mocked_api):
        # GET /api-keys serves at most 200 rows a request, and an org can
        # hold 250 active keys plus its revoked ones.
        route = mocked_api.get("/api-keys").respond(200, json=[])

        client.api_keys.list(number=200, offset=200)

        params = route.calls.last.request.url.params
        assert params["number"] == "200"
        assert params["offset"] == "200"

    def test_revoke(self, client, mocked_api):
        route = mocked_api.delete("/api-keys/2").respond(200)

        assert client.api_keys.revoke(2) is None
        assert route.called


class TestReconciliationReaders:
    def test_page_and_per_page_become_number_and_offset(self, client, mocked_api):
        # Siren's list routes page with `number` and `offset` (PageSizeMiddleware).
        # They never read `page` or `perPage`, so the SDK used to return the
        # first 10 rows whatever page was asked for.
        route = mocked_api.get("/conversions").respond(
            200,
            json={"items": [{"id": 1}], "total": 51, "page": 2, "perPage": 50, "totalPages": 2},
        )

        page = client.conversions.list(page=2, per_page=50)

        params = route.calls.last.request.url.params
        assert params["number"] == "50"
        assert params["offset"] == "50"
        assert "page" not in params
        assert "perPage" not in params
        assert page.page == 2
        assert page.per_page == 50

    def test_envelope_body_is_read(self, client, mocked_api):
        # GetConversions, GetTransactions, GetObligations and GetPayouts run
        # ListResponseWrapperInterceptor, so the body is
        # {items, total, page, perPage, totalPages}, not a bare array. The SDK
        # used to read only bare arrays and returned no rows at all.
        mocked_api.get("/conversions").respond(
            200,
            json={
                "items": [{"id": 1}, {"id": 2}],
                "total": 12,
                "page": 1,
                "perPage": 10,
                "totalPages": 2,
            },
            headers={"x-siren-estimated-count": "12"},
        )

        page = client.conversions.list()

        assert page.data == [{"id": 1}, {"id": 2}]
        assert page.total == 12
        assert page.page == 1
        assert page.per_page == 10
        assert page.total_pages == 2

    def test_page_without_per_page_uses_default_size(self, client, mocked_api):
        route = mocked_api.get("/payouts").respond(200, json={"items": []})

        client.payouts.list(page=3)

        params = route.calls.last.request.url.params
        assert params["number"] == "10"
        assert params["offset"] == "20"

    def test_per_page_is_capped_at_200(self, client, mocked_api):
        # Siren lowers a larger `number` to 200. Clamping here too keeps a
        # page-based offset from skipping the rows between the two sizes.
        route = mocked_api.get("/transactions").respond(200, json={"items": []})

        client.transactions.list(page=2, per_page=500)

        params = route.calls.last.request.url.params
        assert params["number"] == "200"
        assert params["offset"] == "200"

    def test_explicit_number_and_offset_win(self, client, mocked_api):
        route = mocked_api.get("/obligations").respond(200, json={"items": []})

        client.obligations.list(page=5, per_page=20, number=30, offset=7)

        params = route.calls.last.request.url.params
        assert params["number"] == "30"
        assert params["offset"] == "7"

    def test_bare_array_body_falls_back_to_count_header(self, client, mocked_api):
        mocked_api.get("/conversions").respond(
            200,
            json=[{"id": 1}],
            headers={"x-siren-estimated-count": "51"},
        )

        page = client.conversions.list(page=2, per_page=50)

        assert page.data == [{"id": 1}]
        assert page.page == 2
        assert page.per_page == 50
        assert page.total == 51
        assert page.total_pages is None

    def test_total_none_when_count_header_absent(self, client, mocked_api):
        mocked_api.get("/conversions").respond(200, json=[{"id": 1}])

        page = client.conversions.list()

        assert page.data == [{"id": 1}]
        assert page.total is None
        assert page.page is None
        assert page.per_page is None

    def test_extra_filters_pass_through_as_camel_case(self, client, mocked_api):
        route = mocked_api.get("/payouts").respond(200, json={"items": []})

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
            route = mocked_api.get(path).respond(200, json={"items": []})
            getattr(client, name).list()
            assert route.called, path

    def test_list_page_is_iterable_and_sized(self, client, mocked_api):
        mocked_api.get("/transactions").respond(
            200,
            json={"items": [{"id": 1}, {"id": 2}], "total": 2},
        )

        page = client.transactions.list()

        assert len(page) == 2
        assert list(page) == [{"id": 1}, {"id": 2}]
        assert page[0]["id"] == 1

    def test_no_params_sends_no_query_string(self, client, mocked_api):
        route = mocked_api.get("/obligations").respond(200, json={"items": []})

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
