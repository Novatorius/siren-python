"""Webhook signature verification, construct_event, subscriptions, event enum."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

import siren
from siren.webhooks import Webhooks

SECRET = "whsec_test_secret"
PAYLOAD = {"conversionId": 991, "amount": 12.5}
RAW_BODY = json.dumps(PAYLOAD).encode("utf-8")


def sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestVerifySignature:
    def test_valid_signature(self):
        assert Webhooks.verify_signature(RAW_BODY, sign(RAW_BODY), SECRET) is True

    def test_valid_signature_with_str_body(self):
        assert (
            Webhooks.verify_signature(RAW_BODY.decode(), sign(RAW_BODY), SECRET) is True
        )

    def test_tampered_body(self):
        tampered = RAW_BODY.replace(b"12.5", b"9999")
        assert Webhooks.verify_signature(tampered, sign(RAW_BODY), SECRET) is False

    def test_wrong_secret(self):
        assert Webhooks.verify_signature(RAW_BODY, sign(RAW_BODY), "whsec_other") is False

    def test_missing_header(self):
        assert Webhooks.verify_signature(RAW_BODY, None, SECRET) is False
        assert Webhooks.verify_signature(RAW_BODY, "", SECRET) is False

    def test_malformed_header(self):
        assert Webhooks.verify_signature(RAW_BODY, "md5=abcdef", SECRET) is False
        assert Webhooks.verify_signature(RAW_BODY, "sha256=", SECRET) is False
        assert Webhooks.verify_signature(RAW_BODY, "garbage", SECRET) is False

    def test_reserialized_body_does_not_verify(self):
        # Same JSON data, different byte layout -> HMAC must not match.
        reserialized = json.dumps(PAYLOAD, indent=2).encode()
        assert Webhooks.verify_signature(reserialized, sign(RAW_BODY), SECRET) is False


class TestConstructEvent:
    def test_plain_subscription_body_is_the_payload(self, client):
        # Siren's WebhookDeliveryService sends a subscription with no Property
        # (every subscription made through POST /webhooks) the event payload
        # alone, and the event type only in X-Siren-Event. The SDK used to
        # read `type`, `data` and `deliveryId` from the body, which Siren
        # never sends, so every real delivery parsed with an empty type and
        # empty data.
        body = json.dumps({"conversionId": 991}).encode()

        event = client.webhooks.construct_event(
            body,
            sign(body),
            SECRET,
            event_type="conversion.approved",
            delivery_id="evt_0123abcd",
        )

        assert isinstance(event, siren.WebhookEvent)
        assert event.type == "conversion.approved"
        assert event.data == {"conversionId": 991}
        assert event.delivery_id == "evt_0123abcd"
        assert event.raw == {"conversionId": 991}

    def test_plain_payload_with_its_own_type_key_stays_data(self, client):
        # currency.created's payload carries a `type` field of its own.
        body = json.dumps(
            {"code": "PTS", "name": "Points", "type": "points", "exchangeRate": 0.01}
        ).encode()

        event = client.webhooks.construct_event(
            body, sign(body), SECRET, event_type="currency.created"
        )

        assert event.type == "currency.created"
        assert event.data["type"] == "points"

    def test_type_empty_without_event_header(self, client):
        body = json.dumps({"payoutId": 7}).encode()

        event = client.webhooks.construct_event(body, sign(body), SECRET)

        assert event.type == ""
        assert event.data == {"payoutId": 7}
        assert event.delivery_id is None

    def test_connected_site_envelope_is_unwrapped(self, client):
        # A Property-bound subscription gets WebhookDeliveryService's
        # versioned envelope, with the type, id and payload in the body.
        envelope = {
            "version": 1,
            "id": "evt_feed",
            "type": "payout.paid",
            "kind": "engine",
            "occurredAt": "2026-10-19T00:00:00+00:00",
            "propertyId": 4,
            "match": "org",
            "originPropertyId": None,
            "data": {"payoutId": 7, "collaboratorId": 3, "value": 1000, "currency": "USD"},
            "records": {},
            "recordDetail": "ids",
            "omittedRecords": [],
            "recordsTruncated": False,
        }
        body = json.dumps(envelope).encode()

        event = client.webhooks.construct_event(
            body, sign(body), SECRET, event_type="payout.paid", delivery_id="evt_feed"
        )

        assert event.type == "payout.paid"
        assert event.data == envelope["data"]
        assert event.delivery_id == "evt_feed"
        assert event.raw["propertyId"] == 4

    def test_missing_signature_raises(self, client):
        with pytest.raises(siren.SignatureVerificationError):
            client.webhooks.construct_event(RAW_BODY, None, SECRET)

    def test_tampered_body_raises(self, client):
        tampered = RAW_BODY + b" "
        with pytest.raises(siren.SignatureVerificationError):
            client.webhooks.construct_event(tampered, sign(RAW_BODY), SECRET)

    def test_wrong_secret_raises(self, client):
        with pytest.raises(siren.SignatureVerificationError):
            client.webhooks.construct_event(RAW_BODY, sign(RAW_BODY), "whsec_other")

    def test_non_object_body_raises(self, client):
        body = b"[1, 2]"
        with pytest.raises(siren.SignatureVerificationError):
            client.webhooks.construct_event(body, sign(body), SECRET)


class TestSubscriptions:
    def test_create_sends_payload_and_returns_signing_secret(self, client, mocked_api):
        route = mocked_api.post("/webhooks").respond(
            201,
            json={
                "id": 5,
                "targetUrl": "https://example.com/webhooks/siren",
                "events": ["conversion.approved", "payout.paid"],
                "description": "Prod endpoint",
                "status": "active",
                "propertyId": None,
                "dateCreated": "2026-10-19T00:00:00+00:00",
                "signingSecret": "whsec_only_once",
            },
        )

        subscription = client.webhooks.subscriptions.create(
            target_url="https://example.com/webhooks/siren",
            events=[
                siren.WebhookEventType.CONVERSION_APPROVED,
                siren.WebhookEventType.PAYOUT_PAID,
            ],
            description="Prod endpoint",
        )

        body = json.loads(route.calls.last.request.content)
        assert body == {
            "targetUrl": "https://example.com/webhooks/siren",
            "events": ["conversion.approved", "payout.paid"],
            "description": "Prod endpoint",
        }
        assert subscription.id == 5
        assert subscription.signing_secret == "whsec_only_once"
        assert subscription.target_url == "https://example.com/webhooks/siren"
        # Siren's WebhookSubscriptionAdapter names the creation time dateCreated.
        assert subscription.created_at == "2026-10-19T00:00:00+00:00"

    def test_create_accepts_wildcard_strings(self, client, mocked_api):
        route = mocked_api.post("/webhooks").respond(201, json={"id": 6, "events": ["*"]})

        client.webhooks.subscriptions.create(
            target_url="https://example.com/hook", events=["*"]
        )

        body = json.loads(route.calls.last.request.content)
        assert body["events"] == ["*"]
        assert "description" not in body

    def test_list_returns_bare_array(self, client, mocked_api):
        mocked_api.get("/webhooks").respond(
            200,
            json=[
                {"id": 1, "targetUrl": "https://a.example", "events": ["*"]},
                {"id": 2, "targetUrl": "https://b.example", "events": ["sale.created"]},
            ],
        )

        subscriptions = client.webhooks.subscriptions.list()

        assert [s.id for s in subscriptions] == [1, 2]
        assert subscriptions[1].events == ["sale.created"]
        assert subscriptions[0].signing_secret is None

    def test_list_pages_with_number_and_offset(self, client, mocked_api):
        route = mocked_api.get("/webhooks").respond(200, json=[])

        client.webhooks.subscriptions.list(number=50, offset=50)

        params = route.calls.last.request.url.params
        assert params["number"] == "50"
        assert params["offset"] == "50"

    def test_delete(self, client, mocked_api):
        route = mocked_api.delete("/webhooks/9").respond(200, json={"success": True})

        assert client.webhooks.subscriptions.delete(9) is None
        assert route.called


class TestWebhookEventType:
    def test_all_34_types_plus_wildcard(self):
        assert len(siren.WebhookEventType) == 35

    def test_wildcard_member(self):
        assert siren.WebhookEventType.ALL == "*"
        assert siren.WebhookEventType.ALL.value == "*"

    def test_catalog_matches_siren(self):
        # The keys of WebhookCatalog::EVENTS in Siren
        # (lib/Webhooks/Service/Services/WebhookCatalog.php), which
        # GET /webhooks/events serves. Siren added collaborator.updated,
        # collaborator.deactivated, program.created, credit.issued,
        # credit.redeemed, currency.created and currency.deleted after 0.1.0.
        expected = {
            "*",
            "allocation.completed", "collaborator.created",
            "collaborator.deactivated", "collaborator.registered",
            "collaborator.updated", "conversion.approved", "conversion.created",
            "conversion.rejected", "conversion.renewed", "coupon.applied",
            "credit.issued", "credit.redeemed", "currency.created",
            "currency.deleted", "distribution.completed",
            "engagement.awarded", "engagement.completed", "engagement.created",
            "fulfillment.created", "fulfillment.updated", "lead.created",
            "metrics.updated", "obligation.completed", "obligation.created",
            "opportunity.created", "opportunity.invalidated", "payout.created",
            "payout.paid", "program.created", "refund.created",
            "renewal.created", "sale.created", "transaction.completed",
            "transaction.created",
        }
        assert {member.value for member in siren.WebhookEventType} == expected

    def test_is_str_enum(self):
        assert isinstance(siren.WebhookEventType.SALE_CREATED, str)
        assert str(siren.WebhookEventType.SALE_CREATED) == "sale.created"
