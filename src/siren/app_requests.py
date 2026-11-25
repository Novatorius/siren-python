"""One address on the app's server for everything Siren sends it.

:class:`AppRequestHandler` checks each request the way
``apps.verify_request()`` does, runs the function the app registered for the
feature's kind and key, replies, and posts an ask's answer. It is plain
Python. Call it with the raw body and the headers for an
:class:`AppRequestReply`, or mount one of its faces: ``flask``, ``django``,
``asgi`` or ``wsgi``. A framework is imported only when its face is used.

The replies, in the order the checks run:

- 405 for a method other than POST.
- 413 for a body over :data:`MAX_BODY_BYTES`, 1 MiB, more than Siren sends.
  The faces answer it from the length and stop reading.
- 500 when a body parser already turned the body into an object, so the
  bytes Siren signed are gone. ``on_error`` gets a :class:`~siren.SirenError`
  that says so, and Siren sends the request again.
- 401 when ``X-Siren-Signature-V2`` is missing or wrong, ``X-Siren-Timestamp``
  differs from the signed time, or the time is more than ``tolerance_seconds``
  from the clock.
- 400 when the signed body is not JSON, or an ``app.request`` body is not a
  version 1 request. The words name the first field that fails.
- 500 for an ask whose signed ``answerUrl`` is on another Siren than the
  client's ``base_url``. ``on_error`` gets a :class:`~siren.SirenError` that
  names both addresses, and Siren sends the request again. The answer only
  ever goes to the client's own address, never to one a request names.
- A tell: 200 once its function returns, or 500 when it raises, so Siren
  sends it again.
- An ask: 200 at once. The function runs after the reply, and what it
  returns is posted as the answer. It returns ``answers.later()`` when the
  app will answer with ``apps.requests.answer()`` itself. An ask's function
  that returns ``None`` is an error, which goes to ``on_error``.
- 404 for an ``app.request`` whose kind and key have no function, so Siren
  shows the request under Needs attention once its sends run out.
- Any other ``X-Siren-Event`` goes to the ``on_event`` function for its type:
  200 once it returns, 500 when it raises, and 200 when there is none.

Every reply is JSON with ``Cache-Control: no-store``. Its words never hold
the secret or the body.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from http import HTTPStatus
from urllib.parse import urlsplit
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    Mapping,
    MutableMapping,
    Optional,
    Tuple,
    TypeVar,
    Union,
    overload,
)

from .apps import REQUEST_EVENT, Apps, _BodyRefused, _check_signature, _check_tolerance, _checked_answer, _header
from ._answer_checks import siren_refusal
from .errors import AppHandlerError, AppSlowTellError, ConflictError, ErrorCode, SignatureVerificationError, SirenError, ValidationError
from .types import (
    APP_KINDS,
    APP_REQUEST_KINDS,
    AppAsk,
    AppRequest,
    AppTell,
    CollaboratorGroupStructureAnswer,
    CollaboratorGroupStructureQuestion,
    DistributionResolverAnswer,
    DistributionResolverQuestion,
    EngagementCalculationAnswer,
    EngagementCalculationQuestion,
    EngagementFilterAnswer,
    EngagementFilterQuestion,
    FeatureKind,
    IncentiveResolverAnswer,
    IncentiveResolverQuestion,
    IncentiveTypeAnswer,
    IncentiveTypeQuestion,
    MetricCalculationAnswer,
    MetricCalculationQuestion,
    MetricFilterAnswer,
    MetricFilterQuestion,
    PayoutMethodAnswer,
    PayoutMethodQuestion,
    PostAllocationActionTell,
    PostConversionActionTell,
    ProgramGroupSorterAnswer,
    ProgramGroupSorterQuestion,
    TransactionFilterAnswer,
    TransactionFilterQuestion,
    WebhookEvent,
)
from .webhooks import _is_envelope as _is_webhook_envelope

if TYPE_CHECKING:  # pragma: no cover
    from .apps import AppRequests

__all__ = [
    "AppAnswerLater",
    "AppAskResult",
    "AppRequestHandler",
    "AppRequestReply",
    "AppSimulation",
    "MAX_BODY_BYTES",
    "TELL_TIME_LIMIT_SECONDS",
]

#: The longest body the handler reads, 1 MiB, as in the Node SDK. Siren's
#: requests are far smaller, since an answer alone stays under 16 KB.
MAX_BODY_BYTES = 1024 * 1024

#: How long Siren waits for a tell's or a webhook event's reply, in seconds.
#: Siren's WebhookDeliveryService::deliver() sends both through
#: GuardedCurlHttpClient with an 8-second timeout, and counts a slower reply
#: as a failed send, so it sends it again. A tell or ``on_event`` function
#: that runs longer reaches ``on_error`` as :class:`~siren.AppSlowTellError`.
TELL_TIME_LIMIT_SECONDS = 8

#: The handler's default logger. ``request_handler(logger=...)`` replaces it.
logger = logging.getLogger("siren")

#: Refused signatures the default ``on_error`` writes a line for in one
#: minute. It counts the rest and writes the count once the minute is over.
SIGNATURE_LINES_PER_MINUTE = 5

#: What each refused signature's line adds, by the error's code.
_SIGNATURE_HINTS: Dict[str, str] = {
    "signature_mismatch": "Check SIREN_SIGNING_SECRET: it must be this app's signing secret, and it changes when the secret is rotated.",
    "signature_missing": "Check that a proxy passes Siren's X-Siren-* headers through.",
    "signature_malformed": "Check that a proxy passes Siren's X-Siren-* headers through.",
    "timestamp_mismatch": "Check that a proxy passes Siren's X-Siren-* headers through.",
}

_LOGGER_LEVELS = ("debug", "info", "warning", "error")

#: The words a 500 carries when a body parser already ate the body.
BODY_ALREADY_PARSED = (
    "The Siren request handler got a body that a parser already read, so it cannot check the signature. "
    "Mount the handler before any JSON body parser, or give its route the raw body."
)

#: What a face hands the core in place of a body over the limit.
_TOO_LARGE = object()

_JSON_HEADERS: Tuple[Tuple[str, str], ...] = (("Content-Type", "application/json"), ("Cache-Control", "no-store"))

Subject = Union[AppRequest, WebhookEvent]
Work = Callable[[], None]
F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class AppRequestReply:
    """What the handler replies: ``status``, the JSON ``body`` and the
    ``headers``. ``after`` is an ask's work, set only when the reply is 200
    to an ask and no ``run_later`` was given. Send the reply, then call
    ``after()``, or hand it to a queue."""

    status: int
    body: Dict[str, Any]
    after: Optional[Work] = None
    headers: Tuple[Tuple[str, str], ...] = _JSON_HEADERS

    @property
    def content(self) -> bytes:
        """The body as JSON bytes."""
        return json.dumps(self.body).encode("utf-8")

    @property
    def status_line(self) -> str:
        """The status as WSGI writes it, such as ``"200 OK"``."""
        return f"{self.status} {HTTPStatus(self.status).phrase}"


def _reply(status: int, error: Optional[str] = None, *, after: Optional[Work] = None, headers: Tuple[Tuple[str, str], ...] = _JSON_HEADERS) -> AppRequestReply:
    return AppRequestReply(status, {} if error is None else {"error": error}, after, headers)


@dataclass(frozen=True)
class AppSimulation:
    """What :meth:`AppRequestHandler.simulate` saw: the ``status`` and JSON
    ``body`` the handler would reply, for an ask the ``answer`` it would
    post, and the ``error`` that would reach ``on_error``.

    ``answer`` is ``None`` when nothing would be posted. ``error`` is
    ``None`` when nothing failed. It holds what the function raised, the
    error for an ask's function that returned ``None``, the
    :class:`~siren.AppHandlerError` for an answer the handler would not post, or a
    :class:`~siren.ValidationError` with code ``app_answer_invalid`` and
    Siren's own words for an answer Siren would refuse."""

    status: int
    body: Dict[str, Any]
    answer: Optional[Dict[str, Any]] = None
    error: Optional[BaseException] = None


