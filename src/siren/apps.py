"""An app's side of Siren's exchange: checking a request Siren signed and
sent the app, and answering an ask.

Mirrors ``siren.apps.verifyRequest()`` and ``siren.apps.requests.answer()`` in
the Node SDK, and follows the same steps in the same order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ._client import HttpClient, parse_json
from .errors import ApiError, AuthenticationError, ErrorCode, SignatureVerificationError
from .types import AppAnswerReceipt, AppRequest

#: How far the signed time may be from this machine's clock by default, in seconds.
DEFAULT_TOLERANCE_SECONDS = 300

#: The longest tolerance :meth:`Apps.verify_request` takes, an hour.
MAX_TOLERANCE_SECONDS = 3600

#: The longest answer Siren takes, in bytes of JSON. Siren refuses past it too.
MAX_ANSWER_BYTES = 16384

#: The event type of a request Siren sends an app.
REQUEST_EVENT = "app.request"

_REQUEST_ID = re.compile(r"^apr_[0-9a-f]{32}$")
_HEX_SIGNATURE = re.compile(r"^[0-9a-fA-F]{64}$")
_SIGNED_TIME = re.compile(r"^\d{1,15}$")

# Siren's key check answers a missing or unknown key with 401 and no code,
# before the answer route can send ``app_answer_unauthorized``, so the SDK
# gives that 401 the code and these words.
_UNAUTHORIZED_ANSWER = (
    "Siren did not accept this key on the answer route. An app answers with its own key, "
    "the SIREN_API_KEY saved beside its app file. When that key is lost, run "
    "siren apps rotate-key <app key> to make a new one. When the app holds no key, as after "
    "its key was revoked, run siren apps add-key <app key>."
)

HeaderValue = Union[str, Sequence[str]]


def _header(headers: Mapping[str, HeaderValue], name: str) -> Optional[str]:
    """The first value of a header, matched without regard to case."""
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() != wanted:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        return value if isinstance(value, str) else None
    return None


def _read_signature_header(header: str) -> Tuple[int, List[str]]:
    """Reads ``t=<seconds>,v1=<hex>[,v1=<hex>...]``: the signed time and every
    ``v1`` in lowercase. A ``v1`` that is not 64 hex characters can match
    nothing and is dropped."""
    times: List[str] = []
    signatures: List[str] = []
    v1_count = 0
    for part in header.split(","):
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if name == "t":
            times.append(value)
        if name == "v1":
            v1_count += 1
            if _HEX_SIGNATURE.match(value):
                signatures.append(value.lower())
    if len(times) != 1 or not _SIGNED_TIME.match(times[0]) or v1_count == 0:
        raise SignatureVerificationError(
            "The X-Siren-Signature-V2 header cannot be read. Siren sends t=<seconds>,v1=<hex signature>."
        )
    return int(times[0]), signatures


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _is_envelope(body: Any) -> bool:
    """Whether the body is an app request of version 1, as Siren's
    ``AppRequestEnvelope`` builds it."""
    if not _is_object(body):
        return False
    item = body.get("item")
    mode = body.get("mode")
    version = body.get("version")
    return (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == 1
        and isinstance(body.get("id"), str)
        and bool(_REQUEST_ID.match(body["id"]))
        and body.get("type") == REQUEST_EVENT
        and isinstance(body.get("app"), str)
        and body["app"] != ""
        and _is_object(item)
        and isinstance(item.get("kind"), str)
        and isinstance(item.get("key"), str)
        and isinstance(item.get("id"), str)
        and isinstance(body.get("createdAt"), str)
        and _is_object(body.get("data"))
        and mode in ("tell", "ask")
        and (mode == "tell" or (isinstance(body.get("answerBy"), str) and isinstance(body.get("answerUrl"), str)))
    )


class AppRequests:
    """``siren.apps.requests`` — answer an ask Siren sent the app.

    The client must be made with the app's own API key, the one Siren gave
    the app.
    """

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def answer(self, request_id: str, answer: Mapping[str, Any]) -> AppAnswerReceipt:
        """Answer an ask (``POST /apps/requests/{id}/answer``). Sent once and
        never retried, so a lost connection never sends a second answer. The
        answer is the JSON body alone.

        Siren takes the first answer. The same answer again is a duplicate
        and changes nothing. A different second answer is refused with 409
        ``app_request_already_answered``, a :class:`~siren.ConflictError`.
        An answer that doesn't fit the feature's kind is refused with 422.

        Refused before sending, with :class:`ValueError`: an id that is not a
        request key, an answer that is not a mapping, one with no text
        ``outcome``, one JSON cannot write, and one longer than
        :data:`MAX_ANSWER_BYTES` as JSON.

        Raises :class:`~siren.AuthenticationError` with code
        ``app_answer_unauthorized`` when Siren takes no answer from this key,
        and :class:`~siren.ApiError` when Siren answers 2xx with a body that
        is not an accepted answer.
        """
        method = "apps.requests.answer()"
        if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
            raise ValueError(
                f"{method} needs a request id of apr_ and 32 lowercase hex characters, and got {request_id!r}."
            )
        if not isinstance(answer, Mapping):
            raise ValueError(
                f"{method} takes the answer as a mapping, such as {{'outcome': 'paid', 'reference': 'TX-1'}}."
            )
        copy: Dict[str, Any] = dict(answer)
        outcome = copy.get("outcome")
        if not isinstance(outcome, str) or outcome == "":
            raise ValueError(f"{method} needs a text outcome in the answer, such as 'paid' or 'resolved'.")
        try:
            encoded = json.dumps(copy, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError(f"{method} could not write the answer as JSON.") from error
        if len(encoded) > MAX_ANSWER_BYTES:
            raise ValueError(
                f"{method} takes an answer of at most {MAX_ANSWER_BYTES} bytes as JSON, which Siren also refuses past."
            )

        path = f"/apps/requests/{request_id}/answer"
        try:
            response = self._client.request("POST", path, json=copy, retryable=False)
        except AuthenticationError as error:
            if error.status_code == 401 and not error.code:
                raise AuthenticationError(
                    _UNAUTHORIZED_ANSWER,
                    code=ErrorCode.APP_ANSWER_UNAUTHORIZED,
                    status_code=401,
                    data=error.data,
                ) from error
            raise

        reply = parse_json(response)
        duplicate = reply.get("duplicate") if isinstance(reply, dict) else None
        if (
            not isinstance(reply, dict)
            or reply.get("status") != "accepted"
            or ("duplicate" in reply and not isinstance(duplicate, bool))
        ):
            raise ApiError(
                f"Siren answered POST {path} with a body that is not an accepted answer.",
                status_code=response.status_code,
                data=reply,
            )
        return AppAnswerReceipt(status="accepted", duplicate=duplicate is True)


class Apps:
    """``siren.apps`` — check a request Siren signed and sent the app, and
    answer an ask through :attr:`requests`."""

    def __init__(self, client: HttpClient) -> None:
        self.requests = AppRequests(client)

    @staticmethod
    def verify_request(
        raw_body: Union[bytes, bytearray, str],
        headers: Mapping[str, HeaderValue],
        secret: str,
        *,
        tolerance_seconds: Optional[int] = None,
        now: Optional[float] = None,
    ) -> AppRequest:
        """Check a request Siren signed and sent the app, and return it parsed.
        It reads nothing over the network.

        In this order:

        1. The arguments: a non-empty secret, and a whole-number tolerance
           from 1 to :data:`MAX_TOLERANCE_SECONDS`.
        2. ``X-Siren-Event``, when present, must be ``app.request``. Any
           other webhook is checked with ``webhooks.construct_event()``.
        3. ``X-Siren-Signature-V2``, ``t=<seconds>,v1=<hex>``, must be
           present and readable.
        4. ``X-Siren-Timestamp``, when present, must equal the signed ``t``.
        5. One ``v1`` must be the HMAC-SHA256 of ``"{t}."`` and the raw body
           under the secret, compared in constant time.
        6. ``t`` must be within the tolerance of ``now``, so an old request
           replayed later is refused.
        7. The body must be an app request of version 1.

        ``raw_body`` MUST be the exact bytes received, such as Flask's
        ``request.get_data()``. Headers are matched without regard to case,
        and each may be a string or a list of strings.

        Raises :class:`ValueError` for an argument it can't use, and
        :class:`~siren.SignatureVerificationError` with the reason when the
        request fails a check.
        """
        method = "apps.verify_request()"
        if not isinstance(secret, str) or secret == "":
            raise ValueError(f"{method} needs the app's signing secret, which Siren showed when it made the app.")
        tolerance = DEFAULT_TOLERANCE_SECONDS if tolerance_seconds is None else tolerance_seconds
        if (
            not isinstance(tolerance, int)
            or isinstance(tolerance, bool)
            or tolerance < 1
            or tolerance > MAX_TOLERANCE_SECONDS
        ):
            raise ValueError(
                f"{method} takes tolerance_seconds as a whole number from 1 to {MAX_TOLERANCE_SECONDS}."
            )
        clock = time.time() if now is None else now
        if isinstance(clock, bool) or not isinstance(clock, (int, float)) or not math.isfinite(clock):
            raise ValueError(f"{method} takes now as a finite number of Unix seconds.")
        if isinstance(raw_body, str):
            body_bytes = raw_body.encode("utf-8")
        elif isinstance(raw_body, (bytes, bytearray)):
            body_bytes = bytes(raw_body)
        else:
            raise ValueError(
                f"{method} needs the raw request body, as bytes or str, exactly as received. A parsed object cannot be checked."
            )

        event = _header(headers, "X-Siren-Event")
        if event is not None and event != REQUEST_EVENT:
            raise SignatureVerificationError(
                f"This is a {event} webhook, not an app request. Check it with webhooks.construct_event()."
            )

        v2 = _header(headers, "X-Siren-Signature-V2")
        if v2 is None:
            raise SignatureVerificationError("The request has no X-Siren-Signature-V2 header.")
        signed_at, signatures = _read_signature_header(v2)

        stamp = _header(headers, "X-Siren-Timestamp")
        if stamp is not None and stamp != str(signed_at):
            raise SignatureVerificationError(
                "The X-Siren-Timestamp header differs from the time X-Siren-Signature-V2 signed."
            )

        expected = hmac.new(secret.encode("utf-8"), f"{signed_at}.".encode("ascii") + body_bytes, hashlib.sha256).hexdigest()
        matched = False
        for signature in signatures:
            # Every candidate is compared, so the time taken says nothing about which matched.
            matched = hmac.compare_digest(expected, signature) or matched
        if not matched:
            raise SignatureVerificationError(
                "The request signature does not match. Pass the raw body exactly as received and the signing secret of this app."
            )

        if abs(clock - signed_at) > tolerance:
            raise SignatureVerificationError(
                f"The request's signed timestamp is more than {tolerance} seconds from this machine's clock, "
                "so it may be a replay. Check the clock if every request fails."
            )

        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise SignatureVerificationError("The signed body is not JSON.") from error
        if not _is_envelope(body):
            raise SignatureVerificationError("The signed body is not an app request of version 1.")

        return AppRequest(
            version=1,
            id=body["id"],
            type=body["type"],
            mode=body["mode"],
            app=body["app"],
            item=dict(body["item"]),
            created_at=body["createdAt"],
            answer_by=body.get("answerBy") if isinstance(body.get("answerBy"), str) else None,
            answer_url=body.get("answerUrl") if isinstance(body.get("answerUrl"), str) else None,
            data=dict(body["data"]),
            timestamp=signed_at,
            delivery_id=_header(headers, "X-Siren-Delivery"),
            raw=body,
        )
