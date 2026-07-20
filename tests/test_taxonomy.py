"""Pins the SDK's taxonomy to Siren's canonical vocabulary.

If one of these fails, either the SDK drifted or the Siren service changed its
domain language — reconcile against the service (the taxonomy owner), not by
editing the expectation to match the code.
"""

import siren


class TestWebhookEventType:
    def test_matches_canonical_set(self):
        expected = {
            "*",
            "allocation.completed",
            "collaborator.created",
            "collaborator.registered",
            "conversion.approved",
            "conversion.created",
            "conversion.rejected",
            "conversion.renewed",
            "coupon.applied",
            "credit.issued",
            "credit.redeemed",
            "currency.created",
            "currency.deleted",
            "distribution.completed",
            "engagement.awarded",
            "engagement.completed",
            "engagement.created",
            "fulfillment.created",
            "fulfillment.updated",
            "lead.created",
            "metrics.updated",
            "obligation.completed",
            "obligation.created",
            "opportunity.created",
            "opportunity.invalidated",
            "payout.created",
            "payout.paid",
            "refund.created",
            "renewal.created",
            "sale.created",
            "transaction.completed",
            "transaction.created",
        }
        assert {member.value for member in siren.WebhookEventType} == expected
        assert len(siren.WebhookEventType) == len(expected)


class TestEventSlug:
    def test_matches_built_in_ingestion_slugs(self):
        assert {member.value for member in siren.EventSlug} == {
            "sale",
            "refund",
            "site-visited",
        }

    def test_is_str_enum(self):
        assert isinstance(siren.EventSlug.SALE, str)
        assert str(siren.EventSlug.SITE_VISITED) == "site-visited"


class TestStatusVocabularies:
    def test_conversion_status(self):
        assert {member.value for member in siren.ConversionStatus} == {
            "pending",
            "approved",
            "rejected",
            "expired",
            "deleted",
        }

    def test_transaction_status(self):
        assert {member.value for member in siren.TransactionStatus} == {
            "complete",
            "cancelled",
            "refunded",
        }

    def test_obligation_status(self):
        assert {member.value for member in siren.ObligationStatus} == {
            "pending",
            "complete",
            "fulfilled",
            "cancelled",
        }

    def test_payout_status(self):
        assert {member.value for member in siren.PayoutStatus} == {
            "unpaid",
            "processing",
            "paid",
            "failed",
        }

    def test_fulfillment_status(self):
        assert {member.value for member in siren.FulfillmentStatus} == {
            "pending",
            "processing",
            "complete",
            "failed",
        }

    def test_opportunity_status(self):
        assert {member.value for member in siren.OpportunityStatus} == {
            "active",
            "inactive",
            "invalid",
        }

    def test_api_key_status(self):
        assert {member.value for member in siren.ApiKeyStatus} == {
            "active",
            "revoked",
        }

    def test_webhook_subscription_status(self):
        assert {member.value for member in siren.WebhookSubscriptionStatus} == {
            "active",
            "paused",
        }

    def test_statuses_are_str_enums(self):
        assert isinstance(siren.ConversionStatus.APPROVED, str)
        assert str(siren.ConversionStatus.APPROVED) == "approved"
        assert siren.PayoutStatus.PAID == "paid"