class AppAnswerLater:
    """What :meth:`AppRequestHandler.later` returns, the one value of this
    type: an ask's function returns it when the app will answer by itself
    with ``apps.requests.answer()``. Annotate such a function's return as
    ``Union[DistributionResolverAnswer, AppAnswerLater]``."""

    _instance: Optional["AppAnswerLater"] = None

    def __new__(cls) -> "AppAnswerLater":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "answers.later()"


_LATER = AppAnswerLater()

_A = TypeVar("_A")

#: What an ask's function returns: the answer, or :meth:`AppRequestHandler.later`,
#: now or from an ``async def``. ``AppAskResult[DistributionResolverAnswer]``.
AppAskResult = Union[_A, AppAnswerLater, Awaitable[Union[_A, AppAnswerLater]]]

# Generated function types, from app_kinds.json by scripts/feature_kinds.py. Do not edit.
_PostConversionActionFn = TypeVar("_PostConversionActionFn", bound="Callable[[AppTell[PostConversionActionTell]], object]")
_PostAllocationActionFn = TypeVar("_PostAllocationActionFn", bound="Callable[[AppTell[PostAllocationActionTell]], object]")
_PayoutMethodFn = TypeVar("_PayoutMethodFn", bound="Callable[[AppAsk[PayoutMethodQuestion]], AppAskResult[PayoutMethodAnswer]]")
_DistributionResolverFn = TypeVar("_DistributionResolverFn", bound="Callable[[AppAsk[DistributionResolverQuestion]], AppAskResult[DistributionResolverAnswer]]")
_MetricCalculationFn = TypeVar("_MetricCalculationFn", bound="Callable[[AppAsk[MetricCalculationQuestion]], AppAskResult[MetricCalculationAnswer]]")
_MetricFilterFn = TypeVar("_MetricFilterFn", bound="Callable[[AppAsk[MetricFilterQuestion]], AppAskResult[MetricFilterAnswer]]")
_IncentiveResolverFn = TypeVar("_IncentiveResolverFn", bound="Callable[[AppAsk[IncentiveResolverQuestion]], AppAskResult[IncentiveResolverAnswer]]")
_TransactionFilterFn = TypeVar("_TransactionFilterFn", bound="Callable[[AppAsk[TransactionFilterQuestion]], AppAskResult[TransactionFilterAnswer]]")
_IncentiveTypeFn = TypeVar("_IncentiveTypeFn", bound="Callable[[AppAsk[IncentiveTypeQuestion]], AppAskResult[IncentiveTypeAnswer]]")
_EngagementCalculationFn = TypeVar("_EngagementCalculationFn", bound="Callable[[AppAsk[EngagementCalculationQuestion]], AppAskResult[EngagementCalculationAnswer]]")
_EngagementFilterFn = TypeVar("_EngagementFilterFn", bound="Callable[[AppAsk[EngagementFilterQuestion]], AppAskResult[EngagementFilterAnswer]]")
_ProgramGroupSorterFn = TypeVar("_ProgramGroupSorterFn", bound="Callable[[AppAsk[ProgramGroupSorterQuestion]], AppAskResult[ProgramGroupSorterAnswer]]")
_CollaboratorGroupStructureFn = TypeVar("_CollaboratorGroupStructureFn", bound="Callable[[AppAsk[CollaboratorGroupStructureQuestion]], AppAskResult[CollaboratorGroupStructureAnswer]]")
# End of the generated function types.


# The kinds on() takes ---------------------------------------------------------

#: Each kind Siren sends with its name on Siren Cloud's screens, read from
#: the kind snapshot the SDK ships (:data:`~siren.APP_KINDS`), for the words
#: that refuse a name that is not a kind.
_SCREEN_NAMES: Tuple[Tuple[str, str], ...] = tuple(
    (entry["kind"], entry["name"]) for entry in APP_KINDS if entry["kind"] in APP_REQUEST_KINDS
)


def _squash(name: str) -> str:
    return "".join(c for c in name.lower() if c not in " _-")


def _edit_distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (x != y))
    return row[-1]


def _kind_problem(name: str, extra: Tuple[str, ...]) -> Optional[str]:
    """Why ``on()`` refuses a kind, in words that say what to write, or
    ``None`` for a kind Siren sends or one in ``extra_kinds``."""
    if name in APP_REQUEST_KINDS or name in extra:
        return None
    squashed = _squash(name)
    if squashed == "customevent":
        return "Siren never sends a custom event. Your app sends one with events.ingest()."
    if "." in name:
        return f"{name} is a webhook event. Register it with on_event()."
    if squashed == "fulfillmentmethod":
        return f"{name} is the old name. Use payoutMethod."
    by_letters = {kind.lower(): kind for kind in APP_REQUEST_KINDS}
    if "_" in name and squashed in by_letters:
        return f"Kinds are camelCase, as Siren sends them: {by_letters[squashed]}."
    for kind, screen in _SCREEN_NAMES:
        if _squash(screen) == squashed and squashed != kind.lower():
            return f"The kind for {screen} is {kind}."
    near = by_letters.get(squashed)
    if near is None:
        distance, closest = min((_edit_distance(name, kind), kind) for kind in APP_REQUEST_KINDS)
        near = closest if distance <= 2 else None
    if near is not None:
        return f"{name} is not a feature kind. Did you mean {near}?"
    return f"{name} is not a feature kind. The kinds are: {', '.join(APP_REQUEST_KINDS)}."


