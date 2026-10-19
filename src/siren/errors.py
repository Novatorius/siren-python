"""Typed exception hierarchy for the Siren SDK.

One base error (:class:`SirenError`), with subclasses selected by HTTP status.
Every HTTP-derived error carries ``message``, ``code`` (the API's
machine-readable ``error.code`` when present), ``status_code``, and ``data``
(the API's ``error.data`` payload, or its ``error.context`` when there is no
``data``).
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


def _field_errors(data: Any) -> Dict[str, Any]:
    """Per-field messages from an error payload.

    Siren's REST layer refuses a request that fails validation with a 400 and
    ``error.context = {"type": "VALIDATION_FAILED", "failedValidations":
    {field: [messages]}}``. Any other object payload is returned as is.
    """
    if not isinstance(data, dict):
        return {}
    failed = data.get("failedValidations")
    if isinstance(failed, dict):
        return failed
    return data


class BadRequestError(SirenError):
    """400 — the request was malformed or failed validation.

    Siren answers a failed field check with 400. ``field_errors`` maps each
    field that failed to its messages, and ``code`` is ``VALIDATION_FAILED``.
    """

    @property
    def field_errors(self) -> Dict[str, Any]:
        if isinstance(self.data, dict) and isinstance(self.data.get("failedValidations"), dict):
            return self.data["failedValidations"]
        return {}


class AuthenticationError(SirenError):
    """401 — missing or invalid API key."""


class PermissionError(SirenError):  # noqa: A001 - name mandated by the SDK contract
    """403 — the API key lacks the required scope."""


class NotFoundError(SirenError):
    """404 — resource not found (e.g. a refund for an unknown sale)."""


class ConflictError(SirenError):
    """409 — the request conflicts with existing state."""


class ValidationError(SirenError):
    """422 — the request was understood but refused.

    Most field checks answer 400 (:class:`BadRequestError`). A few routes
    still answer 422. ``field_errors`` exposes the per-field errors when the
    API provides them.
    """

    @property
    def field_errors(self) -> Dict[str, Any]:
        return _field_errors(self.data)


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

    The failure body is polymorphic. Some endpoints emit a plain-string error
    (``{"error": "<message>"}``). Siren's REST layer emits
    ``{"error": {"message", "code"?, "context"}}``, and a few errors carry
    ``data``. ``data`` is read from ``error.data``, or from ``error.context``
    when that is a non-empty object. ``code`` is ``error.code``, or the
    context's ``type`` (such as ``VALIDATION_FAILED``) when there is no code.
    Falls back to the HTTP status text when the body is missing, not JSON, or
    has no ``error`` key.
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
        raw_code = error.get("code")
        code = raw_code if isinstance(raw_code, str) else None
        data = error.get("data")
        context = error.get("context")
        if data is None and isinstance(context, dict) and context:
            data = context
        if code is None and isinstance(data, dict) and isinstance(data.get("type"), str):
            code = data["type"]

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
