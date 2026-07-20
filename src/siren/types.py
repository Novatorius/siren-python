"""Typed result objects and Siren's domain taxonomy (event and status catalogs).

Siren owns these vocabularies; this module is the typed source for them so
integrations never hand-roll magic strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional


class WebhookEventType(str, Enum):
    """Every subscribable webhook event type. ``ALL`` (``"*"``) subscribes to all."""

    ALL = "*"
    ALLOCATION_COMPLETED = "allocation.completed"
    COLLABORATOR_CREATED = "collaborator.created"
    COLLABORATOR_REGISTERED = "collaborator.registered"
    CONVERSION_APPROVED = "conversion.approved"
    CONVERSION_CREATED = "conversion.created"
    CONVERSION_REJECTED = "conversion.rejected"
    CONVERSION_RENEWED = "conversion.renewed"
    COUPON_APPLIED = "coupon.applied"
    CREDIT_ISSUED = "credit.issued"
    CREDIT_REDEEMED = "credit.redeemed"
    CURRENCY_CREATED = "currency.created"
    CURRENCY_DELETED = "currency.deleted"
    DISTRIBUTION_COMPLETED = "distribution.completed"
    ENGAGEMENT_AWARDED = "engagement.awarded"
    ENGAGEMENT_COMPLETED = "engagement.completed"
    ENGAGEMENT_CREATED = "engagement.created"
    FULFILLMENT_CREATED = "fulfillment.created"
    FULFILLMENT_UPDATED = "fulfillment.updated"
    LEAD_CREATED = "lead.created"
    METRICS_UPDATED = "metrics.updated"
    OBLIGATION_COMPLETED = "obligation.completed"
    OBLIGATION_CREATED = "obligation.created"
    OPPORTUNITY_CREATED = "opportunity.created"
    OPPORTUNITY_INVALIDATED = "opportunity.invalidated"
    PAYOUT_CREATED = "payout.created"
    PAYOUT_PAID = "payout.paid"
    REFUND_CREATED = "refund.created"
    RENEWAL_CREATED = "renewal.created"
    SALE_CREATED = "sale.created"
    TRANSACTION_COMPLETED = "transaction.completed"
    TRANSACTION_CREATED = "transaction.created"

    def __str__(self) -> str:
        return self.value


class EventSlug(str, Enum):
    """URL slugs for the built-in ingestion event types (``POST /event/{slug}``).

    ``client.events.sale()`` / ``.refund()`` / ``.site_visited()`` already use
    these internally; the members exist for code that routes slugs dynamically
    (e.g. wrapping ``client.events.ingest``).
    """

    SALE = "sale"
    REFUND = "refund"
    SITE_VISITED = "site-visited"

    def __str__(self) -> str:
        return self.value


class ConversionStatus(str, Enum):
    """Statuses a conversion can hold (``client.conversions`` records and
    ``conversion.*`` webhook payloads)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    #: Soft-delete bucket written by the bulk delete action.
    DELETED = "deleted"

    def __str__(self) -> str:
        return self.value


class TransactionStatus(str, Enum):
    """Statuses a transaction can hold (``client.transactions`` records and
    ``transaction.*`` webhook payloads)."""

    COMPLETE = "complete"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

    def __str__(self) -> str:
        return self.value


class ObligationStatus(str, Enum):
    """Statuses an obligation can hold (``client.obligations`` records and
    ``obligation.*`` webhook payloads).

    Note: Siren's machine paths (fulfillment generation, bulk actions) write
    ``complete``, while its management REST surface accepts ``fulfilled`` —
    both appear in the wild, so both are listed here.
    """

    PENDING = "pending"
    COMPLETE = "complete"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


class PayoutStatus(str, Enum):
    """Statuses a payout can hold (``client.payouts`` records and
    ``payout.*`` webhook payloads)."""

    UNPAID = "unpaid"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class FulfillmentStatus(str, Enum):
    """Statuses a fulfillment can hold (``fulfillment.created`` /
    ``fulfillment.updated`` webhook payloads)."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class OpportunityStatus(str, Enum):
    """Statuses an opportunity can hold (``opportunity.created`` /
    ``opportunity.invalidated`` webhook payloads; the ``tracking_id`` on a
    sale refers to an opportunity)."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    #: Set by Siren's invalidation service; never operator-settable.
    INVALID = "invalid"

    def __str__(self) -> str:
        return self.value


class ApiKeyStatus(str, Enum):
    """Statuses an API key can hold (``client.api_keys`` records)."""

    ACTIVE = "active"
    REVOKED = "revoked"

    def __str__(self) -> str:
        return self.value


class WebhookSubscriptionStatus(str, Enum):
    """Statuses a webhook subscription can hold
    (``client.webhooks.subscriptions`` records)."""

    ACTIVE = "active"
    PAUSED = "paused"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class EventResult:
    """Result of an event ingestion call.

    ``opportunity_id`` is read from the ``X-Siren-OID`` response header; it is
    ``None`` when the server did not associate the event with an opportunity.
    """

    opportunity_id: Optional[int] = None


@dataclass(frozen=True)
class WebhookEvent:
    """A verified, parsed webhook delivery."""

    type: str
    data: Dict[str, Any]
    delivery_id: Optional[str] = None


@dataclass(frozen=True)
class WebhookSubscription:
    """A webhook subscription.

    ``signing_secret`` is populated only on the response from
    ``webhooks.subscriptions.create`` — it is returned exactly once and cannot
    be retrieved later. ``raw`` holds the untouched API payload.
    """

    id: Optional[int] = None
    target_url: Optional[str] = None
    events: List[str] = field(default_factory=list)
    description: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    signing_secret: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "WebhookSubscription":
        return cls(
            id=payload.get("id"),
            target_url=payload.get("targetUrl"),
            events=list(payload.get("events") or []),
            description=payload.get("description"),
            status=payload.get("status"),
            created_at=payload.get("createdAt"),
            signing_secret=payload.get("signingSecret"),
            raw=payload,
        )


@dataclass(frozen=True)
class ApiKey:
    """An API key record.

    ``raw_key`` (the plaintext ``sk_live_...`` credential) is populated only on
    the response from ``api_keys.create`` — it is returned exactly once and
    cannot be retrieved later. ``raw`` holds the untouched API payload.
    """

    id: Optional[int] = None
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    status: Optional[str] = None
    scopes: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    raw_key: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ApiKey":
        return cls(
            id=payload.get("id"),
            key_prefix=payload.get("keyPrefix"),
            label=payload.get("label"),
            status=payload.get("status"),
            scopes=list(payload.get("scopes") or []),
            created_at=payload.get("createdAt"),
            expires_at=payload.get("expiresAt"),
            last_used_at=payload.get("lastUsedAt"),
            raw_key=payload.get("rawKey"),
            raw=payload,
        )


@dataclass(frozen=True)
class ListPage:
    """One page of records from a reconciliation reader.

    The API returns a bare JSON array as the body; ``page`` and ``per_page``
    echo the request arguments, and ``total`` is the server's estimated total
    count from the ``x-siren-estimated-count`` response header (``None`` when
    the header is absent). Iterating (or indexing) a ``ListPage`` iterates its
    ``data``.
    """

    data: List[Dict[str, Any]] = field(default_factory=list)
    page: Optional[int] = None
    per_page: Optional[int] = None
    total: Optional[int] = None

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.data[index]
