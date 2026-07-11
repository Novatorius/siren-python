"""Webhook signature verification and subscription management."""

from __future__ import annotations

import hashlib
import hmac
import json
from enum import Enum
from typing import Any, List, Optional, Sequence, Union

from ._client import HttpClient, parse_json
from .errors import SignatureVerificationError
from .types import WebhookEvent, WebhookEventType, WebhookSubscription

_SIGNATURE_PREFIX = "sha256="


def _to_bytes(value: Union[bytes, bytearray, str], name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"{name} must be bytes or str, got {type(value).__name__}")


def _event_value(event: Union[str, WebhookEventType, Enum]) -> str:
    return event.value if isinstance(event, Enum) else str(event)


class WebhookSubscriptions:
    """``siren.webhooks.subscriptions`` — manage webhook subscriptions."""

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def create(
        self,
        *,
        target_url: str,
        events: Sequence[Union[str, WebhookEventType]],
        description: Optional[str] = None,
    ) -> WebhookSubscription:
        """Create a subscription (``POST /webhooks``). Never auto-retried.

        The returned subscription's ``signing_secret`` is provided **once** —
        store it; it cannot be retrieved later. Pass
        ``events=[WebhookEventType.ALL]`` (``["*"]``) to subscribe to everything.
        """
        payload: dict = {
            "targetUrl": target_url,
            "events": [_event_value(event) for event in events],
        }
        if description is not None:
            payload["description"] = description
        response = self._client.request(
            "POST", "/webhooks", json=payload, retryable=False
        )
        return WebhookSubscription.from_dict(parse_json(response) or {})

    def list(self) -> List[WebhookSubscription]:
        """List subscriptions (``GET /webhooks``)."""
        response = self._client.request("GET", "/webhooks", retryable=True)
        records = parse_json(response) or []
        return [WebhookSubscription.from_dict(record) for record in records]

    def delete(self, subscription_id: int) -> None:
        """Delete a subscription (``DELETE /webhooks/{id}``)."""
        self._client.request(
            "DELETE", f"/webhooks/{subscription_id}", retryable=True
        )


class Webhooks:
    """``siren.webhooks`` — verify inbound deliveries and manage subscriptions.

    Deliveries arrive as ``POST`` with headers:

    - ``X-Siren-Signature: sha256=<hex hmac>`` — HMAC-SHA256 of the **raw
      request body** keyed by the subscription's signing secret.
    - ``X-Siren-Event`` — the event type, e.g. ``conversion.approved``.
    - ``X-Siren-Delivery`` — unique id per delivery attempt.
    """

    def __init__(self, client: HttpClient) -> None:
        self.subscriptions = WebhookSubscriptions(client)

    @staticmethod
    def verify_signature(
        raw_body: Union[bytes, str],
        signature_header: Optional[str],
        secret: Union[bytes, str],
    ) -> bool:
        """Constant-time check of ``X-Siren-Signature`` against the raw body.

        ``raw_body`` MUST be the exact bytes received on the wire — a
        re-serialized object will not HMAC to the same value. Returns ``False``
        for a missing, malformed, or mismatched signature.
        """
        if not signature_header or not isinstance(signature_header, str):
            return False
        if not signature_header.startswith(_SIGNATURE_PREFIX):
            return False
        provided = signature_header[len(_SIGNATURE_PREFIX):].strip()
        if not provided:
            return False
        expected = hmac.new(
            _to_bytes(secret, "secret"),
            _to_bytes(raw_body, "raw_body"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, provided.lower())

    @classmethod
    def construct_event(
        cls,
        raw_body: Union[bytes, str],
        signature_header: Optional[str],
        secret: Union[bytes, str],
    ) -> WebhookEvent:
        """Verify a delivery's signature and parse it into a :class:`WebhookEvent`.

        This is the one call to make in a webhook handler. ``raw_body`` MUST be
        the exact bytes received (e.g. Flask's ``request.get_data()``, Django's
        ``request.body``) — never a re-serialized object. Raises
        :class:`SignatureVerificationError` when the signature is missing or
        does not match.
        """
        if not signature_header:
            raise SignatureVerificationError(
                "Missing X-Siren-Signature header; cannot verify webhook delivery."
            )
        if not cls.verify_signature(raw_body, signature_header, secret):
            raise SignatureVerificationError(
                "Webhook signature verification failed. Ensure you passed the raw "
                "request body bytes (not a re-serialized object) and the correct "
                "signing secret."
            )
        body = _to_bytes(raw_body, "raw_body")
        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SignatureVerificationError(
                f"Webhook payload is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise SignatureVerificationError("Webhook payload must be a JSON object.")
        return WebhookEvent(
            type=payload.get("type", ""),
            data=payload.get("data") or {},
            delivery_id=payload.get("deliveryId"),
        )
