"""Reconciliation readers — thin paginated GET wrappers over Siren's ledger."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ._client import HttpClient, parse_json
from .types import ListPage

_ESTIMATED_COUNT_HEADER = "x-siren-estimated-count"


def _snake_to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


class ListResource:
    """A paginated read-only collection (``GET /<path>``).

    Deliberately thin: these exist to reconcile Siren's ledger against your
    own records, not to be a full admin client.
    """

    _path: str = ""

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def list(
        self,
        *,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        **filters: Any,
    ) -> ListPage:
        """Fetch one page of records.

        ``page`` and ``per_page`` map to the API's ``page``/``perPage`` query
        args; any additional keyword filters are passed through as query args
        (snake_case names are converted to camelCase on the wire).
        """
        params: Dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if per_page is not None:
            params["perPage"] = per_page
        for key, value in filters.items():
            if value is not None:
                params[_snake_to_camel(key)] = value

        response = self._client.request(
            "GET", self._path, params=params or None, retryable=True
        )
        body = parse_json(response)
        records = list(body) if isinstance(body, list) else []

        total: Optional[int] = None
        header = response.headers.get(_ESTIMATED_COUNT_HEADER)
        if header is not None:
            try:
                total = int(header)
            except ValueError:
                total = None

        return ListPage(
            data=records,
            page=page,
            per_page=per_page,
            total=total,
        )


class Conversions(ListResource):
    """``siren.conversions`` — ``GET /conversions``."""

    _path = "/conversions"


class Transactions(ListResource):
    """``siren.transactions`` — ``GET /transactions``."""

    _path = "/transactions"


class Obligations(ListResource):
    """``siren.obligations`` — ``GET /obligations``."""

    _path = "/obligations"


class Payouts(ListResource):
    """``siren.payouts`` — ``GET /payouts``."""

    _path = "/payouts"
