"""Webhook signature verification and subscription management."""

from __future__ import annotations

import hashlib
import hmac
import json
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Union

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
    """``siren.webhooks.subscriptions`` — manage webhook subscriptions.

    These routes take an API key with a webhook scope, or a signed-in
    dashboard session. ``webhook-subscriptions:read`` opens :meth:`list`, and
    ``webhook-subscriptions:manage`` opens every method here. The key must be
    organization-wide, because subscriptions are not kept by store. A key
    bound to one store gets a :class:`~siren.StoreKeyNotAllowedError`.
    """

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

        Siren stores any event name it is given and does not check it against
        its catalog, so a misspelled or retired name is accepted and never
        delivered. :class:`WebhookEventType` lists the names Siren sends.
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

    def list(
        self, *, number: Optional[int] = None, offset: Optional[int] = None
    ) -> List[WebhookSubscription]:
        """List subscriptions (``GET /webhooks``).

        Siren returns at most 200 subscriptions per request. ``number`` (the
        page size) and ``offset`` page through more.
        """
        params: Dict[str, int] = {}
        if number is not None:
            params["number"] = number
        if offset is not None:
            params["offset"] = offset
        response = self._client.request(
            "GET", "/webhooks", params=params or None, retryable=True
        )
        records = parse_json(response)
        if not isinstance(records, list):
            return []
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
    - ``X-Siren-Delivery`` — the event id (``evt_...``). It stays the same
      when Siren retries the delivery, so use it to drop repeats.

    The body of a subscription made with ``subscriptions.create`` is the
    event's payload alone, such as ``{"conversionId": 991}``. The event type
    travels only in ``X-Siren-Event``, so pass that header to
    :meth:`construct_event` as ``event_type``. A subscription that belongs to
    a connected site gets a versioned envelope instead, with ``type``,
    ``id`` and ``data`` in the body, and :meth:`construct_event` reads those.
    Siren sends both with the same headers. The envelope's ``id`` is the
    ``X-Siren-Delivery`` value and its ``type`` is the ``X-Siren-Event``
    value, and that match is how :meth:`construct_event` tells an envelope
    from a payload that has ``version``, ``type`` and ``data`` keys of its own.
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
        *,
        event_type: Optional[str] = None,
        delivery_id: Optional[str] = None,
    ) -> WebhookEvent:
        """Verify a delivery's signature and parse it into a :class:`WebhookEvent`.

        This is the one call to make in a webhook handler. ``raw_body`` MUST be
        the exact bytes received (e.g. Flask's ``request.get_data()``, Django's
        ``request.body``) — never a re-serialized object. Pass the
        ``X-Siren-Event`` header as ``event_type`` and the ``X-Siren-Delivery``
        header as ``delivery_id``::

            event = client.webhooks.construct_event(
                request.get_data(),
                request.headers.get("X-Siren-Signature"),
                signing_secret,
                event_type=request.headers.get("X-Siren-Event"),
                delivery_id=request.headers.get("X-Siren-Delivery"),
            )

        For a plain subscription the whole body becomes ``event.data`` and
        ``event.type`` comes from ``event_type``. For a connected site's
        versioned envelope, ``type``, ``data`` and ``id`` come from the body.
        When either header is passed, a body counts as an envelope only when
        its ``id`` equals ``delivery_id`` and its ``type`` equals
        ``event_type``. Called with neither header, a body shaped like an
        envelope is read as one.
        Raises :class:`SignatureVerificationError` when the signature is
        missing or does not match, when the body is not a JSON object, and
        when a plain subscription's delivery arrives without ``event_type``.
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

        event_type = event_type.strip() if event_type and event_type.strip() else None
        delivery_id = delivery_id.strip() if delivery_id and delivery_id.strip() else None

        if _is_envelope(payload, event_type, delivery_id):
            return WebhookEvent(
                type=payload["type"],
                data=payload["data"],
                delivery_id=payload["id"],
                raw=payload,
            )

        # A plain subscription's body never names its event. Without the
        # X-Siren-Event header there is no type to dispatch on, so refuse the
        # delivery loudly rather than hand back an event with an empty type
        # that matches no handler. Code written for 0.1.0 passes no headers.
        if event_type is None:
            raise SignatureVerificationError(
                "Siren names a delivery's event in the X-Siren-Event header, and this body "
                "does not carry it. Pass the header as event_type (and X-Siren-Delivery as "
                "delivery_id) to construct_event."
            )

        return WebhookEvent(
            type=event_type,
            data=payload,
            delivery_id=delivery_id,
            raw=payload,
        )


def _is_envelope(
    payload: dict, event_type: Optional[str], delivery_id: Optional[str]
) -> bool:
    """Whether a body is the versioned envelope a connected site's subscription gets.

    Siren sends an envelope and a plain payload with the same headers, and a
    plain payload, such as a custom event's, may have ``version``, ``type``
    and ``data`` keys of its own. Only an envelope carries its own delivery
    id: Siren writes the ``X-Siren-Delivery`` value into the envelope's
    ``id`` and the ``X-Siren-Event`` value into its ``type``. So when the
    caller passes either header, the body must match them. Without either
    header the shape alone decides.
    """
    version = payload.get("version")
    envelope_id = payload.get("id")
    envelope_type = payload.get("type")
    shaped = (
        isinstance(version, int)
        and not isinstance(version, bool)
        and isinstance(envelope_id, str)
        and envelope_id != ""
        and isinstance(envelope_type, str)
        and envelope_type != ""
        and isinstance(payload.get("data"), dict)
    )
    if not shaped:
        return False
    if event_type is None and delivery_id is None:
        return True
    return (
        delivery_id is not None
        and envelope_id == delivery_id
        and (event_type is None or envelope_type == event_type)
    )
