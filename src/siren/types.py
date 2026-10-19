"""Typed result objects and the webhook event-type catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional


class WebhookEventType(str, Enum):
    """Every subscribable webhook event type. ``ALL`` (``"*"``) subscribes to all.

    Matches the catalog Siren serves at ``GET /webhooks/events``. Siren
    accepts any string in a subscription's ``events``, so a name it does not
    send is stored and never delivered.
    """

    ALL = "*"
    ALLOCATION_COMPLETED = "allocation.completed"
    COLLABORATOR_CREATED = "collaborator.created"
    COLLABORATOR_DEACTIVATED = "collaborator.deactivated"
    COLLABORATOR_REGISTERED = "collaborator.registered"
    COLLABORATOR_UPDATED = "collaborator.updated"
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
    PROGRAM_CREATED = "program.created"
    REFUND_CREATED = "refund.created"
    RENEWAL_CREATED = "renewal.created"
    SALE_CREATED = "sale.created"
    TRANSACTION_COMPLETED = "transaction.completed"
    TRANSACTION_CREATED = "transaction.created"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class EventResult:
    """Result of an event ingestion call.

    ``opportunity_id`` is read from the ``X-Siren-OID`` response header. It is
    ``None`` when the server did not associate the event with an opportunity.

    ``http_status`` is the response status and ``body`` the JSON object Siren
    answered with, or ``{}`` when the body was empty. A sale answers
    ``{status, created, transactionId, conversions, message}``, with a
    ``reason`` when it earned nothing. A site visit answers ``{status,
    attributed, opportunityId, message}``. While an organization's processing
    is paused, Siren queues the event and answers ``202`` with
    ``{status: "queued", queued: true, message}``. A queued event is recorded
    later, so a refund for an unknown sale is not refused at that point.
    """

    opportunity_id: Optional[int] = None
    http_status: Optional[int] = None
    body: Dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> Optional[str]:
        """The outcome Siren reported, such as ``created``, ``duplicate``,
        ``unattributed``, ``attributed``, ``not_attributed`` or ``queued``."""
        value = self.body.get("status")
        return value if isinstance(value, str) else None

    @property
    def queued(self) -> bool:
        """Whether Siren queued the event for later instead of recording it now."""
        return self.http_status == 202 or self.body.get("queued") is True


@dataclass(frozen=True)
class WebhookEvent:
    """A verified, parsed webhook delivery.

    ``type`` is the event type, such as ``conversion.approved``. ``data`` is
    the event's payload. ``delivery_id`` is the event id (``evt_...``), which
    stays the same when Siren retries a delivery, so use it to drop repeats.
    ``raw`` holds the whole parsed body.
    """

    type: str
    data: Dict[str, Any]
    delivery_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def _created_at(payload: Dict[str, Any]) -> Optional[str]:
    """Siren names a record's creation time ``dateCreated``."""
    value = payload.get("dateCreated")
    return value if value is not None else payload.get("createdAt")


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
    property_id: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "WebhookSubscription":
        return cls(
            id=payload.get("id"),
            target_url=payload.get("targetUrl"),
            events=list(payload.get("events") or []),
            description=payload.get("description"),
            status=payload.get("status"),
            created_at=_created_at(payload),
            signing_secret=payload.get("signingSecret"),
            property_id=payload.get("propertyId"),
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
    property_id: Optional[int] = None
    revoked_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ApiKey":
        return cls(
            id=payload.get("id"),
            key_prefix=payload.get("keyPrefix"),
            label=payload.get("label"),
            status=payload.get("status"),
            scopes=list(payload.get("scopes") or []),
            created_at=_created_at(payload),
            expires_at=payload.get("expiresAt"),
            last_used_at=payload.get("lastUsedAt"),
            raw_key=payload.get("rawKey"),
            property_id=payload.get("propertyId"),
            revoked_at=payload.get("revokedAt"),
            raw=payload,
        )


@dataclass(frozen=True)
class ListPage:
    """One page of records from a reconciliation reader.

    Siren answers a list with ``{items, total, page, perPage, totalPages}``.
    ``data`` holds ``items``, and the other fields come from the same body.
    When a body is a bare array instead, ``page`` and ``per_page`` echo the
    request and ``total`` comes from the ``x-siren-estimated-count`` header.
    Iterating (or indexing) a ``ListPage`` iterates its ``data``.
    """

    data: List[Dict[str, Any]] = field(default_factory=list)
    page: Optional[int] = None
    per_page: Optional[int] = None
    total: Optional[int] = None
    total_pages: Optional[int] = None

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.data[index]
