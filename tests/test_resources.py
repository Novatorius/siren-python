"""Reconciliation readers and the client's namespaces."""

from __future__ import annotations

import json

import pytest

import siren


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
        assert hasattr(client.webhooks.subscriptions, "list")
        assert hasattr(client.webhooks.subscriptions, "delete")
        for method in ("scopes", "create_key", "rotate_key", "revoke_key"):
            assert hasattr(client.apps, method)
        for reader in ("conversions", "transactions", "obligations", "payouts"):
            assert hasattr(getattr(client, reader), "list")

    def test_version(self):
        assert siren.__version__ == "0.2.0"
