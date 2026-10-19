"""Payload mapping for each event helper, and X-Siren-OID handling."""

from __future__ import annotations

import json

import pytest

import siren


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


class TestSale:
    def test_minimal_payload_mapping(self, client, mocked_api):
        route = mocked_api.post("/event/sale").respond(200, headers={"X-Siren-OID": "4021"})

        result = client.events.sale(
            source="stripe", external_id="cs_test_a1b2c3", total=49.99, tracking_id=4021
        )

        assert _body(route) == {
            "source": "stripe",
            "externalId": "cs_test_a1b2c3",
            "total": 49.99,
            "trackingId": 4021,
        }
        assert result.opportunity_id == 4021

    def test_total_stays_in_major_units(self, client, mocked_api):
        route = mocked_api.post("/event/sale").respond(200)

        client.events.sale(source="woo", external_id="9", total=9.99, tracking_id=1)

        assert _body(route)["total"] == 9.99  # 9.99 means $9.99, never 999 cents

    def test_currency_included_when_given(self, client, mocked_api):
        route = mocked_api.post("/event/sale").respond(200)

        client.events.sale(
            source="woo", external_id="9", total=5.0, tracking_id=1, currency="EUR"
        )

        assert _body(route)["currency"] == "EUR"

    def test_line_items_map_to_camel_case_with_quantity_default_1(
        self, client, mocked_api
    ):
        route = mocked_api.post("/event/sale").respond(200)

        client.events.sale(
            source="stripe",
            external_id="ord_77",
            total=109.97,
            tracking_id=3,
            items=[
                {"external_id": "sku_1", "name": "Pro Plan (annual)", "amount": 49.99},
                {"name": "Add-on", "quantity": 3, "amount": 19.99},
            ],
        )

        assert _body(route)["items"] == [
            {
                "externalId": "sku_1",
                "name": "Pro Plan (annual)",
                "quantity": 1,  # missing quantity defaults to 1
                "amount": 49.99,
            },
            {"name": "Add-on", "quantity": 3, "amount": 19.99},
        ]

    def test_line_items_accept_camel_case_keys(self, client, mocked_api):
        route = mocked_api.post("/event/sale").respond(200)

        client.events.sale(
            source="stripe",
            external_id="ord_78",
            total=10.0,
            tracking_id=3,
            items=[{"externalId": "sku_9", "name": "Thing", "quantity": 2, "amount": 5.0}],
        )

        assert _body(route)["items"][0]["externalId"] == "sku_9"
        assert _body(route)["items"][0]["quantity"] == 2

    def test_line_item_missing_required_field_raises(self, client):
        with pytest.raises(ValueError, match="amount"):
            client.events.sale(
                source="stripe",
                external_id="ord_79",
                total=10.0,
                tracking_id=3,
                items=[{"name": "No price"}],
            )

    def test_items_omitted_from_payload_when_not_given(self, client, mocked_api):
        route = mocked_api.post("/event/sale").respond(200)

        client.events.sale(source="woo", external_id="1", total=1.0, tracking_id=1)

        assert "items" not in _body(route)
        assert "currency" not in _body(route)


class TestRefund:
    def test_payload_mapping(self, client, mocked_api):
        route = mocked_api.post("/event/refund").respond(200, headers={"X-Siren-OID": "8"})

        result = client.events.refund(source="stripe", external_id="cs_test_a1b2c3")

        assert _body(route) == {"source": "stripe", "externalId": "cs_test_a1b2c3"}
        assert result.opportunity_id == 8

    def test_unknown_sale_raises_not_found(self, client, mocked_api):
        mocked_api.post("/event/refund").respond(
            404,
            json={"error": {"message": "No matching sale transaction.", "code": "not_found"}},
        )

        with pytest.raises(siren.NotFoundError) as excinfo:
            client.events.refund(source="stripe", external_id="nope")

        assert excinfo.value.status_code == 404
        assert excinfo.value.code == "not_found"


class TestSiteVisited:
    def test_payload_mapping(self, client, mocked_api):
        route = mocked_api.post("/event/site-visited").respond(
            200, headers={"X-Siren-OID": "555"}
        )

        result = client.events.site_visited(collaborator_id=88, user_id=12345)

        assert _body(route) == {"collaboratorId": 88, "userId": 12345}
        assert result.opportunity_id == 555

    def test_user_id_omitted_when_not_given(self, client, mocked_api):
        route = mocked_api.post("/event/site-visited").respond(200)

        client.events.site_visited(collaborator_id=88)

        assert _body(route) == {"collaboratorId": 88}


class TestIngest:
    def test_posts_payload_through_to_custom_slug(self, client, mocked_api):
        route = mocked_api.post("/event/loyalty-points-earned").respond(
            200, headers={"X-Siren-OID": "31"}
        )
        payload = {"points": 120, "memberId": "m_9", "nested": {"a": [1, 2]}}

        result = client.events.ingest("loyalty-points-earned", payload)

        assert _body(route) == payload
        assert result.opportunity_id == 31


class TestEventResult:
    def test_opportunity_id_none_when_header_missing(self, client, mocked_api):
        mocked_api.post("/event/sale").respond(200)

        result = client.events.sale(
            source="woo", external_id="1", total=1.0, tracking_id=1
        )

        assert result.opportunity_id is None

    def test_opportunity_id_none_when_header_not_numeric(self, client, mocked_api):
        mocked_api.post("/event/sale").respond(200, headers={"X-Siren-OID": "abc"})

        result = client.events.sale(
            source="woo", external_id="1", total=1.0, tracking_id=1
        )

        assert result.opportunity_id is None


class TestEventOutcome:
    def test_sale_outcome_body_is_exposed(self, client, mocked_api):
        # SaleEventFactory now answers with SaleIngestion's outcome instead of
        # an empty 200. The SDK used to drop the body, so a sale that matched
        # no opportunity looked the same as one that paid out.
        mocked_api.post("/event/sale").respond(
            200,
            json={
                "status": "unattributed",
                "created": False,
                "transactionId": None,
                "conversions": [],
                "message": "The tracking id matches no opportunity.",
            },
        )

        result = client.events.sale(
            source="woo", external_id="1", total=1.0, tracking_id=1
        )

        assert result.http_status == 200
        assert result.status == "unattributed"
        assert result.body["created"] is False
        assert result.queued is False

    def test_queued_event_is_reported(self, client, mocked_api):
        # IngestEvent queues an event with a 202 while the org's processing
        # is paused, instead of refusing it with a 429.
        mocked_api.post("/event/site-visited").respond(
            202,
            json={
                "status": "queued",
                "queued": True,
                "attributed": False,
                "opportunityId": 77,
                "message": "Engine activity is paused for this organization.",
            },
            headers={"X-Siren-OID": "77"},
        )

        result = client.events.site_visited(collaborator_id=5)

        assert result.queued is True
        assert result.status == "queued"
        assert result.opportunity_id == 77

    def test_empty_body_gives_empty_dict(self, client, mocked_api):
        mocked_api.post("/event/refund").respond(200)

        result = client.events.refund(source="woo", external_id="1")

        assert result.body == {}
        assert result.status is None
        assert result.queued is False


class TestQueuedVisitOpportunity:
    def test_opportunity_id_falls_back_to_body(self, client, mocked_api):
        # IngestEvent's queued visit names the reserved opportunity in the
        # body. Match the Node SDK and read it there when no header came back.
        mocked_api.post("/event/site-visited").respond(
            202, json={"status": "queued", "queued": True, "opportunityId": 81}
        )

        result = client.events.site_visited(collaborator_id=5)

        assert result.opportunity_id == 81
        assert result.queued is True
