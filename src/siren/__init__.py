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
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ConnectionError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    SignatureVerificationError,
    SirenError,
    ValidationError,
)
from .types import (
    ApiKey,
    ApiKeyStatus,
    ConversionStatus,
    EventResult,
    EventSlug,
    FulfillmentStatus,
    ListPage,
    ObligationStatus,
    OpportunityStatus,
    PayoutStatus,
    TransactionStatus,
    WebhookEvent,
    WebhookEventType,
    WebhookSubscription,
    WebhookSubscriptionStatus,
)

__all__ = [
    "__version__",
    "Siren",
    # Errors
    "SirenError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "RateLimitError",
    "ApiError",
    "ConnectionError",
    "SignatureVerificationError",
    # Types
    "ApiKey",
    "EventResult",
    "ListPage",
    "WebhookEvent",
    "WebhookEventType",
    "WebhookSubscription",
    # Taxonomy
    "ApiKeyStatus",
    "ConversionStatus",
    "EventSlug",
    "FulfillmentStatus",
    "ObligationStatus",
    "OpportunityStatus",
    "PayoutStatus",
    "TransactionStatus",
    "WebhookSubscriptionStatus",
]
