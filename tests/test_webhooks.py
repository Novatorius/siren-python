"""Webhook signature verification, construct_event, subscriptions, event enum."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

import siren
from siren.webhooks import Webhooks

SECRET = "whsec_test_secret"
PAYLOAD = {
    "type": "conversion.approved",
    "data": {"id": 991, "amount": 12.5},
    "deliveryId": "dlv_01H",
}
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
    def test_returns_parsed_event(self, client):
        event = client.webhooks.construct_event(RAW_BODY, sign(RAW_BODY), SECRET)

        assert isinstance(event, siren.WebhookEvent)
        assert event.type == "conversion.approved"
        assert event.data == {"id": 991, "amount": 12.5}
        assert event.delivery_id == "dlv_01H"

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

    def test_delivery_id_optional(self, client):
        body = json.dumps({"type": "payout.paid", "data": {}}).encode()
        event = client.webhooks.construct_event(body, sign(body), SECRET)
        assert event.delivery_id is None


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
                "createdAt": "2026-07-11T00:00:00Z",
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

    def test_delete(self, client, mocked_api):
        route = mocked_api.delete("/webhooks/9").respond(200)

        assert client.webhooks.subscriptions.delete(9) is None
        assert route.called


class TestWebhookEventType:
    def test_all_31_types_plus_wildcard(self):
        # The full canonical set is pinned in tests/test_taxonomy.py.
        assert len(siren.WebhookEventType) == 32

    def test_wildcard_member(self):
        assert siren.WebhookEventType.ALL == "*"
        assert siren.WebhookEventType.ALL.value == "*"

    def test_catalog_matches_design(self):
        expected = {
            "*",
            "allocation.completed", "collaborator.created", "collaborator.registered",
            "conversion.approved", "conversion.created", "conversion.rejected",
            "conversion.renewed", "coupon.applied", "credit.issued",
            "credit.redeemed", "currency.created", "currency.deleted",
            "distribution.completed",
            "engagement.awarded", "engagement.completed", "engagement.created",
            "fulfillment.created", "fulfillment.updated", "lead.created",
            "metrics.updated", "obligation.completed", "obligation.created",
            "opportunity.created", "opportunity.invalidated", "payout.created",
            "payout.paid", "refund.created", "renewal.created", "sale.created",
            "transaction.completed", "transaction.created",
        }
        assert {member.value for member in siren.WebhookEventType} == expected

    def test_is_str_enum(self):
        assert isinstance(siren.WebhookEventType.SALE_CREATED, str)
        assert str(siren.WebhookEventType.SALE_CREATED) == "sale.created"
