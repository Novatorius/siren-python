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
  version 1 request.
- A tell: 200 once its function returns, or 500 when it raises, so Siren
  sends it again.
- An ask: 200 at once. The function runs after the reply, and what it
  returns is posted as the answer. When it returns ``None``, the app answers
  later with ``apps.requests.answer()``.
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
from http import HTTPStatus
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Tuple,
    TypeVar,
    Union,
    overload,
)

from .apps import REQUEST_EVENT, Apps, _BodyRefused, _check_signature, _check_tolerance, _header
from .errors import SignatureVerificationError, SirenError
from .types import AppRequest, WebhookEvent
from .webhooks import _is_envelope as _is_webhook_envelope

if TYPE_CHECKING:  # pragma: no cover
    from .apps import AppRequests

__all__ = ["AppRequestHandler", "AppRequestReply", "MAX_BODY_BYTES"]

#: The longest body the handler reads, 1 MiB, as in the Node SDK. Siren's
#: requests are far smaller, since an answer alone stays under 16 KB.
MAX_BODY_BYTES = 1024 * 1024

logger = logging.getLogger("siren")

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
    ignored. An ask's is posted as the answer. An ``async def`` function is
    awaited, and under ASGI a plain one runs on a worker thread. The handler
    keeps no state, and Siren may send one request twice, so a tell's
    function writes once by ``request.id``.
    """

    def __init__(
        self,
        requests: "AppRequests",
        signing_secret: str,
        *,
        tolerance_seconds: Optional[int] = None,
        run_later: Optional[Callable[[Work], Any]] = None,
        on_error: Optional[Callable[..., Any]] = None,
    ) -> None:
        method = "apps.request_handler()"
        if not isinstance(signing_secret, str) or signing_secret == "":
            raise ValueError(f"{method} needs the app's signing secret, which Siren showed when it made the app.")
        if run_later is not None and not callable(run_later):
            raise ValueError(f"{method} takes run_later as a function that is handed the work, such as a queue's add.")
        if on_error is not None and not callable(on_error):
            raise ValueError(f"{method} takes on_error as a function of (error, request).")
        self._requests = requests
        self._secret = signing_secret
        self._tolerance = _check_tolerance(method, tolerance_seconds)
        self._run_later = run_later
        self._on_error = on_error
        self._functions: Dict[Tuple[str, Optional[str]], Callable[[Any], Any]] = {}
        self._events: Dict[str, Callable[[Any], Any]] = {}
        self.wsgi = _Wsgi(self)
        self.asgi = _Asgi(self)
        self.flask = _flask_view(self)
        self.django = _django_view(self)

    # Registration ----------------------------------------------------------

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

        Raises :class:`ValueError` for an empty kind or key, a function that
        is not callable, and a second function for the same kind and key.
        """
        if fn is None and callable(key):
            key, fn = None, key
        if not isinstance(kind, str) or kind == "":
            raise ValueError("answers.on() needs the feature's kind, such as 'distributionResolver'.")
        if key is not None and (not isinstance(key, str) or key == ""):
            raise ValueError("answers.on() takes the feature's key as text, such as 'split-by-score', or no key for every feature of the kind.")

        def register(function: F) -> F:
            if not callable(function):
                raise ValueError("answers.on() needs a function that takes the request.")
            if (kind, key) in self._functions:
                named = f"{kind} {key}" if key is not None else f"every {kind}"
                raise ValueError(f"answers.on() already has a function for {named}.")
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
            raise ValueError("answers.on_event() needs a webhook event type, such as 'conversion.approved'. App requests go to answers.on().")

        def register(function: F) -> F:
            if not callable(function):
                raise ValueError("answers.on_event() needs a function that takes the event.")
            if type in self._events:
                raise ValueError(f"answers.on_event() already has a function for {type}.")
            self._events[type] = function
            return function

        if fn is None:
            return register
        register(fn)
        return self

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

    # The core --------------------------------------------------------------

    def _handle(self, method: Any, raw_body: Any, headers: Any) -> AppRequestReply:
        checked = self._check(method, raw_body, headers)
        if isinstance(checked, AppRequestReply):
            return checked
        if checked.mode == "ask":
            return _reply(200, after=lambda: self._answer(checked))
        try:
            _call(checked.fn, checked.subject)
        except Exception as error:
            self._report(error, checked.subject)
            return _reply(500, checked.failed)
        return _reply(200)

    def _check(self, method: Any, raw_body: Any, headers: Any) -> Union[AppRequestReply, _Run]:
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
                return _reply(401, error.message)
            kind, key = request.item["kind"], request.item["key"]
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
        return _Run("event", handles, subject, f"the {event} event", f"The function for the {event} event failed, so Siren will send it again.")

    def _answer(self, run: _Run) -> None:
        """An ask's work: run the function, then post what it returned."""
        try:
            answer = _call(run.fn, run.subject)
        except Exception as error:
            self._report(error, run.subject)
            return
        self._post(run, answer)

    async def _answer_async(self, run: _Run) -> None:
        try:
            answer = await _call_async(run.fn, run.subject)
        except Exception as error:
            self._report(error, run.subject)
            return
        await asyncio.to_thread(self._post, run, answer)

    def _post(self, run: _Run, answer: Any) -> None:
        if answer is None:
            return
        request = run.subject
        assert isinstance(request, AppRequest)
        try:
            # Retried on a lost connection, 429 and 5xx, since the same bytes
            # go each time and Siren takes the same answer again as a
            # duplicate. A refusal is never resent.
            self._requests._send(f"The function for {run.label}", request.id, answer, retryable=True)
        except Exception as error:
            self._report(error, request)

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
            self._report(error, subject)

    def _report(self, error: BaseException, subject: Optional[Subject]) -> None:
        try:
            if self._on_error is not None:
                self._on_error(error, subject)
            else:
                logger.error("Siren request handler: %s", error, exc_info=error)
        except Exception:
            logger.exception("The Siren request handler's on_error raised while reporting: %s", error)


# Running a function, plain or async ----------------------------------------


def _now() -> float:
    return time.time()


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
        body: Any = _TOO_LARGE if size > MAX_BODY_BYTES else b"".join(chunks)
        checked = handler._check(scope.get("method"), body, list(scope.get("headers") or []))
        run: Optional[_Run] = None
        if isinstance(checked, AppRequestReply):
            reply = checked
        elif checked.mode == "ask":
            reply, run = _reply(200), checked
        else:
            try:
                await _call_async(checked.fn, checked.subject)
                reply = _reply(200)
            except Exception as error:
                handler._report(error, checked.subject)
                reply = _reply(500, checked.failed)

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
