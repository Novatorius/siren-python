"""The Siren client."""

from __future__ import annotations

from typing import Optional

from ._client import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    HttpClient,
)
from .api_keys import ApiKeys
from .events import Events
from .reconciliation import Conversions, Obligations, Payouts, Transactions
from .webhooks import Webhooks


class Siren:
    """Client for the Siren affiliate platform API.

    Usage::

        import siren

        client = siren.Siren(api_key="sk_live_...")
        result = client.events.sale(
            source="stripe",
            external_id="cs_test_a1b2c3",
            total=49.99,
            tracking_id=4021,
        )
        print(result.opportunity_id)

    Args:
        api_key: Your ``sk_live_...`` API key (Settings -> API Keys in the
            Siren dashboard, or ``api_keys.create``).
        base_url: API base URL; defaults to production
            (``https://api.sirenaffiliates.com/siren/v1``). Point it at
            staging or a local dev server to test.
        timeout: Request timeout in seconds. Defaults to 30.
        max_retries: Retry count for network errors and 429/5xx responses,
            with exponential backoff. Defaults to 2. Applies to idempotent
            reads and event ingestion only — management writes (create key,
            create subscription) are never auto-retried.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._client = HttpClient(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.events = Events(self._client)
        self.webhooks = Webhooks(self._client)
        self.api_keys = ApiKeys(self._client)
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