def _extra_kinds(method: str, extra_kinds: Any) -> Tuple[str, ...]:
    if extra_kinds is None:
        return ()
    words = f"{method} takes extra_kinds as a list of kind names a newer Siren sends, such as ['refundPolicy']."
    if isinstance(extra_kinds, (str, bytes)):
        raise AppHandlerError(words)
    try:
        kinds = tuple(extra_kinds)
    except TypeError:
        raise AppHandlerError(words) from None
    for kind in kinds:
        if not isinstance(kind, str) or kind == "" or "." in kind:
            raise AppHandlerError(words)
    return kinds


# What the default on_error says happens next ---------------------------------


def _window_close(answer_by: Optional[str]) -> Optional[str]:
    """When an ask's answer window closes, as ``16:58 UTC``, with the date
    when it is not today, or ``None`` when ``answerBy`` can't be read."""
    if not isinstance(answer_by, str):
        return None
    text = answer_by.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        closes = datetime.fromisoformat(text)
    except ValueError:
        return None
    if closes.tzinfo is None:
        closes = closes.replace(tzinfo=timezone.utc)
    closes = closes.astimezone(timezone.utc)
    if closes.date() == datetime.now(timezone.utc).date():
        return closes.strftime("%H:%M UTC")
    return closes.strftime("%Y-%m-%d %H:%M UTC")


def _next_send(error: BaseException, request: AppRequest, mode: str) -> str:
    """What Siren does next with a request whose function failed."""
    if mode == "tell":
        return "Siren sends it again in about a minute."
    if isinstance(error, ConflictError):
        return ""  # Answered or dropped: Siren sends it no more.
    if isinstance(error, ValidationError) and error.code == ErrorCode.APP_ANSWER_INVALID:
        # Siren sends it again, and the same answer would break the rule again.
        return (
            "Your function's answer broke Siren's rule. Reproduce it with simulate() on this request's data, "
            f"fix it, then resend with `siren apps resend {request.id}`."
        )
    closes = _window_close(request.answer_by)
    when = f"after {closes}, when" if closes is not None else "when"
    return (
        f"Siren sends it again {when} its answer window closes. "
        f"Resend it sooner with `siren apps resend {request.id}`."
    )


#: Siren Cloud's two public hosts, which reach the same service, so an
#: answer address on either one is the same Siren. Siren's own repository
#: shows they are one deployment: .github/workflows/deploy-cloud.yml checks
#: health at https://app.sirenaffiliates.com/health,
#: .github/workflows/publish-product-screenshots.yml sets
#: APP_URL=https://app.sirenaffiliates.com, and docs/origin-lock-runbook.md
#: has Cloudflare proxy both hosts to the same origin, where nginx answers
#: with ``server_name _``. Only this pair, over https on 443, counts as one.
#: Any other difference in scheme, host or port is another Siren.
_CLOUD_HOSTS = frozenset({"api.sirenaffiliates.com", "app.sirenaffiliates.com"})


