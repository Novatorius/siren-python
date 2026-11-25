"""Event ingestion — the primary integration point (``POST /event/{slug}``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Union

from ._client import HttpClient, parse_json
from ._tokens import AppTokens
from .errors import SirenError
from .types import BrowserToken, EventResult

if TYPE_CHECKING:  # pragma: no cover
    from .browser_tokens import BrowserTokenHandler

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
    """``siren.events`` — record sales, refunds, referred visits, and custom events.

    A custom event takes only a short-lived app token, which :meth:`ingest`
    gets from the app's key and keeps fresh on its own. For a page that sends
    a custom event from the browser, see :meth:`browser_token_handler`.
    """

    def __init__(self, client: HttpClient, tokens: Optional[AppTokens] = None) -> None:
        self._client = client
        self._tokens = tokens if tokens is not None else AppTokens(client)

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
        """Send a custom event of the app (``POST /event/{slug}``).

        ``payload`` is passed through as the JSON body unchanged.

        The send carries a short-lived app token, not the key. The client
        gets one from the key the first time, keeps it, and gets a new one
        when fewer than two minutes of its life are left. Threads that need
        one at the same time share one request. A send Siren answers with 401
        drops the token and goes again once with a new one. A key that
        belongs to no app sends with the key.
        """
        token = self._tokens.server_token()
        if token is None:
            return self._post(slug, dict(payload))
        try:
            return self._post(slug, dict(payload), token)
        except SirenError as exc:
            if exc.status_code != 401:
                raise
            self._tokens.drop_server_token(token)
            fresh = self._tokens.server_token()
            if fresh is None:
                raise
            return self._post(slug, dict(payload), fresh)

    def browser_token(self, event_key: str) -> BrowserToken:
        """A browser token for one custom event of the app.

        Call it on the app's server, never in the page. The token lasts ten
        minutes and works only for this event, from a domain the event
        allows. The client reuses it until fewer than 60 seconds are left,
        so a busy page costs one grant every nine minutes or so.

        Raises :class:`siren.BadRequestError` with ``code`` ``invalid_scope``
        when the app has no such event or the event allows no domain.
        """
        if not event_key:
            raise ValueError("events.browser_token() requires a non-empty event key")
        return self._tokens.browser_token(event_key)

    def browser_token_handler(
        self, *, events: Optional[Sequence[str]] = None
    ) -> "BrowserTokenHandler":
        """The browser token address for the app's server, mounted in one line.

        It reads the event key from a JSON body ``{"event": "..."}`` or the
        query ``?event=``. ``events``, when given, is the only event keys a
        page may ask for. Call the handler with ``(body, query)`` for
        ``(status, json)``, or mount ``handler.wsgi`` or ``handler.asgi``.
        See :class:`siren.BrowserTokenHandler`.
        """
        from .browser_tokens import BrowserTokenHandler

        return BrowserTokenHandler(self, events=events)

    def _post(self, slug: str, payload: Dict[str, Any], token: Optional[str] = None) -> EventResult:
        response = self._client.request(
            "POST", f"/event/{slug}", json=payload, retryable=True, bearer=token
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
