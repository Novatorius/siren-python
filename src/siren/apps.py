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
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from ._client import HttpClient, parse_json
from .errors import ApiError, AuthenticationError, ErrorCode, SignatureVerificationError
from .types import AppAnswerReceipt, AppRequest

if TYPE_CHECKING:  # pragma: no cover
    from .app_requests import AppRequestHandler

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


class _BodyRefused(SignatureVerificationError):
    """The signature held, but the signed body is not what Siren sends. The
    request handler answers this with 400 and a failed signature with 401."""


def _check_tolerance(method: str, tolerance_seconds: Optional[int]) -> int:
    tolerance = DEFAULT_TOLERANCE_SECONDS if tolerance_seconds is None else tolerance_seconds
    if (
        not isinstance(tolerance, int)
        or isinstance(tolerance, bool)
        or tolerance < 1
        or tolerance > MAX_TOLERANCE_SECONDS
    ):
        raise ValueError(f"{method} takes tolerance_seconds as a whole number from 1 to {MAX_TOLERANCE_SECONDS}.")
    return tolerance


def _check_signature(body_bytes: bytes, headers: Mapping[str, HeaderValue], secret: str, tolerance: int, clock: float) -> int:
    """Steps 3 to 6 of :meth:`Apps.verify_request`: the V2 signature over
    ``"{t}."`` and the raw body, the timestamp header, and the replay window.
    Returns the signed time. Webhook events at the app's address are signed
    the same way (WebhookSignature::headers() in Siren)."""
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
    return signed_at


def _checked_answer(method: str, request_id: Any, answer: Any) -> Dict[str, Any]:
    """The answer as :meth:`AppRequests.answer` sends it, or :class:`ValueError`
    for one Siren would refuse or that can't be written."""
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
    return copy


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
        return self._send("apps.requests.answer()", request_id, answer, retryable=False)

    def _send(self, method: str, request_id: Any, answer: Any, *, retryable: bool) -> AppAnswerReceipt:
        """Checks and posts an answer. ``retryable`` is true only from the
        request handler, which always resends the same bytes, and Siren takes
        the same answer again as a duplicate. A refusal (4xx) is never resent."""
        copy = _checked_answer(method, request_id, answer)
        path = f"/apps/requests/{request_id}/answer"
        try:
            response = self._client.request("POST", path, json=copy, retryable=retryable)
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

    def request_handler(
        self,
        signing_secret: str,
        *,
        tolerance_seconds: Optional[int] = None,
        run_later: Optional[Callable[[Callable[[], None]], Any]] = None,
        on_error: Optional[Callable[..., Any]] = None,
    ) -> "AppRequestHandler":
        """One address for everything Siren sends the app: it checks each
        request as :meth:`verify_request` does, runs the function registered
        for its kind and key with :meth:`AppRequestHandler.on`, replies, and
        posts an ask's answer with this client's key. See
        :class:`~siren.AppRequestHandler` for the replies and the faces to
        mount (``flask``, ``django``, ``asgi``, ``wsgi``).

        ``run_later`` is handed an ask's work, a callable with no arguments,
        in place of running it after the reply, for a host that stops work
        once it has replied. ``on_error(error, request)`` gets every failure
        the reply can't show. It logs to the ``siren`` logger by default.

        Raises :class:`ValueError` for an empty secret, a tolerance outside 1
        to :data:`MAX_TOLERANCE_SECONDS`, and a ``run_later`` or ``on_error``
        that is not callable.
        """
        from .app_requests import AppRequestHandler

        return AppRequestHandler(
            self.requests,
            signing_secret,
            tolerance_seconds=tolerance_seconds,
            run_later=run_later,
            on_error=on_error,
        )

    @staticmethod
    def split_pool(
        pool: Mapping[str, Any],
        weights: Union[Mapping[Any, Any], Iterable[Tuple[Any, Any]]],
    ) -> Dict[str, int]:
        """Split a pool Siren offered by weight, for a distribution or
        incentive resolver's answer. It reads nothing over the network.

        ``pool`` is the question's ``pool``: ``value`` in the stored integer
        form, and ``currency`` with ``scale`` and ``decimalPlaces``.
        ``weights`` maps each id to a weight, such as a metric's ``score``, or
        is a list of ``(id, weight)`` pairs.

        Returns ``{str(id): amount}``. Every amount is a whole number of the
        currency's step, ``10 ** (scale - decimalPlaces)`` in the stored form
        (1 for dollars, 100 for yen), rounded down, so the sum never passes
        ``pool["value"]``. Part of the pool may stay unpaid, which Siren
        takes. When every weight is 0 the pool is split evenly.

        Raises :class:`ValueError` for a pool without a whole ``value`` of 0
        or more, a ``scale`` or ``decimalPlaces`` that is not a whole number,
        an id that is not text or a whole number, an id given twice, and a
        weight that is negative, not finite, or not a number.
        """
        method = "apps.split_pool()"
        if not isinstance(pool, Mapping):
            raise ValueError(f"{method} takes the question's pool, a mapping with value and currency.")
        value = pool.get("value")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{method} needs the pool's value as a whole number of 0 or more, as Siren sends it.")
        currency = pool.get("currency")
        currency = currency if isinstance(currency, Mapping) else {}
        scale = currency.get("scale", 0)
        places = currency.get("decimalPlaces", scale)
        for name, number in (("scale", scale), ("decimalPlaces", places)):
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                raise ValueError(f"{method} needs the currency's {name} as a whole number of 0 or more.")
        step = 10 ** max(0, scale - places)

        if isinstance(weights, (str, bytes)):
            raise ValueError(f"{method} takes the weights as a mapping of id to weight or a list of (id, weight) pairs.")
        pairs = list(weights.items()) if isinstance(weights, Mapping) else list(weights)
        exact: Dict[str, Fraction] = {}
        for pair in pairs:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError(f"{method} takes the weights as a mapping of id to weight or a list of (id, weight) pairs.")
            key, weight = pair
            if isinstance(key, bool) or not isinstance(key, (str, int)):
                raise ValueError(f"{method} takes each id as text or a whole number, and got {key!r}.")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise ValueError(f"{method} takes each weight as a number, and the weight for {key} is {weight!r}.")
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(f"{method} takes each weight as a finite number of 0 or more, and the weight for {key} is {weight!r}.")
            name = str(key)
            if name in exact:
                raise ValueError(f"{method} got the id {name} twice.")
            exact[name] = Fraction(weight)

        if not exact:
            return {}
        steps = value // step
        total = sum(exact.values(), Fraction(0))
        if total == 0:
            even = steps // len(exact)
            return {name: even * step for name in exact}
        # Fractions keep the arithmetic exact, so rounding each share down
        # keeps the sum at or under the pool.
        return {name: math.floor(steps * weight / total) * step for name, weight in exact.items()}

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
        tolerance = _check_tolerance(method, tolerance_seconds)
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

        signed_at = _check_signature(body_bytes, headers, secret, tolerance, clock)

        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise _BodyRefused("The signed body is not JSON.") from error
        if not _is_envelope(body):
            raise _BodyRefused("The signed body is not an app request of version 1.")

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