def _origin(url: str) -> Optional[Tuple[str, str, int]]:
    """A URL's scheme, host and port, with Siren Cloud's two hosts as one,
    or ``None`` for an address that can't be read."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or host == "":
        return None
    port = port if port is not None else (443 if scheme == "https" else 80)
    if scheme == "https" and port == 443 and host in _CLOUD_HOSTS:
        host = "siren-cloud"
    return scheme, host, port


@dataclass
class _Run:
    """A checked request that has a function to run."""

    mode: str  # "tell", "ask" or "event"
    fn: Callable[[Any], Any]
    subject: Subject
    label: str
    failed: str = field(default="")


class AppRequestHandler:
    """The app's request handler. Make it with
    ``client.apps.request_handler(signing_secret)``, where the client holds
    the app's own key, register a function per feature with :meth:`on`, and
    mount one face::

        answers = client.apps.request_handler(os.environ["SIREN_SIGNING_SECRET"])

        @answers.on("distributionResolver", "split-by-score")
        def split(request):
            ...

        app.add_url_rule("/siren", view_func=answers.flask, methods=["POST"])  # Flask
        path("siren", answers.django)                                           # Django
        app.add_route("/siren", answers.asgi, methods=["POST"])                 # FastAPI, Starlette
        answers.wsgi                                                            # any WSGI server
        reply = answers(raw_body, headers, method="POST")                       # anything else

    A function takes the :class:`~siren.AppRequest`. A tell's return value is
    ignored. An ask's is posted as the answer, or the function returns
    :meth:`later` when the app will answer with ``apps.requests.answer()``
    itself. An ``async def`` function is awaited, and under ASGI a plain one
    runs on a worker thread.

    The handler keeps no state. Siren may send one request more than once,
    and sends an unanswered ask again when its answer window closes, so
    every function writes once by ``request.id``, the same on every send,
    and an ask's function returns the same answer each time.
    """

    def __init__(
        self,
        requests: "AppRequests",
        signing_secret: str,
        *,
        tolerance_seconds: Optional[int] = None,
        run_later: Optional[Callable[[Work], Any]] = None,
        on_error: Optional[Callable[..., Any]] = None,
        extra_kinds: Optional[Iterable[str]] = None,
        logger: Optional[Any] = None,
    ) -> None:
        method = "apps.request_handler()"
        if not isinstance(signing_secret, str) or signing_secret == "":
            raise AppHandlerError(f"{method} needs the app's signing secret, which Siren showed when it made the app.")
        if run_later is not None and not callable(run_later):
            raise AppHandlerError(f"{method} takes run_later as a function that is handed the work, such as a queue's add.")
        if on_error is not None and not callable(on_error):
            raise AppHandlerError(f"{method} takes on_error as a function of (error, request).")
        if logger is not None and (isinstance(logger, str) or not all(callable(getattr(logger, level, None)) for level in _LOGGER_LEVELS)):
            raise AppHandlerError(
                f"{method} takes logger as a logging.Logger, or anything with debug, info, warning and error, "
                "such as a LoggerAdapter."
            )
        self._logger: Any = logging.getLogger("siren") if logger is None else logger
        # The refused signatures of the current minute, for the default's flood control.
        self._refusals_lock = threading.Lock()
        self._refusals_since: Optional[float] = None
        self._refusals_written = 0
        self._refusals_counted = 0
        self._requests = requests
        self._secret = signing_secret
        try:
            self._tolerance = _check_tolerance(method, tolerance_seconds)
        except ValueError as error:
            raise AppHandlerError(str(error)) from None
        self._run_later = run_later
        self._on_error = on_error
        self._extra_kinds = _extra_kinds(method, extra_kinds)
        self._functions: Dict[Tuple[str, Optional[str]], Callable[[Any], Any]] = {}
        self._events: Dict[str, Callable[[Any], Any]] = {}
        self.wsgi = _Wsgi(self)
        self.asgi = _Asgi(self)
        self.flask = _flask_view(self)
        self.django = _django_view(self)

    # Registration ----------------------------------------------------------

    # Generated from app_kinds.json by scripts/feature_kinds.py. Do not edit.
    @overload
    def on(self, kind: Literal["postConversionAction", FeatureKind.POST_CONVERSION_ACTION], key: str, fn: Callable[[AppTell[PostConversionActionTell]], object]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["postConversionAction", FeatureKind.POST_CONVERSION_ACTION], key: Callable[[AppTell[PostConversionActionTell]], object]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["postConversionAction", FeatureKind.POST_CONVERSION_ACTION], key: Optional[str] = None) -> Callable[[_PostConversionActionFn], _PostConversionActionFn]: ...

    @overload
    def on(self, kind: Literal["postAllocationAction", FeatureKind.POST_ALLOCATION_ACTION], key: str, fn: Callable[[AppTell[PostAllocationActionTell]], object]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["postAllocationAction", FeatureKind.POST_ALLOCATION_ACTION], key: Callable[[AppTell[PostAllocationActionTell]], object]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["postAllocationAction", FeatureKind.POST_ALLOCATION_ACTION], key: Optional[str] = None) -> Callable[[_PostAllocationActionFn], _PostAllocationActionFn]: ...

    @overload
    def on(self, kind: Literal["payoutMethod", FeatureKind.PAYOUT_METHOD], key: str, fn: Callable[[AppAsk[PayoutMethodQuestion]], AppAskResult[PayoutMethodAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["payoutMethod", FeatureKind.PAYOUT_METHOD], key: Callable[[AppAsk[PayoutMethodQuestion]], AppAskResult[PayoutMethodAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["payoutMethod", FeatureKind.PAYOUT_METHOD], key: Optional[str] = None) -> Callable[[_PayoutMethodFn], _PayoutMethodFn]: ...

    @overload
    def on(self, kind: Literal["distributionResolver", FeatureKind.DISTRIBUTION_RESOLVER], key: str, fn: Callable[[AppAsk[DistributionResolverQuestion]], AppAskResult[DistributionResolverAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["distributionResolver", FeatureKind.DISTRIBUTION_RESOLVER], key: Callable[[AppAsk[DistributionResolverQuestion]], AppAskResult[DistributionResolverAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["distributionResolver", FeatureKind.DISTRIBUTION_RESOLVER], key: Optional[str] = None) -> Callable[[_DistributionResolverFn], _DistributionResolverFn]: ...

    @overload
    def on(self, kind: Literal["metricCalculation", FeatureKind.METRIC_CALCULATION], key: str, fn: Callable[[AppAsk[MetricCalculationQuestion]], AppAskResult[MetricCalculationAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["metricCalculation", FeatureKind.METRIC_CALCULATION], key: Callable[[AppAsk[MetricCalculationQuestion]], AppAskResult[MetricCalculationAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["metricCalculation", FeatureKind.METRIC_CALCULATION], key: Optional[str] = None) -> Callable[[_MetricCalculationFn], _MetricCalculationFn]: ...

    @overload
    def on(self, kind: Literal["metricFilter", FeatureKind.METRIC_FILTER], key: str, fn: Callable[[AppAsk[MetricFilterQuestion]], AppAskResult[MetricFilterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["metricFilter", FeatureKind.METRIC_FILTER], key: Callable[[AppAsk[MetricFilterQuestion]], AppAskResult[MetricFilterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["metricFilter", FeatureKind.METRIC_FILTER], key: Optional[str] = None) -> Callable[[_MetricFilterFn], _MetricFilterFn]: ...

    @overload
    def on(self, kind: Literal["incentiveResolver", FeatureKind.INCENTIVE_RESOLVER], key: str, fn: Callable[[AppAsk[IncentiveResolverQuestion]], AppAskResult[IncentiveResolverAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["incentiveResolver", FeatureKind.INCENTIVE_RESOLVER], key: Callable[[AppAsk[IncentiveResolverQuestion]], AppAskResult[IncentiveResolverAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["incentiveResolver", FeatureKind.INCENTIVE_RESOLVER], key: Optional[str] = None) -> Callable[[_IncentiveResolverFn], _IncentiveResolverFn]: ...

    @overload
    def on(self, kind: Literal["transactionFilter", FeatureKind.TRANSACTION_FILTER], key: str, fn: Callable[[AppAsk[TransactionFilterQuestion]], AppAskResult[TransactionFilterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["transactionFilter", FeatureKind.TRANSACTION_FILTER], key: Callable[[AppAsk[TransactionFilterQuestion]], AppAskResult[TransactionFilterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["transactionFilter", FeatureKind.TRANSACTION_FILTER], key: Optional[str] = None) -> Callable[[_TransactionFilterFn], _TransactionFilterFn]: ...

    @overload
    def on(self, kind: Literal["incentiveType", FeatureKind.INCENTIVE_TYPE], key: str, fn: Callable[[AppAsk[IncentiveTypeQuestion]], AppAskResult[IncentiveTypeAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["incentiveType", FeatureKind.INCENTIVE_TYPE], key: Callable[[AppAsk[IncentiveTypeQuestion]], AppAskResult[IncentiveTypeAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["incentiveType", FeatureKind.INCENTIVE_TYPE], key: Optional[str] = None) -> Callable[[_IncentiveTypeFn], _IncentiveTypeFn]: ...

    @overload
    def on(self, kind: Literal["engagementCalculation", FeatureKind.ENGAGEMENT_CALCULATION], key: str, fn: Callable[[AppAsk[EngagementCalculationQuestion]], AppAskResult[EngagementCalculationAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["engagementCalculation", FeatureKind.ENGAGEMENT_CALCULATION], key: Callable[[AppAsk[EngagementCalculationQuestion]], AppAskResult[EngagementCalculationAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["engagementCalculation", FeatureKind.ENGAGEMENT_CALCULATION], key: Optional[str] = None) -> Callable[[_EngagementCalculationFn], _EngagementCalculationFn]: ...

    @overload
    def on(self, kind: Literal["engagementFilter", FeatureKind.ENGAGEMENT_FILTER], key: str, fn: Callable[[AppAsk[EngagementFilterQuestion]], AppAskResult[EngagementFilterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["engagementFilter", FeatureKind.ENGAGEMENT_FILTER], key: Callable[[AppAsk[EngagementFilterQuestion]], AppAskResult[EngagementFilterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["engagementFilter", FeatureKind.ENGAGEMENT_FILTER], key: Optional[str] = None) -> Callable[[_EngagementFilterFn], _EngagementFilterFn]: ...

    @overload
    def on(self, kind: Literal["programGroupSorter", FeatureKind.PROGRAM_GROUP_SORTER], key: str, fn: Callable[[AppAsk[ProgramGroupSorterQuestion]], AppAskResult[ProgramGroupSorterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["programGroupSorter", FeatureKind.PROGRAM_GROUP_SORTER], key: Callable[[AppAsk[ProgramGroupSorterQuestion]], AppAskResult[ProgramGroupSorterAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["programGroupSorter", FeatureKind.PROGRAM_GROUP_SORTER], key: Optional[str] = None) -> Callable[[_ProgramGroupSorterFn], _ProgramGroupSorterFn]: ...

    @overload
    def on(self, kind: Literal["collaboratorGroupStructure", FeatureKind.COLLABORATOR_GROUP_STRUCTURE], key: str, fn: Callable[[AppAsk[CollaboratorGroupStructureQuestion]], AppAskResult[CollaboratorGroupStructureAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["collaboratorGroupStructure", FeatureKind.COLLABORATOR_GROUP_STRUCTURE], key: Callable[[AppAsk[CollaboratorGroupStructureQuestion]], AppAskResult[CollaboratorGroupStructureAnswer]]) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: Literal["collaboratorGroupStructure", FeatureKind.COLLABORATOR_GROUP_STRUCTURE], key: Optional[str] = None) -> Callable[[_CollaboratorGroupStructureFn], _CollaboratorGroupStructureFn]: ...

    # End of the generated overloads.
    @overload
    def on(self, kind: str, key: str, fn: F) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: str, key: F) -> "AppRequestHandler": ...

    @overload
    def on(self, kind: str, key: Optional[str] = None) -> Callable[[F], F]: ...

    def on(self, kind: str, key: Any = None, fn: Any = None) -> Any:
        """Register the function for a feature: ``on(kind, key, fn)`` for
        one feature, ``on(kind, fn)`` for every feature of the kind. Without
        a function it is a decorator, ``@answers.on(kind, key)`` or
        ``@answers.on(kind)``, that hands the function back. The direct forms
        return the handler, so calls chain. A function for the key wins over
        one for the kind.

        ``kind`` is one of :data:`~siren.APP_REQUEST_KINDS`, as text or as
        a :class:`~siren.FeatureKind`, or a kind in the handler's
        ``extra_kinds``. Any other name is refused at once with words that
        say what to write, such as "distributionResolvr is not a feature
        kind. Did you mean distributionResolver?"

        For each kind Siren sends, the overloads type the function by the
        kind: an ask's takes ``AppAsk[<Kind>Question]`` and returns
        ``<Kind>Answer`` or :meth:`later`, and a tell's takes
        ``AppTell[<Kind>Tell]``. A kind from ``extra_kinds`` takes any
        function of the request.

        Raises :class:`~siren.AppHandlerError` for a kind Siren never sends,
        an empty key, a function that is not callable, and a second function
        for the same kind and key.
        """
        if fn is None and callable(key):
            key, fn = None, key
        if isinstance(kind, Enum):
            kind = kind.value
        if not isinstance(kind, str) or kind == "":
            raise AppHandlerError("answers.on() needs the feature's kind, such as 'distributionResolver'.")
        problem = _kind_problem(kind, self._extra_kinds)
        if problem is not None:
            raise AppHandlerError(problem)
        if key is not None and (not isinstance(key, str) or key == ""):
            raise AppHandlerError("answers.on() takes the feature's key as text, such as 'split-by-score', or no key for every feature of the kind.")

        def register(function: F) -> F:
            if not callable(function):
                raise AppHandlerError("answers.on() needs a function that takes the request.")
            if (kind, key) in self._functions:
                named = f"{kind} {key}" if key is not None else f"every {kind}"
                raise AppHandlerError(f"answers.on() already has a function for {named}.")
            self._functions[(kind, key)] = function
            return function

        if fn is None:
            return register
        register(fn)
        return self

    @overload
    def on_event(self, type: str, fn: F) -> "AppRequestHandler": ...

    @overload
    def on_event(self, type: str) -> Callable[[F], F]: ...

    def on_event(self, type: str, fn: Any = None) -> Any:  # noqa: A002 - mirrors onEvent(type, fn)
        """Register the function for a webhook event that reaches the same
        address, such as ``conversion.approved``. It takes a
        :class:`~siren.WebhookEvent`. Without a function it is a decorator.
        The events are signed and timed like an app request."""
        if not isinstance(type, str) or type == "" or type == REQUEST_EVENT:
            raise AppHandlerError("answers.on_event() needs a webhook event type, such as 'conversion.approved'. App requests go to answers.on().")

        def register(function: F) -> F:
            if not callable(function):
                raise AppHandlerError("answers.on_event() needs a function that takes the event.")
            if type in self._events:
                raise AppHandlerError(f"answers.on_event() already has a function for {type}.")
            self._events[type] = function
            return function

        if fn is None:
            return register
        register(fn)
        return self

    def later(self) -> AppAnswerLater:
        """What an ask's function returns when the app will answer by itself
        with ``apps.requests.answer()``, such as after a queue or a person
        decides. The handler posts nothing for it. Returning ``None`` from an
        ask's function is an error instead, so a forgotten ``return`` shows
        in ``on_error``."""
        return _LATER

    # The plain call --------------------------------------------------------

    def __call__(self, raw_body: Any, headers: Any, method: str = "POST") -> AppRequestReply:
        """Answer one request. ``raw_body`` is the body exactly as received,
        as bytes or text. ``headers`` is a mapping or a list of pairs.

        For an ask, the reply's ``after`` holds the work: send the reply,
        then call it. With ``run_later``, the work goes to it here and
        ``after`` is ``None``.
        """
        reply = self._handle(method, raw_body, headers)
        if reply.after is not None and self._run_later is not None:
            self._hand_off(reply.after, None)
            return AppRequestReply(reply.status, reply.body, None, reply.headers)
        return reply

    def simulate(self, request: Any) -> AppSimulation:
        """Run a request through the handler, for a test, without posting an
        answer. ``request`` is what :func:`siren.testing.app_request` builds,
        or anything with ``method``, ``headers`` and ``raw_body``.

        Every check runs as for a real request, and the result holds the
        reply's ``status`` and ``body``. A refused request, including an ask
        from another Siren, shows as its status. For an ask, ``answer``
        holds what the function returned, checked as the post checks it and
        then as Siren's answer route checks it, rule for rule.

        A failure comes back in ``error``, never raised: what the function
        raised, an ask's function that returned ``None``, an answer the
        handler would not post, and an answer Siren would refuse, as a
        :class:`~siren.ValidationError` with code ``app_answer_invalid`` and
        Siren's words. A tell's failure has the 500 its reply would have. An
        ``async def`` function is run to the end.

        Raises :class:`~siren.AppHandlerError` when ``request`` is not a
        request.
        """
        if not hasattr(request, "raw_body") or not hasattr(request, "headers"):
            raise AppHandlerError(
                "answers.simulate() takes a request from siren.testing.app_request(), "
                "or anything with method, headers and raw_body."
            )
        checked = self._check(getattr(request, "method", "POST"), request.raw_body, request.headers, report=False)
        if isinstance(checked, AppRequestReply):
            return AppSimulation(checked.status, checked.body)
        started = _clock()
        try:
            returned = _call(checked.fn, checked.subject)
        except Exception as error:
            if checked.mode == "ask":
                return AppSimulation(200, {}, None, error)
            return AppSimulation(500, {"error": checked.failed}, None, error)
        if checked.mode != "ask":
            return AppSimulation(200, {}, None, _slow(checked, _clock() - started))
        if returned is _LATER:
            return AppSimulation(200, {})
        asked = checked.subject
        assert isinstance(asked, AppRequest)
        if returned is None:
            return AppSimulation(200, {}, None, self._returned_nothing(checked))
        try:
            answer = _checked_answer(f"The function for {checked.label}", asked.id, returned)
        except ValueError as error:
            return AppSimulation(200, {}, None, AppHandlerError(str(error)))
        refusal = siren_refusal(asked.feature["kind"], asked.data, answer)
        if refusal is not None:
            invalid = ValidationError(
                refusal,
                code=ErrorCode.APP_ANSWER_INVALID,
                status_code=422,
                data={"error": {"code": ErrorCode.APP_ANSWER_INVALID, "message": refusal}},
            )
            return AppSimulation(200, {}, None, invalid)
        return AppSimulation(200, {}, answer)

    @staticmethod
    def _returned_nothing(run: "_Run") -> AppHandlerError:
        return AppHandlerError(
            f"The function for {run.label} returned nothing. Return the answer, "
            "or return answers.later() when you will answer with apps.requests.answer()."
        )

    # The core --------------------------------------------------------------

    def _handle(self, method: Any, raw_body: Any, headers: Any) -> AppRequestReply:
        started = _clock()
        seen: List[Subject] = []
        reply = self._serve(method, raw_body, headers, seen)
        self._debug(seen, reply.status, started)
        return reply

    def _serve(self, method: Any, raw_body: Any, headers: Any, seen: List[Subject]) -> AppRequestReply:
        checked = self._check(method, raw_body, headers, seen=seen)
        if isinstance(checked, AppRequestReply):
            return checked
        if checked.mode == "ask":
            return _reply(200, after=lambda: self._answer(checked))
        started = _clock()
        try:
            _call(checked.fn, checked.subject)
        except Exception as error:
            self._report(error, checked.subject, checked.mode, own=True)
            return _reply(500, checked.failed)
        self._timed(checked, started)
        return _reply(200)

    def _timed(self, run: _Run, started: float) -> None:
        """Reports a tell or an event whose function ran past Siren's wait."""
        slow = _slow(run, _clock() - started)
        if slow is not None:
            self._report(slow, run.subject, run.mode)

    def _debug(self, seen: List[Subject], status: int, started: float) -> None:
        """The request's one debug line, with its status and time."""
        try:
            took = round((_clock() - started) * 1000)
            self._logger.debug("%s", f"Siren request handler{_describe(seen[0] if seen else None)}: {status} in {took} ms")
        except Exception:
            pass  # A logger that raises must not take the reply with it.

    def _check(
        self,
        method: Any,
        raw_body: Any,
        headers: Any,
        *,
        seen: Optional[List[Subject]] = None,
        report: bool = True,
    ) -> Union[AppRequestReply, _Run]:
        """The reply for a request the handler refuses, or the function to
        run. ``seen`` collects the checked request, for the debug line.
        ``report`` is false from ``simulate()``, which hands a refused
        signature to no one."""
        if not isinstance(method, str) or method.upper() != "POST":
            return _reply(405, "Siren sends its requests with POST.", headers=_JSON_HEADERS + (("Allow", "POST"),))
        too_large = _reply(413, f"The request body is over {MAX_BODY_BYTES} bytes, more than Siren sends.")
        if raw_body is _TOO_LARGE:
            return too_large
        if isinstance(raw_body, str):
            body = raw_body.encode("utf-8")
        elif isinstance(raw_body, (bytes, bytearray, memoryview)):
            body = bytes(raw_body)
        else:
            self._report(SirenError(BODY_ALREADY_PARSED), None)
            return _reply(500, BODY_ALREADY_PARSED)
        if len(body) > MAX_BODY_BYTES:
            return too_large
        names = _headers(headers)

        event = _header(names, "X-Siren-Event")
        if event is None or event == REQUEST_EVENT:
            try:
                request = Apps.verify_request(body, names, self._secret, tolerance_seconds=self._tolerance)
            except _BodyRefused as error:
                return _reply(400, error.message)
            except SignatureVerificationError as error:
                if report:
                    self._report(error, None, signature=True)
                return _reply(401, error.message)
            if seen is not None:
                seen.append(request)
            if request.mode == "ask":
                wrong_siren = self._wrong_siren(request.answer_url or "")
                if wrong_siren is not None:
                    self._report(SirenError(wrong_siren), request)
                    return _reply(500, wrong_siren)
            kind, key = request.feature["kind"], request.feature["key"]
            fn = self._functions.get((kind, key)) or self._functions.get((kind, None))
            if fn is None:
                return _reply(404, f"No function handles {kind} {key}.")
            return _Run(
                request.mode,
                fn,
                request,
                f"{kind} {key}",
                f"The function for {kind} {key} failed, so Siren will send the request again.",
            )

        try:
            _check_signature(body, names, self._secret, self._tolerance, _now())
        except SignatureVerificationError as error:
            if report:
                self._report(error, None, signature=True)
            return _reply(401, error.message)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _reply(400, "The signed body is not JSON.")
        if not isinstance(payload, dict):
            return _reply(400, "The signed body is not a JSON object.")
        handles = self._events.get(event)
        if handles is None:
            return _reply(200)
        delivery = _header(names, "X-Siren-Delivery")
        if _is_webhook_envelope(payload, event, delivery):
            subject = WebhookEvent(type=payload["type"], data=payload["data"], delivery_id=payload["id"], raw=payload)
        else:
            subject = WebhookEvent(type=event, data=payload, delivery_id=delivery, raw=payload)
        if seen is not None:
            seen.append(subject)
        return _Run("event", handles, subject, f"the {event} event", f"The function for the {event} event failed, so Siren will send it again.")

    def _wrong_siren(self, answer_url: str) -> Optional[str]:
        """Why an ask's answer can't go where the request expects it, or
        ``None`` when the signed ``answerUrl`` is on the client's Siren."""
        base = self._requests._client.base_url
        signed = _origin(answer_url)
        if signed is not None and signed == _origin(base):
            return None
        marker = answer_url.find("/apps/requests/")
        sent_from = answer_url[:marker] if marker > 0 else answer_url
        return (
            f"Siren sent this request from {answer_url}, but this client sends answers to {base}. "
            f"Set base_url to {sent_from}."
        )

    def _answer(self, run: _Run) -> None:
        """An ask's work: run the function, then post what it returned."""
        try:
            answer = _call(run.fn, run.subject)
        except Exception as error:
            self._report(error, run.subject, "ask", own=True)
            return
        self._post(run, answer)

    async def _answer_async(self, run: _Run) -> None:
        try:
            answer = await _call_async(run.fn, run.subject)
        except Exception as error:
            self._report(error, run.subject, "ask", own=True)
            return
        await asyncio.to_thread(self._post, run, answer)

    def _post(self, run: _Run, answer: Any) -> None:
        if answer is _LATER:
            return
        request = run.subject
        assert isinstance(request, AppRequest)
        if answer is None:
            self._report(self._returned_nothing(run), request, "ask")
            return
        try:
            _checked_answer(f"The function for {run.label}", request.id, answer)
        except ValueError as error:
            self._report(AppHandlerError(str(error)), request, "ask")
            return
        try:
            # Retried on a lost connection, 429 and 5xx, since the same bytes
            # go each time and Siren takes the same answer again as a
            # duplicate. A refusal is never resent.
            self._requests._send(f"The function for {run.label}", request.id, answer, retryable=True)
        except Exception as error:
            self._report(error, request, "ask")

    def _hand_off(self, work: Work, subject: Optional[Subject], default: Optional[Callable[[Work], None]] = None) -> None:
        """Gives an ask's work to ``run_later``, or to the face's default."""
        try:
            if self._run_later is not None:
                self._run_later(work)
            elif default is not None:
                default(work)
            else:
                work()
        except Exception as error:
            # run_later is the app's own function.
            self._report(error, subject, own=True)

    def _report(
        self,
        error: BaseException,
        subject: Optional[Subject],
        mode: Optional[str] = None,
        *,
        own: bool = False,
        signature: bool = False,
    ) -> None:
        """Hands a failure to ``on_error``, or writes the default line.
        ``own`` is true when the app's own function raised it, the one case
        whose line carries a traceback. ``signature`` marks a refused
        signature."""
        try:
            if self._on_error is not None:
                self._on_error(error, subject)
            else:
                self._default_line(error, subject, mode, own, signature)
        except Exception:
            try:
                self._logger.exception("%s", f"The Siren request handler's on_error raised while reporting: {_one_line(str(error))}")
            except Exception:
                pass  # A logger that raises must not take the reply or the process with it.

    def _default_line(self, error: BaseException, subject: Optional[Subject], mode: Optional[str], own: bool, signature: bool) -> None:
        """The default ``on_error``: one line through the logger. A refused
        signature and a slow tell are warnings, and every other failure an
        error. A failed tell's or ask's line says when Siren sends it again."""
        if signature:
            self._refused(error)
            return
        line = f"Siren request handler{_describe(subject)}: {_error_text(error)}"
        if isinstance(error, AppSlowTellError):
            self._logger.warning("%s", line)
            return
        if isinstance(subject, AppRequest) and mode in ("tell", "ask"):
            after = _next_send(error, subject, mode)
            if after:
                line = f"{line} {after}"
        self._logger.error("%s", line, exc_info=error if own else None)

    def _refused(self, error: BaseException) -> None:
        """A refused signature's warning, five a minute at most. The rest of
        the minute are counted and written as one line when the next refusal
        after it comes. No timer runs, so nothing keeps a process alive."""
        now = _clock()
        with self._refusals_lock:
            counted = 0
            if self._refusals_since is None or now - self._refusals_since >= 60:
                counted = self._refusals_counted
                self._refusals_since, self._refusals_written, self._refusals_counted = now, 0, 0
            write = self._refusals_written < SIGNATURE_LINES_PER_MINUTE
            if write:
                self._refusals_written += 1
            else:
                self._refusals_counted += 1
        if counted:
            requests = "request" if counted == 1 else "requests"
            self._logger.warning(
                "%s",
                f"{counted} more {requests} failed the signature check in the last minute. "
                "When every request fails, SIREN_SIGNING_SECRET is wrong or was rotated.",
            )
        if write:
            hint = _SIGNATURE_HINTS.get(getattr(error, "code", None) or "")
            words = _one_line(str(error))
            self._logger.warning("%s", f"Siren request handler: refused a request with 401. {words}" + (f" {hint}" if hint else ""))


