"""Siren — official Python SDK for the Siren affiliate platform API.

Quickstart::

    import siren

    client = siren.Siren(api_key="sk_live_...")
    client.events.sale(
        source="stripe",
        external_id="cs_test_a1b2c3",
        total=49.99,
        tracking_id=4021,
    )
"""

from ._version import __version__
from .client import Siren
from .errors import (
    ApiError,
    ApiKeyNotAcceptedError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ConnectionError,
    ErrorCode,
    MissingScopeError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    SignatureVerificationError,
    SirenError,
    StoreKeyNotAllowedError,
    ValidationError,
)
from .scopes import LEDGER_READ_SCOPES, ApiKeyScope, event_scope
from .types import (
    ApiKey,
    EventResult,
    ListPage,
    WebhookEvent,
    WebhookEventType,
    WebhookSubscription,
)

__all__ = [
    "__version__",
    "Siren",
    # Errors
    "SirenError",
    "BadRequestError",
    "AuthenticationError",
    "ApiKeyNotAcceptedError",
    "PermissionError",
    "MissingScopeError",
    "StoreKeyNotAllowedError",
    "ErrorCode",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "RateLimitError",
    "ApiError",
    "ConnectionError",
    "SignatureVerificationError",
    # Scopes
    "ApiKeyScope",
    "LEDGER_READ_SCOPES",
    "event_scope",
    # Types
    "ApiKey",
    "EventResult",
    "ListPage",
    "WebhookEvent",
    "WebhookEventType",
    "WebhookSubscription",
]
