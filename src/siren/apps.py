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
import os
import re
import time
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union, cast

from ._client import HttpClient, parse_json
from .errors import ApiError, AppHandlerError, AuthenticationError, ErrorCode, SignatureVerificationError
from .types import (
    APP_REQUEST_STATUSES,
    ApiKeyScopeGroups,
    AppRecord,
    AppWithRawKey,
    AppAnswerReceipt,
    AppAsk,
    AppFeature,
    AppRequest,
    AppRequestDetail,
    AppRequestRecord,
    AppRequestStatus,
    AppTell,
    RemovedAppFeature,
)

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
            "The X-Siren-Signature-V2 header cannot be read. Siren sends t=<seconds>,v1=<hex signature>.",
            code="signature_malformed",
        )
    return int(times[0]), signatures


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _envelope_problem(body: Any) -> Optional[str]:
    """What is wrong with a body that should be an app request of version 1,
    as Siren's ``AppRequestEnvelope`` builds it, naming the first field that
    fails and its rule, or ``None`` when it is one."""
    if not _is_object(body):
        return "The signed body must be a JSON object."
    version = body.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        return "The request's `version` must be 1."
    if not isinstance(body.get("id"), str) or not _REQUEST_ID.match(body["id"]):
        return "The request's `id` must be `apr_` followed by 32 lowercase hex characters."
    if body.get("type") != REQUEST_EVENT:
        return f"The request's `type` must be `{REQUEST_EVENT}`."
    mode = body.get("mode")
    if mode not in ("tell", "ask"):
        return "The request's `mode` must be `tell` or `ask`."
    if "test" in body and not isinstance(body["test"], bool):
        return "The request's `test` must be true or false."
    if not isinstance(body.get("app"), str) or body["app"] == "":
        return "The request's `app` must be the app's key, as text."
    feature = body.get("feature")
    if not _is_object(feature):
        return "The request's `feature` must be an object with `kind`, `key` and `id`."
    for name in ("kind", "key", "id"):
        if not isinstance(feature.get(name), str):
            return f"The request's `feature.{name}` must be text."
    if not isinstance(body.get("createdAt"), str):
        return "The request's `createdAt` must be a date as text."
    if not _is_object(body.get("data")):
        return "The request's `data` must be a JSON object."
    if mode == "ask":
        if not isinstance(body.get("answerBy"), str):
            return "An ask's `answerBy` must be a date as text."
        if not isinstance(body.get("answerUrl"), str):
            return "An ask's `answerUrl` must be the answer address as text."
    return None


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
        raise SignatureVerificationError("The request has no X-Siren-Signature-V2 header.", code="signature_missing")
    signed_at, signatures = _read_signature_header(v2)

    stamp = _header(headers, "X-Siren-Timestamp")
    if stamp is not None and stamp != str(signed_at):
        raise SignatureVerificationError(
            "The X-Siren-Timestamp header differs from the time X-Siren-Signature-V2 signed.",
            code="timestamp_mismatch",
        )

    expected = hmac.new(secret.encode("utf-8"), f"{signed_at}.".encode("ascii") + body_bytes, hashlib.sha256).hexdigest()
    matched = False
    for signature in signatures:
        # Every candidate is compared, so the time taken says nothing about which matched.
        matched = hmac.compare_digest(expected, signature) or matched
    if not matched:
        raise SignatureVerificationError(
            "The request signature does not match. Pass the raw body exactly as received and the signing secret of this app.",
            code="signature_mismatch",
        )

    if abs(clock - signed_at) > tolerance:
        raise SignatureVerificationError(
            f"The request's signed timestamp is more than {tolerance} seconds from this machine's clock, "
            "so it may be a replay. Check the clock if every request fails.",
            code="timestamp_out_of_tolerance",
        )
    return signed_at