# Running a function, plain or async ----------------------------------------


def _now() -> float:
    return time.time()


def _clock() -> float:
    """The handler's clock for timing a tell and counting refusals, in
    seconds. Tests put a fake one here."""
    return time.monotonic()


def _slow(run: _Run, took: float) -> Optional[AppSlowTellError]:
    """The error for a tell or a webhook event whose function ran past
    Siren's wait, or ``None``. Siren delivers both with the same 8-second
    timeout, so a slower function runs twice. An event is named by its type,
    as the PHP SDK names it."""
    if run.mode not in ("tell", "event") or took <= TELL_TIME_LIMIT_SECONDS:
        return None
    name = run.subject.type if isinstance(run.subject, WebhookEvent) else run.label
    return AppSlowTellError(
        f"{name} took {took:.1f} s. Siren stops waiting at {TELL_TIME_LIMIT_SECONDS} s and sends the {run.mode} again, "
        "so it ran twice. Save what you need, hand the slow part to run_later or your own queue, and return."
    )


def _describe(subject: Optional[Subject]) -> str:
    """A request's name in a line: its kind, key and id, or an event's type
    and delivery id."""
    if isinstance(subject, AppRequest):
        return f" ({subject.feature['kind']} {subject.feature['key']} {subject.id})"
    if isinstance(subject, WebhookEvent):
        return " (" + " ".join(part for part in (subject.type, subject.delivery_id) if part) + ")"
    return ""


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _error_text(error: BaseException) -> str:
    """An error as one sentence: its message, its code when the message
    doesn't hold it, and a cause folded in as "(cause: ...)". Never its
    data, which can hold the app's business data."""
    words = _one_line(str(error)) or type(error).__name__
    stop = ""
    if words.endswith((".", "!", "?")):
        words, stop = words[:-1], words[-1]
    code = getattr(error, "code", None)
    if isinstance(code, str) and code and code not in words:
        words += f" ({code})"
    cause = error.__cause__
    if isinstance(cause, BaseException):
        told = _one_line(str(cause)).rstrip(".")
        if told and told not in words:
            words += f" (cause: {told})"
    return words + (stop or ".")


