"""Internal HTTP core: auth, retries, and error mapping."""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

import httpx

from ._version import __version__
from .errors import ConnectionError, RateLimitError, SirenError, error_from_response

DEFAULT_BASE_URL = "https://api.sirenaffiliates.com/siren/v1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2

_INITIAL_BACKOFF = 0.5
_MAX_BACKOFF = 8.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpClient:
    """Thin wrapper over ``httpx.Client`` implementing the SDK's HTTP policy.

    Retries network errors and 429/5xx responses with exponential backoff on
    idempotent operations (``retryable=True``); never on management writes.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key is required (an sk_live_... key)")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(0, int(max_retries))
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": f"siren-python/{__version__}",
            },
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        retryable: bool = True,
    ) -> httpx.Response:
        """Perform a request, raising a typed :class:`SirenError` on failure."""
        attempts = (self.max_retries + 1) if retryable else 1
        error: SirenError

        for attempt in range(attempts):
            response: Optional[httpx.Response]
            try:
                response = self._client.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                response = None
                error = ConnectionError(f"Request to Siren API failed: {exc}")
                error.__cause__ = exc
            else:
                if response.status_code < 400:
                    return response
                error = error_from_response(response)

            retryable_failure = response is None or response.status_code in _RETRYABLE_STATUSES
            if attempt >= attempts - 1 or not retryable_failure:
                raise error
            time.sleep(self._backoff_delay(attempt, error))

        raise error  # pragma: no cover - loop always returns or raises

    def _backoff_delay(self, attempt: int, error: SirenError) -> float:
        if isinstance(error, RateLimitError) and error.retry_after is not None:
            return float(error.retry_after)
        return min(_INITIAL_BACKOFF * (2**attempt), _MAX_BACKOFF)


def parse_json(response: httpx.Response) -> Any:
    """Return the parsed JSON body verbatim (bare array or object), or ``None``.

    The Siren API has no ``{"data": ...}`` success envelope: list endpoints
    return a bare JSON array and create/read-by-id endpoints return a bare JSON
    object.
    """
    if not response.content:
        return None
    try:
        return response.json()
    except Exception:
        return None
