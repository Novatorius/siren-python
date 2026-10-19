"""Typed exception hierarchy for the Siren SDK.

One base error (:class:`SirenError`), with subclasses selected by HTTP status,
and by ``error.code`` for an API key's refusals (:class:`ErrorCode`).
Every HTTP-derived error carries ``message``, ``code`` (the API's
machine-readable ``error.code`` when present), ``status_code``, and ``data``
(the API's ``error.data`` payload, or its ``error.context`` when there is no
``data``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ErrorCode:
    """The ``error.code`` values this SDK raises a dedicated error for.

    Compare ``error.code`` against these, not the message, which can change.
    """

    #: 401: the route does not accept an API key, such as every ``/api-keys``
    #: route. It takes a signed-in dashboard session.
    #: :class:`ApiKeyNotAcceptedError`.
    API_KEY_NOT_ACCEPTED = "api_key_not_accepted"

    #: 403: the API key holds none of the scopes the route needs.
    #: ``error.context.scope`` names one and ``error.context.anyOf`` lists
    #: every scope that opens the route. :class:`MissingScopeError`.
    MISSING_SCOPE = "missing_scope"

    #: 403: an API key bound to one store asked for records that are not kept
    #: by store, such as payouts or webhook subscriptions.
    #: :class:`StoreKeyNotAllowedError`.
    STORE_KEY_NOT_ALLOWED = "store_key_not_allowed"


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
    """Per-field errors from a 422 error payload.

    A ``failedValidations`` object is returned as is. Any other object
    payload is returned whole.
    """
    if not isinstance(data, dict):
        return {}
    failed = data.get("failedValidations")
    if isinstance(failed, dict):
        return failed
    return data


class BadRequestError(SirenError):
    """400 — the request was malformed or failed validation.

    Siren answers a failed field check with 400, ``code`` set to
    ``VALIDATION_FAILED``, and ``error.context.failedValidations`` holding one
    entry per broken rule: ``{"field", "message", "type", "context"}``, such as
    ``{"field": "total", "message": "total is required.", "type": "REQUIRED"}``.
    ``field_errors`` returns those entries, in Siren's order.
    """

    @property
    def field_errors(self) -> List[Dict[str, Any]]:
        if not isinstance(self.data, dict):
            return []
        failed = self.data.get("failedValidations")
        if not isinstance(failed, list):
            return []
        return [
            entry
            for entry in failed
            if isinstance(entry, dict)
            and isinstance(entry.get("field"), str)
            and isinstance(entry.get("message"), str)
        ]


class AuthenticationError(SirenError):
    """401 — missing, invalid, expired or revoked API key."""


class ApiKeyNotAcceptedError(AuthenticationError):
    """401 with ``code`` ``api_key_not_accepted``.

    The key may be valid, but the route takes a signed-in dashboard session
    instead, as every ``api_keys`` route does. Retrying with the same key
    fails again.
    """


class PermissionError(SirenError):  # noqa: A001 - name mandated by the SDK contract
    """403 — the credential may not do this."""


def _context(data: Any) -> Dict[str, Any]:
    return data if isinstance(data, dict) else {}


class MissingScopeError(PermissionError):
    """403 with ``code`` ``missing_scope``.

    The API key holds none of the scopes the route needs, such as a key
    without ``ledger:conversions:read`` reading conversions. Create a key
    that holds one of :attr:`any_of`. Retrying with the same key fails again.
    """

    @property
    def scope(self) -> Optional[str]:
        """The scope the route needs, from ``error.context.scope``, when Siren names it."""
        scope = _context(self.data).get("scope")
        return scope if isinstance(scope, str) and scope else None

    @property
    def any_of(self) -> List[str]:
        """Every scope that opens the route, from ``error.context.anyOf``.

        Holding any one is enough. Empty when Siren does not list them.
        """
        any_of = _context(self.data).get("anyOf")
        if not isinstance(any_of, list):
            return []
        return [scope for scope in any_of if isinstance(scope, str) and scope]


class StoreKeyNotAllowedError(PermissionError):
    """403 with ``code`` ``store_key_not_allowed``.

    An API key bound to one store asked for payouts or webhook subscriptions,
    which are not kept by store. Use an organization-wide key, one created
    without a ``property_id``.
    """

    @property
    def property_id(self) -> Optional[int]:
        """The store the key is bound to, from ``error.context.propertyId``, when Siren names it."""
        property_id = _context(self.data).get("propertyId")
        if isinstance(property_id, bool) or not isinstance(property_id, int):
            return None
        return property_id


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

_CODE_TO_ERROR = {
    (401, ErrorCode.API_KEY_NOT_ACCEPTED): ApiKeyNotAcceptedError,
    (403, ErrorCode.MISSING_SCOPE): MissingScopeError,
    (403, ErrorCode.STORE_KEY_NOT_ALLOWED): StoreKeyNotAllowedError,
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

    error_cls = _CODE_TO_ERROR.get((status, code)) or _STATUS_TO_ERROR.get(status, ApiError)
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
