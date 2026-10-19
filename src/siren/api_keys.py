"""API key management (``/api-keys``)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ._client import HttpClient, parse_json
from .types import ApiKey


class ApiKeys:
    """``siren.api_keys`` — create, list, and revoke API keys.

    These routes take an organization admin's dashboard session. Siren Cloud
    does not accept an API key on them, because an API key only sends events.
    """

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def create(
        self,
        *,
        label: str,
        scopes: Sequence[str],
        expires_at: Optional[str] = None,
        property_id: Optional[int] = None,
    ) -> ApiKey:
        """Create an API key (``POST /api-keys``). Never auto-retried.

        The returned key's ``raw_key`` (the plaintext ``sk_live_...``
        credential) is provided **once** and cannot be retrieved later — store
        it securely.

        ``scopes`` is required and must not be empty. An API key sends events
        and does nothing else, so Siren issues event scopes only:
        ``event:*`` for every event Siren takes with a wildcard, or
        ``event:{slug}`` for one event type. ``*`` is also accepted. A
        resource scope such as ``programs:read`` is refused with a 400.

        ``expires_at`` is an ISO 8601 time in the future when the key stops
        working. ``property_id`` ties the key to one of the organization's
        Properties.
        """
        if isinstance(scopes, str) or not scopes:
            raise ValueError(
                "scopes is required: pass event scopes such as ['event:*'] or ['event:sale']"
            )
        payload: Dict[str, object] = {"label": label, "scopes": list(scopes)}
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