def _call(fn: Callable[[Any], Any], subject: Any) -> Any:
    """Calls a function from plain code, running what an ``async def`` one
    returns to the end."""
    result = fn(subject)
    if inspect.isawaitable(result):
        return _run_awaitable(result)
    return result


def _run_awaitable(awaitable: Awaitable[Any]) -> Any:
    async def wait() -> Any:
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(wait())
    # A loop already runs on this thread, so the coroutine gets its own.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, wait()).result()


async def _call_async(fn: Callable[[Any], Any], subject: Any) -> Any:
    """Calls a function under ASGI: an ``async def`` one is awaited, and a
    plain one runs on a worker thread."""
    if inspect.iscoroutinefunction(fn):
        return await fn(subject)
    result = await asyncio.to_thread(fn, subject)
    if inspect.isawaitable(result):
        result = await result
    return result


def _headers(headers: Any) -> Dict[str, Any]:
    """Headers as a plain dict, from a mapping, a framework's header object,
    or a list of pairs."""
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    merged: Dict[str, Any] = {}
    for name, value in items:
        if isinstance(name, bytes):
            name = name.decode("latin-1")
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        lower = str(name).lower()
        if lower not in merged:
            merged[lower] = value
    return merged


# The faces -------------------------------------------------------------------


class _ClosingBody:
    """A WSGI body whose ``close()``, which the server calls once the body is
    sent, starts an ask's work."""

    def __init__(self, data: bytes, close: Optional[Callable[[], None]]) -> None:
        self._data = data
        self._close = close

    def __iter__(self) -> Iterator[bytes]:
        yield self._data

    def close(self) -> None:
        close, self._close = self._close, None
        if close is not None:
            close()


