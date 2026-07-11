"""Event ingestion — the primary integration point (``POST /event/{slug}``)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from ._client import HttpClient
from .types import EventResult

Number = Union[int, float]


def _normalize_line_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Map a line item to the wire format (camelCase, ``quantity`` defaulting to 1).

    Accepts snake_case (``external_id``) or camelCase (``externalId``) keys so
    both idioms work; the JSON on the wire is always camelCase.
    """
    missing = [key for key in ("name", "amount") if key not in item]
    if missing:
        raise ValueError(f"line item is missing required field(s): {', '.join(missing)}")

    normalized: Dict[str, Any] = {}
    external_id = item.get("external_id", item.get("externalId"))
    if external_id is not None:
        normalized["externalId"] = external_id
    normalized["name"] = item["name"]
    quantity = item.get("quantity")
    normalized["quantity"] = 1 if quantity is None else int(quantity)
    normalized["amount"] = item["amount"]
    return normalized


class Events:
    """``siren.events`` — record sales, refunds, referred visits, and custom events."""

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def sale(
        self,
        *,
        source: str,
        external_id: str,
        total: Number,
        tracking_id: int,
        currency: Optional[str] = None,
        items: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> EventResult:
        """Record a completed sale (``POST /event/sale``).

        ``total`` and each line item ``amount`` are in **major** currency units
        (``9.99`` means $9.99). Reward pools compute ``amount x quantity`` per
        line, so ``quantity`` must be honest; a missing ``quantity`` defaults
        to ``1``. Omit ``items`` to treat ``total`` as a single line.
        """
        payload: Dict[str, Any] = {
            "source": source,
            "externalId": external_id,
            "total": total,
            "trackingId": tracking_id,
        }
        if currency is not None:
            payload["currency"] = currency
        if items is not None:
            payload["items"] = [_normalize_line_item(item) for item in items]
        return self._post("sale", payload)

    def refund(self, *, source: str, external_id: str) -> EventResult:
        """Reverse a previously recorded sale (``POST /event/refund``).

        The sale is matched by ``(external_id, source)``; both must match the
        original sale exactly. Raises :class:`siren.NotFoundError` when no
        matching sale transaction exists.
        """
        return self._post("refund", {"source": source, "externalId": external_id})

    def site_visited(
        self, *, collaborator_id: int, user_id: Optional[int] = None
    ) -> EventResult:
        """Record a referred visit, opening an opportunity (``POST /event/site-visited``)."""
        payload: Dict[str, Any] = {"collaboratorId": collaborator_id}
        if user_id is not None:
            payload["userId"] = user_id
        return self._post("site-visited", payload)

    def ingest(self, slug: str, payload: Mapping[str, Any]) -> EventResult:
        """Record a custom event type registered on the org (``POST /event/{slug}``).

        ``payload`` is passed through as the JSON body unchanged.
        """
        return self._post(slug, dict(payload))

    def _post(self, slug: str, payload: Dict[str, Any]) -> EventResult:
        response = self._client.request(
            "POST", f"/event/{slug}", json=payload, retryable=True
        )
        header = response.headers.get("X-Siren-OID")
        opportunity_id: Optional[int] = None
        if header is not None:
            try:
                opportunity_id = int(header)
            except ValueError:
                opportunity_id = None
        return EventResult(opportunity_id=opportunity_id)
