"""Typed exception hierarchy for the Siren SDK.

One base error (:class:`SirenError`), with subclasses selected by HTTP status.
Every HTTP-derived error carries ``message``, ``code`` (the API's
machine-readable ``error.code`` when present), ``status_code``, and ``data``
(the API's ``error.data`` payload when present).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class SirenError(Exception):
    """Base class for every error raised by the Siren SDK."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        data: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.data = data

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"{type(self).__name__}(message={self.message!r}, "
            f"code={self.code!r}, status_code={self.status_code!r})"
        )


class BadRequestError(SirenError):
    """400 — the request was malformed."""


class AuthenticationError(SirenError):
    """401 — missing or invalid API key."""


class PermissionError(SirenError):  # noqa: A001 - name mandated by the SDK contract
    """403 — the API key lacks the required scope."""


class NotFoundError(SirenError):
    """404 — resource not found (e.g. a refund for an unknown sale)."""


class ConflictError(SirenError):
    """409 — the request conflicts with existing state."""


class ValidationError(SirenError):
    """422 — the request body failed validation.

    ``field_errors`` exposes the per-field errors from ``error.data`` when the
    API provides them.
    """

    @property
    def field_errors(self) -> Dict[str, Any]:
        return self.data if isinstance(self.data, dict) else {}


class RateLimitError(SirenError):
    """429 — too many requests. ``retry_after`` is seconds, when the server says."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        data: Optional[Any] = None,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code, data=data)
        self.retry_after = retry_after


class ApiError(SirenError):
    """5xx — the Siren API failed, or an unexpected status was returned."""


class ConnectionError(SirenError):  # noqa: A001 - name mandated by the SDK contract
    """The request never completed: DNS, TCP, TLS, or timeout failure."""


class SignatureVerificationError(SirenError):
    """Webhook signature verification failed (no HTTP status)."""


_STATUS_TO_ERROR = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def error_from_response(response: Any) -> SirenError:
    """Build the right :class:`SirenError` subclass from an HTTP response.

    The failure body is polymorphic. Most endpoints emit a plain-string error
    (``{"error": "<message>"}``); structured errors (login/entitlement) emit
    ``{"error": {"message", "code", "data"?}}``. Both are parsed; ``code`` and
    ``data`` are populated only from the object form. Falls back to the HTTP
    status text when the body is missing, not JSON, or has no ``error`` key.
    """
    status = response.status_code
    try:
        body = response.json()
    except Exception:
        body = None

    fallback = f"Siren API request failed with status {status}"
    message = fallback
    code: Optional[str] = None
    data: Optional[Any] = None

    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        message = error or fallback
    elif isinstance(error, dict):
        message = error.get("message") or fallback
        code = error.get("code")
        data = error.get("data")

    error_cls = _STATUS_TO_ERROR.get(status, ApiError)
    if error_cls is RateLimitError:
        retry_after: Optional[int] = None
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                retry_after = int(header)
            except ValueError:
                retry_after = None
        return RateLimitError(
            message, code=code, status_code=status, data=data, retry_after=retry_after
        )
    return error_cls(message, code=code, status_code=status, data=data)
