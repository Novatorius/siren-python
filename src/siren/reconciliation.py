"""Reconciliation readers — thin paginated GET wrappers over Siren's ledger."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ._client import HttpClient, parse_json
from .types import ListPage

_ESTIMATED_COUNT_HEADER = "x-siren-estimated-count"

#: The largest page Siren serves. A larger ``number`` is lowered to this.
MAX_PAGE_SIZE = 200

#: The page size Siren uses when a list request names none.
DEFAULT_PAGE_SIZE = 10


def _snake_to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class ListResource:
    """A paginated read-only collection (``GET /<path>``).

    Deliberately thin: these exist to reconcile Siren's ledger against your
    own records, not to be a full admin client.

    These routes take an API key that holds the collection's ledger scope,
    such as ``ledger:conversions:read``, a signed-in dashboard session, or a
    connected site's token. A key bound to one store reads only its own
    store's conversions, transactions and obligations, and is refused on
    payouts with :class:`~siren.StoreKeyNotAllowedError`.
    """

    _path: str = ""

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def list(
        self,
        *,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        property_id: Optional[int] = None,
        **filters: Any,
    ) -> ListPage:
        """Fetch one page of records.

        Siren pages with ``number`` (page size) and ``offset`` (first row).
        ``page`` and ``per_page`` are turned into those, with ``per_page``
        capped at 200, the largest page Siren serves. ``number`` and
        ``offset`` may also be passed directly, and win over ``page`` and
        ``per_page``. ``property_id`` narrows an organization-wide read to
        one store, and is sent as ``propertyId``. Any other keyword filters
        pass through as query args (snake_case names are converted to
        camelCase on the wire).
        """
        if property_id is not None:
            filters["property_id"] = property_id
        params: Dict[str, Any] = {}
        for key, value in filters.items():
            if value is not None:
                params[_snake_to_camel(key)] = value

        if per_page is not None and "number" not in params:
            params["number"] = per_page
        if "number" in params:
            size = _as_int(params["number"])
            if size is not None and size > MAX_PAGE_SIZE:
                params["number"] = MAX_PAGE_SIZE
        if page is not None and "offset" not in params:
            size = _as_int(params.get("number"))
            if size is None:
                size = DEFAULT_PAGE_SIZE
            params["number"] = size
            params["offset"] = max(0, (int(page) - 1) * size)

        response = self._client.request(
            "GET", self._path, params=params or None, retryable=True
        )
        body = parse_json(response)

        number = _as_int(params.get("number"))
        offset = _as_int(params.get("offset"))
        requested_page: Optional[int] = None
        if number is not None and number > 0 and offset is not None:
            requested_page = offset // number + 1

        if isinstance(body, dict) and isinstance(body.get("items"), list):
            body_page = _as_int(body.get("page"))
            body_per_page = _as_int(body.get("perPage"))
            return ListPage(
                data=list(body["items"]),
                page=body_page if body_page is not None else requested_page,
                per_page=body_per_page if body_per_page is not None else number,
                total=_as_int(body.get("total")),
                total_pages=_as_int(body.get("totalPages")),
            )

        records = list(body) if isinstance(body, list) else []
        return ListPage(
            data=records,
            page=requested_page,
            per_page=number,
            total=_as_int(response.headers.get(_ESTIMATED_COUNT_HEADER)),
        )


class Conversions(ListResource):
    """``siren.conversions`` — ``GET /conversions``. An API key needs ``ledger:conversions:read``."""

    _path = "/conversions"


class Transactions(ListResource):
    """``siren.transactions`` — ``GET /transactions``. An API key needs ``ledger:transactions:read``."""

    _path = "/transactions"


class Obligations(ListResource):
    """``siren.obligations`` — ``GET /obligations``. An API key needs ``ledger:obligations:read``."""

    _path = "/obligations"


class Payouts(ListResource):
    """``siren.payouts`` — ``GET /payouts``. An API key needs ``ledger:payouts:read``."""

    _path = "/payouts"
