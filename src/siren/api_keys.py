"""API key management (``/api-keys``)."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Sequence, Union

from ._client import HttpClient, parse_json
from .scopes import ApiKeyScope
from .types import ApiKey


class ApiKeys:
    """``siren.api_keys`` — create, list, and revoke API keys.

    These routes take an organization admin's dashboard session. No API key
    scope reaches them, so Siren answers an API key with a 401 that the SDK
    raises as :class:`~siren.ApiKeyNotAcceptedError`.
    """

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def create(
        self,
        *,
        scopes: Sequence[Union[str, ApiKeyScope]],
        label: Optional[str] = None,
        expires_at: Optional[str] = None,
        property_id: Optional[int] = None,
    ) -> ApiKey:
        """Create an API key (``POST /api-keys``). Never auto-retried.

        The returned key's ``raw_key`` (the plaintext ``sk_live_...``
        credential) is provided **once** and cannot be retrieved later — store
        it securely.

        ``scopes`` is required and must not be empty. Take them from
        :class:`~siren.ApiKeyScope`. Event scopes (``event:*``, or
        ``event:{slug}`` for one event type) send events. The ledger scopes,
        such as ``ledger:conversions:read``, read conversions, transactions,
        obligations and payouts. ``webhook-subscriptions:read`` and
        ``webhook-subscriptions:manage`` read and manage webhook
        subscriptions. ``*`` is also accepted. An older resource scope such
        as ``programs:read`` or ``conversions:read`` is refused with a 400.

        ``label`` is an optional name for the key, and is left out of the
        request when not given. ``expires_at`` is an ISO 8601 time in the
        future when the key stops working. ``property_id`` binds the key to
        one of the organization's stores (Properties). Siren stamps it on the
        key's events, the key reads only that store's conversions,
        transactions and obligations, and Siren refuses it on payouts and
        webhook subscriptions with :class:`~siren.StoreKeyNotAllowedError`.
        """
        if isinstance(scopes, str) or not scopes:
            raise ValueError(
                "scopes is required: pass scopes such as [ApiKeyScope.ALL_EVENTS] "
                "or [ApiKeyScope.LEDGER_CONVERSIONS_READ]"
            )
        payload: Dict[str, object] = {
            "scopes": [
                scope.value if isinstance(scope, Enum) else str(scope)
                for scope in scopes
            ]
        }
        if label is not None:
            payload["label"] = label
        if expires_at is not None:
            payload["expiresAt"] = expires_at
        if property_id is not None:
            payload["propertyId"] = property_id
        response = self._client.request(
            "POST", "/api-keys", json=payload, retryable=False
        )
        return ApiKey.from_dict(parse_json(response) or {})

    def list(
        self, *, number: Optional[int] = None, offset: Optional[int] = None
    ) -> List[ApiKey]:
        """List API keys, without raw key material (``GET /api-keys``).

        Siren returns at most 200 keys per request, revoked keys included,
        and an organization can hold 250 active keys. Pass ``offset`` (and
        optionally ``number``, the page size) to read past the first 200.
        """
        params: Dict[str, int] = {}
        if number is not None:
            params["number"] = number
        if offset is not None:
            params["offset"] = offset
        response = self._client.request(
            "GET", "/api-keys", params=params or None, retryable=True
        )
        records = parse_json(response)
        if not isinstance(records, list):
            return []
        return [ApiKey.from_dict(record) for record in records]

    def revoke(self, key_id: int) -> None:
        """Revoke an API key (``DELETE /api-keys/{id}``)."""
        self._client.request("DELETE", f"/api-keys/{key_id}", retryable=True)