def _plain(value: Any) -> Any:
    """``value`` with every mapping made a dict and every tuple a list, so
    any mapping the answer types accept, such as a read-only one, writes as
    JSON."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


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
    copy: Dict[str, Any] = _plain(answer)
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


#: The kinds whose question holds the engagement type's ``args``.
_ARGS_KINDS = ("engagementCalculation", "collaboratorGroupStructure")


def _read_data(kind: Any, data: Mapping[str, Any]) -> Dict[str, Any]:
    """A request's ``data`` as the SDK's types describe it. Siren sends an
    engagement type with no settings as ``"args": []``, because PHP writes an
    empty array as a list (AppEngagementQuestion::args() and the
    ``json_encode`` in AppRequestService on Siren feat/apps). The SDK reads it
    as ``{}``, so ``args.get(...)`` works either way. ``raw`` keeps the body
    as sent."""
    copy = dict(data)
    if kind in _ARGS_KINDS and copy.get("args") == []:
        copy["args"] = {}
    return copy


def _request_id(request_id: Any, method: str) -> str:
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        raise ValueError(
            f"{method} needs a request id of apr_ and 32 lowercase hex characters, and got {request_id!r}."
        )
    return request_id


class AppRequests:
    """``siren.apps.requests`` — the requests Siren sent the organization's
    apps.

    :meth:`answer` takes the app's own API key, the one Siren gave the app,
    or an admin's session to answer by hand. :meth:`list`, :meth:`retrieve`
    and :meth:`resend` take an admin's session, as every app route does. An
    app's key gets 401 ``api_key_not_accepted``, raised as
    :class:`~siren.ApiKeyNotAcceptedError`.
    """

    #: The params :meth:`list` sends, the ones ``GET /apps/requests`` reads
    #: (ListAppRequests.php).
    LIST_FILTERS: Tuple[str, ...] = ("status", "limit", "before")

    #: The most requests one page holds. Siren lists 50 when ``limit`` is absent.
    MAX_LIST_LIMIT = 100

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def list(
        self,
        *,
        status: "Optional[AppRequestStatus]" = None,
        limit: Optional[int] = None,
        before: Optional[str] = None,
    ) -> "List[AppRequestRecord]":
        """The organization's latest requests, newest first, from
        ``GET /apps/requests``. Retried as a read.

        - ``status`` keeps one status, one of :data:`~siren.APP_REQUEST_STATUSES`.
        - ``limit`` is how many a page holds, 1 to :attr:`MAX_LIST_LIMIT`.
          Siren lists 50 without it.
        - ``before`` is a request id. The page then holds only requests older
          than it, so the last id of one page reads the next. Siren lists
          none for an id the organization has no request for.

        Siren ignores ``limit`` and ``before`` for ``needs_attention``, whose
        list comes whole.

        Raises :class:`ValueError` before sending for a status outside
        :data:`~siren.APP_REQUEST_STATUSES`, a ``limit`` that is not a whole
        number from 1 to 100, or a ``before`` that is not a request id, and
        :class:`~siren.ApiError` when the 2xx body holds no list of requests
        in ``requests``.
        """
        if status is not None and status not in APP_REQUEST_STATUSES:
            raise ValueError(f"apps.requests.list() takes a status of {', '.join(APP_REQUEST_STATUSES)}.")
        params: Dict[str, Any] = {} if status is None else {"status": status}
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.MAX_LIST_LIMIT:
                raise ValueError(
                    f"apps.requests.list() takes a limit that is a whole number from 1 to {self.MAX_LIST_LIMIT}, "
                    f"and got {limit!r}."
                )
            params["limit"] = limit
        if before is not None:
            if not isinstance(before, str) or not _REQUEST_ID.match(before):
                raise ValueError(
                    "apps.requests.list() takes a before that is a request id, such as the last id of the page "
                    f"before, and got {before!r}."
                )
            params["before"] = before
        response = self._client.request("GET", "/apps/requests", params=params)
        body = parse_json(response)
        rows = body.get("requests") if isinstance(body, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ApiError(
                "Siren answered GET /apps/requests with no list of requests in `requests`.",
                status_code=response.status_code,
                data=body,
            )
        return cast("List[AppRequestRecord]", rows)

    def retrieve(self, request_id: str) -> "AppRequestDetail":
        """One request by its id, however old, from
        ``GET /apps/requests/{id}``: the list's fields, the ``data`` Siren
        sent the app, the ``answer`` it took or ``None``, and
        ``answeredByUser``, the person who answered by hand or ``None``.
        Retried as a read.

        Raises :class:`ValueError` before sending for an id that is not
        ``apr_`` and 32 lowercase hex characters,
        :class:`~siren.NotFoundError` for a request the organization lacks
        (``app_request_not_found``, GetAppRequest.php), and
        :class:`~siren.ApiError` when the 2xx body is not this request.
        """
        key = _request_id(request_id, "apps.requests.retrieve()")
        path = f"/apps/requests/{key}"
        response = self._client.request("GET", path)
        body = parse_json(response)
        problem = None
        if not isinstance(body, dict) or body.get("id") != key:
            problem = "another request's id"
        elif not isinstance(body.get("data"), dict):
            problem = "no object in `data`"
        elif body.get("answer") is not None and not isinstance(body.get("answer"), dict):
            problem = "an `answer` that is neither an object nor null"
        else:
            user = body.get("answeredByUser")
            if user is not None and not (
                isinstance(user, dict) and isinstance(user.get("id"), int) and isinstance(user.get("email"), str)
            ):
                problem = "an `answeredByUser` that is neither {id, email} nor null"
        if problem is not None:
            raise ApiError(f"Siren answered GET {path} with {problem}.", status_code=response.status_code, data=body)
        return cast("AppRequestDetail", body)

    def resend(self, request_id: str) -> "AppRequestRecord":
        """Send a request again under the same key, with
        ``POST /apps/requests/{id}/resend``, and return it as it now stands.
        An ask gets a new answer window. A request that holds an answer
        Siren has not applied is not sent to the app again: Siren applies
        its stored answer instead, even while the app is off. Sent once.

        Raises :class:`ValueError` before sending for an id that is not
        ``apr_`` and 32 lowercase hex characters,
        :class:`~siren.ConflictError` with ``app_request_not_resendable`` for
        a request that is ``applied``, a ``delivered`` tell, or one Siren
        would send while its app is off, :class:`~siren.NotFoundError` for a
        request the organization lacks (ResendAppRequest.php), and
        :class:`~siren.ApiError` when the 2xx body is not this request.
        """
        key = _request_id(request_id, "apps.requests.resend()")
        path = f"/apps/requests/{key}/resend"
        response = self._client.request("POST", path, retryable=False)
        body = parse_json(response)
        if not isinstance(body, dict) or body.get("id") != key:
            raise ApiError(f"Siren answered POST {path} with a body that is not this request.", status_code=response.status_code, data=body)
        return cast("AppRequestRecord", body)

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


_APP_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Words Siren keeps for its own routes under ``/apps``, never an app's key.
RESERVED_APP_KEYS: Tuple[str, ...] = ("requests", "events", "scopes", "kinds")


def _app_path(app: Any, method: str) -> str:
    """``/apps/{app}``, or :class:`ValueError` naming ``method`` for an app
    key that is not lowercase letters and digits joined by single dashes, at
    most 64 characters, or that is one of :data:`RESERVED_APP_KEYS`."""
    if not isinstance(app, str) or len(app) > 64 or not _APP_KEY.match(app) or app in RESERVED_APP_KEYS:
        raise ValueError(
            f"{method} needs an app key of lowercase letters and digits joined by single dashes, "
            f"at most 64 characters and never {', '.join(RESERVED_APP_KEYS)}, and got {app!r}."
        )
    return f"/apps/{app}"


def _feature_id(feature_id: Any, method: str) -> int:
    if isinstance(feature_id, bool) or not isinstance(feature_id, int) or feature_id < 1:
        raise ValueError(f"{method} needs a positive whole number feature id, and got {feature_id!r}.")
    return feature_id


def _picked(params: Any, allowed: Tuple[str, ...], method: str, what: str) -> Dict[str, Any]:
    if not isinstance(params, Mapping):
        raise ValueError(f"{method} needs {what} as a dict.")
    for name in params:
        if name not in allowed:
            if name in ("kind", "key") and "kind" not in allowed:
                raise ValueError(
                    f"{method} cannot change {name}. A feature's kind and key never change once Siren makes it."
                )
            raise ValueError(f"{method} does not take {name}.")
    return dict(params)


def _is_feature(body: Any) -> bool:
    return (
        _is_object(body)
        and isinstance(body.get("id"), int)
        and not isinstance(body.get("id"), bool)
        and isinstance(body.get("kind"), str)
        and isinstance(body.get("key"), str)
    )


def _checked_feature(response: Any, route: str) -> "AppFeature":
    body = parse_json(response)
    if not _is_feature(body):
        raise ApiError(f"Siren answered {route} with a body that is not a feature.", status_code=response.status_code, data=body)
    return cast("AppFeature", body)


class AppFeatures:
    """``siren.apps.features`` — one app's features through their own
    routes, ``/apps/{app}/features``, so the dashboard and code can add one
    without declaring the whole app. A feature's kind and key lock once
    Siren makes it.

    These routes take an organization admin's dashboard session, passed as
    the client's key. Siren answers an API key with a 401 that the SDK
    raises as :class:`~siren.ApiKeyNotAcceptedError`, and a member limited
    to some stores with a 403, reads included, since a feature's page names
    the records that pick it.

    Each method refuses a bad app key or feature id with
    :class:`ValueError` before it sends anything.
    """

    #: The fields :meth:`create` sends, those ``POST /apps/{app}/features``
    #: reads.
    CREATE_FIELDS: Tuple[str, ...] = (
        "kind", "key", "name", "description", "currencies", "answerWindowMinutes",
        "scoresMoney", "conversionTypes", "fields", "domains",
    )
    #: The fields :meth:`update` sends. Siren refuses a changed kind or key.
    UPDATE_FIELDS: Tuple[str, ...] = CREATE_FIELDS[2:]

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def create(self, app: str, feature: Mapping[str, Any]) -> "AppFeature":
        """Add one feature to the app (``POST /apps/{app}/features``), sent
        once, and return it with ``uses`` and ``usedBy``. Siren answers 201,
        or 200 when an archived feature of the same kind and key comes back
        with the new fields.

        Raises :class:`ValueError` before sending for a feature that is not
        a dict, a field not in :attr:`CREATE_FIELDS`, or no ``kind`` or
        ``name``. With no ``key``, Siren makes one from the name. Siren
        answers 422 with the field for the rest
        (:class:`~siren.ValidationError`), 409
        ``app_features_need_key_and_address`` when the app lacks a key or an
        address (:class:`~siren.ConflictError`), and 404 for an app the
        organization lacks.
        """
        method = "apps.features.create()"
        path = f"{_app_path(app, method)}/features"
        sent = _picked(feature, self.CREATE_FIELDS, method, "the feature")
        for name in ("kind", "name"):
            if sent.get(name) is None:
                raise ValueError(f"{method} needs a {name}.")
        response = self._client.request("POST", path, json=sent, retryable=False)
        return _checked_feature(response, f"POST {path}")

    def retrieve(self, app: str, feature_id: int) -> "AppFeature":
        """One feature by its id, with ``uses``, how many records pick it,
        ``usedBy``, up to 100 of them, and ``openRequests``
        (``GET /apps/{app}/features/{id}``). Retried as a read. Siren
        answers 404, ``Feature not found.``, for a feature another app
        holds."""
        method = "apps.features.retrieve()"
        path = f"{_app_path(app, method)}/features/{_feature_id(feature_id, method)}"
        return _checked_feature(self._client.request("GET", path), f"GET {path}")

    def update(self, app: str, feature_id: int, changes: Mapping[str, Any]) -> "AppFeature":
        """Change a feature's name, description or settings
        (``PATCH /apps/{app}/features/{id}``), sent once, and return it. A
        field left out keeps its value.

        Raises :class:`ValueError` before sending for changes that are not a
        dict and any field not in :attr:`UPDATE_FIELDS`, ``kind`` and
        ``key`` included, since a feature's kind and key never change.
        """
        method = "apps.features.update()"
        path = f"{_app_path(app, method)}/features/{_feature_id(feature_id, method)}"
        sent = _picked(changes, self.UPDATE_FIELDS, method, "the change")
        response = self._client.request("PATCH", path, json=sent, retryable=False)
        return _checked_feature(response, f"PATCH {path}")

    def delete(self, app: str, feature_id: int) -> "RemovedAppFeature":
        """Remove a feature (``DELETE /apps/{app}/features/{id}``), sent
        once. Siren archives it while a record picks it and answers
        ``{"archived": True, "feature": {...}}``, and deletes it otherwise
        and answers ``{"archived": False, "feature": None}``. Raises
        :class:`~siren.ApiError` for any other body."""
        method = "apps.features.delete()"
        path = f"{_app_path(app, method)}/features/{_feature_id(feature_id, method)}"
        response = self._client.request("DELETE", path, retryable=False)
        body = parse_json(response)
        fits = (
            _is_object(body)
            and set(body) == {"archived", "feature"}
            and isinstance(body["archived"], bool)
            and (body["feature"] is None or _is_feature(body["feature"]))
        )
        if not fits:
            raise ApiError(
                f"Siren answered DELETE {path} with a body that is not {{archived, feature}}.",
                status_code=response.status_code,
                data=body,
            )
        return cast("RemovedAppFeature", body)


def _is_app(body: Any) -> bool:
    return _is_object(body) and isinstance(body.get("key"), str) and isinstance(body.get("id"), int)


def _checked_app(response: Any, route: str) -> "AppRecord":
    body = parse_json(response)
    if not _is_app(body):
        raise ApiError(f"Siren answered {route} with a body that is not an app.", status_code=response.status_code, data=body)
    return cast("AppRecord", body)


def _with_raw_key(response: Any, route: str, then: str) -> "AppWithRawKey":
    app = _checked_app(response, route)
    raw_key = cast(Dict[str, Any], app).get("rawKey")
    if not isinstance(raw_key, str) or raw_key == "":
        raise ApiError(
            f"Siren answered {route} with an app but no rawKey. Siren made the key by then. {then}",
            status_code=response.status_code,
            data=app,
        )
    return cast("AppWithRawKey", app)


class Apps:
    """``siren.apps`` — check a request Siren signed and sent the app,
    answer an ask through :attr:`requests`, add, read, change and remove one
    app's features through :attr:`features`, and make, rotate and revoke an
    app's key.

    An app owns its key. Siren has no ``/api-keys`` routes, so a key is made
    with :meth:`create_key` or ``siren apps apply``, and :meth:`scopes` lists
    what it may hold.
    """

    def __init__(self, client: HttpClient) -> None:
        self._client = client
        self.requests = AppRequests(client)
        self.features = AppFeatures(client)

    def scopes(self) -> "ApiKeyScopeGroups":
        """The scopes an app's key may hold, grouped by resource
        (``GET /apps/scopes``, ``ApiKeyScopePolicy::issuableGroups()``), each
        a :class:`~siren.ApiKeyScopeEntry`. The same groups the API keys
        screen offers. Retried as a read.

        Takes a signed-in dashboard session, passed as the client's key.
        Siren answers an API key with a 401 that the SDK raises as
        :class:`~siren.ApiKeyNotAcceptedError`. Raises :class:`~siren.ApiError`
        when the body is not an object of scope lists.
        """
        response = self._client.request("GET", "/apps/scopes")
        body = parse_json(response)
        fits = _is_object(body) and all(isinstance(group, list) and all(_is_object(entry) for entry in group) for group in body.values())
        if not fits:
            raise ApiError(
                "Siren answered GET /apps/scopes with a body that is not an object of scope lists.",
                status_code=response.status_code,
                data=body,
            )
        return cast("ApiKeyScopeGroups", body)

    def create_key(self, app: str, *, scopes: Optional[Sequence[str]] = None) -> "AppWithRawKey":
        """Make a key for an app that has none (``POST /apps/{app}/key``),
        sent once, holding ``scopes`` or none. Siren labels it "App: {name}"
        and answers 201 with the app and its ``rawKey``, shown this once.

        Takes a signed-in dashboard session, passed as the client's key, as
        ``siren apps apply`` does. Raises :class:`ValueError` before sending
        for a bad app key or ``scopes`` that is not a list of text. Siren
        answers 409 ``app_key_exists`` when the app holds an active key, and
        400 with ``limit`` when the organization holds its cap of keys.
        Raises :class:`~siren.ApiError` when the answer lacks ``rawKey``:
        Siren made the key by then, so rotate it to get one you can read.
        """
        method = "apps.create_key()"
        path = f"{_app_path(app, method)}/key"
        sent: Dict[str, Any] = {}
        if scopes is not None:
            if isinstance(scopes, (str, bytes)) or not all(isinstance(scope, str) for scope in scopes):
                raise ValueError(f"{method} takes scopes as a list of text, such as ['ledger:payouts:read'].")
            sent["scopes"] = [str(scope) for scope in scopes]
        response = self._client.request("POST", path, json=sent, retryable=False)
        return _with_raw_key(response, f"POST {path}", "Rotate it with apps.rotate_key() to get a new one.")

    def rotate_key(self, app: str) -> "AppWithRawKey":
        """Replace the app's key (``POST /apps/{app}/key/rotate``), sent
        once. Siren revokes the old key first, then makes a new one with the
        old label, scopes and store, and answers the app with ``rawKey``,
        shown this once. The signing secret never changes with the key.

        Takes a signed-in dashboard session, passed as the client's key, as
        ``siren apps rotate-key`` does. Raises :class:`ValueError` before
        sending for a bad app key. Siren answers 409 ``app_key_missing`` for
        an app with no key. Raises :class:`~siren.ApiError` when the answer
        lacks ``rawKey``: Siren rotated the key by then, so rotate again.
        """
        method = "apps.rotate_key()"
        path = f"{_app_path(app, method)}/key/rotate"
        response = self._client.request("POST", path, retryable=False)
        return _with_raw_key(response, f"POST {path}", "Rotate it again to get one you can read.")

    def revoke_key(self, app: str) -> "AppRecord":
        """Revoke the app's key (``DELETE /apps/{app}/key``), sent once, and
        return the app, which keeps its address.

        Takes a signed-in dashboard session, passed as the client's key.
        Raises :class:`ValueError` before sending for a bad app key. Siren
        answers 409 ``app_key_missing`` for an app with no key,
        ``app_needs_a_part`` for an app with no address, and
        ``app_features_need_key_and_address`` for an app with features.
        """
        method = "apps.revoke_key()"
        path = f"{_app_path(app, method)}/key"
        response = self._client.request("DELETE", path, retryable=False)
        return _checked_app(response, f"DELETE {path}")

    def request_handler(
        self,
        signing_secret: Optional[str] = None,
        *,
        tolerance_seconds: Optional[int] = None,
        run_later: Optional[Callable[[Callable[[], None]], Any]] = None,
        on_error: Optional[Callable[..., Any]] = None,
        extra_kinds: Optional[Iterable[str]] = None,
        logger: Optional[Any] = None,
    ) -> "AppRequestHandler":
        """One address for everything Siren sends the app: it checks each
        request as :meth:`verify_request` does, runs the function registered
        for its kind and key with :meth:`AppRequestHandler.on`, replies, and
        posts an ask's answer with this client's key. See
        :class:`~siren.AppRequestHandler` for the replies and the faces to
        mount (``flask``, ``django``, ``asgi``, ``wsgi``).

        ``signing_secret`` is the app's, which Siren showed once when it made
        the app. It is read from ``SIREN_SIGNING_SECRET`` when not passed.

        ``run_later`` is handed an ask's work, a callable with no arguments,
        in place of running it after the reply, for a host that stops work
        once it has replied. ``on_error(error, request)`` gets every failure
        the reply can't show, a refused signature and a tell slower than
        8 seconds among them. By default it writes one line through
        ``logger``, which says when Siren sends a failed request again.

        ``logger`` is a :class:`logging.Logger`, or anything with ``debug``,
        ``info``, ``warning`` and ``error``, such as a ``LoggerAdapter``. It
        is the ``siren`` logger by default. The handler writes one ``debug``
        line there per request it handles.

        ``extra_kinds`` lists kinds a newer Siren sends that this SDK doesn't
        know yet, for :meth:`AppRequestHandler.on` to take.

        Raises :class:`~siren.AppHandlerError` for no secret, a tolerance
        outside 1 to :data:`MAX_TOLERANCE_SECONDS`, a ``run_later`` or
        ``on_error`` that is not callable, a ``logger`` that is not one, and
        an ``extra_kinds`` that is not a list of kind names.
        """
        from .app_requests import AppRequestHandler

        if signing_secret is None:
            signing_secret = os.environ.get("SIREN_SIGNING_SECRET") or None
        if signing_secret is None:
            raise AppHandlerError(
                "apps.request_handler() needs the app's signing secret, which Siren showed once when it made the app. "
                "Pass signing_secret, or set SIREN_SIGNING_SECRET."
            )
        return AppRequestHandler(
            self.requests,
            signing_secret,
            tolerance_seconds=tolerance_seconds,
            run_later=run_later,
            on_error=on_error,
            extra_kinds=extra_kinds,
            logger=logger,
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
    ) -> "AppRequest[Dict[str, Any]]":
        """Check a request Siren signed and sent the app, and return it parsed:
        an :class:`~siren.AppAsk` or an :class:`~siren.AppTell`.
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
        7. The body must be an app request of version 1. The error names the
           first field that fails and its rule.

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
        problem = _envelope_problem(body)
        if problem is not None:
            raise _BodyRefused(problem)

        made = AppAsk if body["mode"] == "ask" else AppTell
        return made(
            version=1,
            id=body["id"],
            type=body["type"],
            mode=body["mode"],
            test=body.get("test") is True,
            app=body["app"],
            feature={"kind": body["feature"]["kind"], "key": body["feature"]["key"], "id": body["feature"]["id"]},
            created_at=body["createdAt"],
            answer_by=body.get("answerBy") if isinstance(body.get("answerBy"), str) else None,
            answer_url=body.get("answerUrl") if isinstance(body.get("answerUrl"), str) else None,
            data=_read_data(body["feature"]["kind"], body["data"]),
            timestamp=signed_at,
            delivery_id=_header(headers, "X-Siren-Delivery"),
            raw=body,
        )