class _Wsgi:
    """The handler as a WSGI app. It answers at any path it is mounted on."""

    def __init__(self, handler: AppRequestHandler) -> None:
        self._handler = handler

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        headers = {key[5:].replace("_", "-"): value for key, value in environ.items() if key.startswith("HTTP_")}
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        reply = self._handler._handle(environ.get("REQUEST_METHOD"), _wsgi_body(environ), headers)
        data = reply.content
        start_response(reply.status_line, list(reply.headers) + [("Content-Length", str(len(data)))])
        after = reply.after
        if after is None:
            return _ClosingBody(data, None)
        return _ClosingBody(data, lambda: self._handler._hand_off(after, None))


def _wsgi_body(environ: Mapping[str, Any]) -> Any:
    """The body from ``wsgi.input``, ``None`` when something read it first, or
    the too-large marker for a body over the limit, which is left unread."""
    stream = environ.get("wsgi.input")
    if stream is None:
        return None
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_BODY_BYTES:
        return _TOO_LARGE
    if length <= 0:
        if not environ.get("wsgi.input_terminated"):
            return b""
        # One byte past the limit is enough to know it is too long.
        data = bytes(stream.read(MAX_BODY_BYTES + 1))
        return _TOO_LARGE if len(data) > MAX_BODY_BYTES else data
    data = bytes(stream.read(length))
    # Fewer bytes than the header promised means a middleware read them.
    return data if len(data) == length else None


