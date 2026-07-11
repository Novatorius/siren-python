"""API key management (``/api-keys``)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ._client import HttpClient, parse_json
from .types import ApiKey


class ApiKeys:
    """``siren.api_keys`` — create, list, and revoke API keys."""

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def create(
        self, *, label: str, scopes: Optional[Sequence[str]] = None
    ) -> ApiKey:
        """Create an API key (``POST /api-keys``). Never auto-retried.

        The returned key's ``raw_key`` (the plaintext ``sk_live_...``
        credential) is provided **once** and cannot be retrieved later — store
        it securely. Omit ``scopes`` for full access.
        """
        payload: Dict[str, object] = {"label": label}
        if scopes is not None:
            payload["scopes"] = list(scopes)
        response = self._client.request(
            "POST", "/api-keys", json=payload, retryable=False
        )
        return ApiKey.from_dict(parse_json(response) or {})

    def list(self) -> List[ApiKey]:
        """List API keys, without raw key material (``GET /api-keys``)."""
        response = self._client.request("GET", "/api-keys", retryable=True)
        records = parse_json(response) or []
        return [ApiKey.from_dict(record) for record in records]

    def revoke(self, key_id: int) -> None:
        """Revoke an API key (``DELETE /api-keys/{id}``)."""
        self._client.request("DELETE", f"/api-keys/{key_id}", retryable=True)
