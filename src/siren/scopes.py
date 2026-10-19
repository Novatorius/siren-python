"""The scopes an API key may hold (``api_keys.create``)."""

from __future__ import annotations

from enum import Enum
from typing import Tuple


class ApiKeyScope(str, Enum):
    """The named scopes an API key may be given.

    A key sends events with event scopes: ``ALL_EVENTS`` (``event:*``), or
    :func:`event_scope` for one event type. ``FULL_ACCESS`` (``*``) is still
    accepted and grants what ``ALL_EVENTS`` grants.

    A key also reads the ledger and manages webhook subscriptions:

    - ``LEDGER_CONVERSIONS_READ``, ``LEDGER_TRANSACTIONS_READ``,
      ``LEDGER_OBLIGATIONS_READ`` and ``LEDGER_PAYOUTS_READ`` open ``list()``
      on ``conversions``, ``transactions``, ``obligations`` and ``payouts``,
      and Siren's read by id on each.
    - ``WEBHOOK_SUBSCRIPTIONS_READ`` lists subscriptions, reads one, and reads
      the event catalog and a subscription's deliveries.
    - ``WEBHOOK_SUBSCRIPTIONS_MANAGE`` does all of that, and creates, changes
      and deletes subscriptions and sends a test delivery
      (``POST /webhooks/{id}/test``).

    Siren grants each of these six only by its exact name. ``*``,
    ``event:*``, wildcards such as ``ledger:*``, and older names such as
    ``conversions:read`` grant none of them, and Siren refuses those older
    names on a new key with a 400. No scope reaches API key management,
    which takes a signed-in dashboard session.
    """

    ALL_EVENTS = "event:*"
    FULL_ACCESS = "*"
    SALE = "event:sale"
    REFUND = "event:refund"
    SITE_VISITED = "event:site-visited"
    LEDGER_CONVERSIONS_READ = "ledger:conversions:read"
    LEDGER_TRANSACTIONS_READ = "ledger:transactions:read"
    LEDGER_OBLIGATIONS_READ = "ledger:obligations:read"
    LEDGER_PAYOUTS_READ = "ledger:payouts:read"
    WEBHOOK_SUBSCRIPTIONS_READ = "webhook-subscriptions:read"
    WEBHOOK_SUBSCRIPTIONS_MANAGE = "webhook-subscriptions:manage"

    def __str__(self) -> str:
        return self.value


#: The four ledger read scopes.
LEDGER_READ_SCOPES: Tuple[ApiKeyScope, ...] = (
    ApiKeyScope.LEDGER_CONVERSIONS_READ,
    ApiKeyScope.LEDGER_TRANSACTIONS_READ,
    ApiKeyScope.LEDGER_OBLIGATIONS_READ,
    ApiKeyScope.LEDGER_PAYOUTS_READ,
)


def event_scope(slug: str) -> str:
    """The scope for one event type, such as ``event:loyalty-points-earned``."""
    if not isinstance(slug, str) or not slug:
        raise ValueError("An event scope needs a non-empty event slug.")
    return f"event:{slug}"