class _Asgi:
    """The handler as an ASGI app. A tell's function, or an event's, runs
    before the reply. An ask's runs after it, in the same ASGI call."""

    def __init__(self, handler: AppRequestHandler) -> None:
        self._handler = handler

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            raise RuntimeError(f"The Siren request handler serves HTTP, not {scope['type']}.")

        chunks: List[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                break  # Stop reading. The reply is 413.
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        handler = self._handler
        started = _clock()
        seen: List[Subject] = []
        body: Any = _TOO_LARGE if size > MAX_BODY_BYTES else b"".join(chunks)
        checked = handler._check(scope.get("method"), body, list(scope.get("headers") or []), seen=seen)
        run: Optional[_Run] = None
        if isinstance(checked, AppRequestReply):
            reply = checked
        elif checked.mode == "ask":
            reply, run = _reply(200), checked
        else:
            ran = _clock()
            try:
                await _call_async(checked.fn, checked.subject)
                reply = _reply(200)
                handler._timed(checked, ran)
            except Exception as error:
                handler._report(error, checked.subject, checked.mode, own=True)
                reply = _reply(500, checked.failed)
        handler._debug(seen, reply.status, started)

        data = reply.content
        headers = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in reply.headers]
        headers.append((b"content-length", str(len(data)).encode()))
        await send({"type": "http.response.start", "status": reply.status, "headers": headers})
        await send({"type": "http.response.body", "body": data})

        if run is None:
            return
        if handler._run_later is not None:
            asked = run
            handler._hand_off(lambda: handler._answer(asked), run.subject)
            return
        await handler._answer_async(run)


def _flask_view(handler: AppRequestHandler) -> Callable[[], Any]:
    def siren_request_handler() -> Any:
        """The handler as a Flask view. Flask is imported when a request comes."""
        from flask import Response, request

        body: Any
        if (request.content_length or 0) > MAX_BODY_BYTES:
            body = _TOO_LARGE
        else:
            data = request.get_data(cache=True)
            body = data
            if not data and (request.content_length or 0) > 0:
                body = None  # Something read the stream first.
        reply = handler._handle(request.method, body, request.headers)
        response = Response(reply.content, status=reply.status, headers=list(reply.headers))
        after = reply.after
        if after is not None:
            # Flask calls this once the server has sent the body.
            response.call_on_close(lambda: handler._hand_off(after, None))
        return response

    return siren_request_handler


def _django_view(handler: AppRequestHandler) -> Callable[[Any], Any]:
    def siren_request_handler(request: Any) -> Any:
        """The handler as a Django view. Django is imported when a request comes."""
        from django.http import HttpResponse  # type: ignore[import-untyped,import-not-found,unused-ignore]
        from django.http.request import RawPostDataException  # type: ignore[import-untyped,import-not-found,unused-ignore]

        body: Any
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        try:
            body = _TOO_LARGE if length > MAX_BODY_BYTES else request.body
        except RawPostDataException:
            body = None
        reply = handler._handle(request.method, body, request.headers)
        response = HttpResponse(reply.content, status=reply.status, content_type="application/json")
        for name, value in reply.headers:
            response[name] = value
        after = reply.after
        if after is not None:
            # Django has no public hook after the reply, so the work starts on
            # its own thread.
            handler._hand_off(after, None, default=lambda work: threading.Thread(target=work, name="siren-answer").start())
        return response

    siren_request_handler.csrf_exempt = True  # type: ignore[attr-defined]
    return siren_request_handler
