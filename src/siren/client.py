"""The Siren client."""

from __future__ import annotations

import os
from typing import Optional

from ._client import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    HttpClient,
)
from .apps import Apps
from .events import Events
from .reconciliation import Conversions, Obligations, Payouts, Transactions
from .webhooks import Webhooks


class Siren:
    """Client for the Siren affiliate platform API.

    Usage::

        import siren

        client = siren.Siren()  # reads SIREN_API_KEY
        result = client.events.sale(
            source="stripe",
            external_id="cs_test_a1b2c3",
            total=49.99,
            tracking_id=4021,
        )
        print(result.opportunity_id)

    Args:
        api_key: Your API key. An app uses its own key, which Siren showed
            once when it made the app. Read from ``SIREN_API_KEY`` when not
            passed.
        base_url: The Siren API address. Siren Cloud's,
            ``https://api.sirenaffiliates.com/siren/v1``, by default. Set it
            only for a local or sandbox Siren.
        timeout: Request timeout in seconds. Defaults to 30.
        max_retries: Retry count for network errors and 429/5xx responses,
            with exponential backoff. Defaults to 2. Applies to idempotent
            reads and event ingestion only — management writes (create key,
            create subscription) are never auto-retried.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("SIREN_API_KEY") or None
        if not api_key or not isinstance(api_key, str):
            raise ValueError(
                "siren.Siren() needs an API key. Pass api_key, or set SIREN_API_KEY. "
                "An app uses its own key, which Siren showed once when it made the app."
            )
        self._client = HttpClient(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.events = Events(self._client)
        self.webhooks = Webhooks(self._client)
        self.apps = Apps(self._client)
        self.conversions = Conversions(self._client)
        self.transactions = Transactions(self._client)
        self.obligations = Obligations(self._client)
        self.payouts = Payouts(self._client)

    @property
    def base_url(self) -> str:
        return self._client.base_url

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "Siren":
        return self

    def __exit__(self, exc_type: Optional[type], exc: Optional[BaseException], tb: object) -> None:
        self.close()
