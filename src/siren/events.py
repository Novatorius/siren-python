"""Event ingestion — the primary integration point (``POST /event/{slug}``)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from ._client import HttpClient, parse_json
from .types import EventResult

Number = Union[int, float]


def _normalize_line_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Map a line item to the wire format (camelCase, ``quantity`` defaulting to 1).

    Accepts snake_case (``external_id``) or camelCase (``externalId``) keys so
    both idioms work; the JSON on the wire is always camelCase. Siren needs
    only ``amount``. ``name`` and ``external_id`` are sent when given.
    """
    if "amount" not in item:
        raise ValueError("line item is missing required field(s): amount")

    normalized: Dict[str, Any] = {}
    external_id = item.get("external_id", item.get("externalId"))
    if external_id is not None:
        normalized["externalId"] = external_id
    name = item.get("name")
    if name is not None:
        normalized["name"] = name
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
        external_id: Union[str, int],
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

        ``external_id`` may be a number, which Siren stores as text. Each line
        item needs ``amount``, and ``name``, ``external_id`` and ``quantity``
        are optional.

        ``currency`` must be a currency the organization uses. Without it the
        lines carry no currency and read as the organization's default.

        Sending the same ``(source, external_id)`` again records nothing new.
        Read the outcome from ``result.status``: ``created``, ``recorded``,
        ``duplicate``, ``unattributed`` or ``ignored`` (or ``queued``).
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

    def refund(self, *, source: str, external_id: Union[str, int]) -> EventResult:
        """Reverse a previously recorded sale (``POST /event/refund``).

        The sale is matched by ``(external_id, source)``; both must match the
        original sale exactly. Raises :class:`siren.NotFoundError` when no
        matching sale transaction exists, unless the organization's processing
        is paused. Then Siren queues the refund (``result.queued``) and checks
        the sale when it replays the queue.
        """
        return self._post("refund", {"source": source, "externalId": external_id})

    def site_visited(
        self, *, collaborator_id: int, user_id: Optional[int] = None
    ) -> EventResult:
        """Record a referred visit, opening an opportunity (``POST /event/site-visited``).

        ``result.status`` is ``attributed``, ``recorded`` (no active program
        of the collaborator rewards a visit), ``not_attributed`` (the
        collaborator is missing or not active, and nothing was written; see
        ``result.body["reason"]``) or ``queued``.
        """
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
        body = parse_json(response)
        if not isinstance(body, dict):
            body = {}
        # A site visit, queued or live, also names its opportunity in the body.
        body_opportunity = body.get("opportunityId")
        if opportunity_id is None and isinstance(body_opportunity, int) and not isinstance(
            body_opportunity, bool
        ):
            opportunity_id = body_opportunity
        return EventResult(
            opportunity_id=opportunity_id,
            http_status=response.status_code,
            body=body,
        )
