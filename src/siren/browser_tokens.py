"""The browser token address on the app's own server, for any framework.

A page never holds the app's key. It asks the app's server for a browser
token for one event, then posts the event's data to Siren with it. This
module is that address. :class:`BrowserTokenHandler` is plain Python: call it
with the request body and query for ``(status, json)``, or mount its
``wsgi`` or ``asgi`` app. No web framework is needed or imported.
"""

from __future__ import annotations

import asyncio
import json
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from urllib.parse import parse_qs

from .errors import SirenError

if TYPE_CHECKING:  # pragma: no cover
    from .events import Events

Body = Union[bytes, str, Mapping[str, Any], None]
Query = Union[Mapping[str, Any], str, bytes, None]

_REASONS = {200: "200 OK", 400: "400 Bad Request", 502: "502 Bad Gateway"}
_HEADERS = [("Content-Type", "application/json"), ("Cache-Control", "no-store")]

NO_EVENT = 'Name the event as {"event": "..."} in a JSON body or as ?event= in the query.'
NOT_OFFERED = "This server gives no browser token for that event."
UNREACHABLE = "Siren could not be reached for a browser token. Try again shortly."


class BrowserTokenHandler:
    """Answers a page's request for a browser token.

    It reads the event key from a JSON body ``{"event": "..."}`` or the query
    ``?event=``, and answers:

    - 200 ``{"token", "expiresIn", "url"}`` from ``events.browser_token()``.
    - 400 ``{"error"}`` when the request names no event, names one outside
      ``events``, or names one Siren refuses (``invalid_scope``: the app has
      no such event, or it allows no domain).
    - 502 ``{"error"}`` when Siren can't be reached or refuses the app's key.

    Use it three ways::

        status, body = handler(request_body, request_query)   # any framework
        handler.wsgi                                          # a WSGI app: Flask, Django
        handler.asgi                                          # an ASGI app: FastAPI, Starlette
    """

    def __init__(self, resource: "Events", *, events: Optional[Sequence[str]] = None) -> None:
        if events is not None and (
            isinstance(events, (str, bytes)) or not all(isinstance(e, str) for e in events)
        ):
            raise ValueError("events must be a list of event keys")
        self._events = resource
        self._allowed: Optional[List[str]] = None if events is None else list(events)
        self.wsgi = _WsgiApp(self)
        self.asgi = _AsgiApp(self)

    def __call__(self, body: Body = None, query: Query = None) -> Tuple[int, Dict[str, Any]]:
        """Answer one request: ``(status, json)``.

        ``body`` is the raw body (bytes or text) or one the framework already
        decoded. ``query`` is the query parameters as a mapping, such as
        Flask's ``request.args`` or Django's ``request.GET``, or the raw
        query string.
        """
        event = _event_from(body, query)
        if event is None:
            return 400, {"error": NO_EVENT}
        if self._allowed is not None and event not in self._allowed:
            return 400, {"error": NOT_OFFERED}
        try:
            return 200, self._events.browser_token(event).to_dict()
        except SirenError as exc:
            # ``invalid_scope`` is Siren refusing this event. Anything else
            # is the server's own trouble, which the page can't fix.
            if exc.code == "invalid_scope":
                return 400, {"error": exc.message}
            return 502, {"error": UNREACHABLE}


class _WsgiApp:
    """The handler as a WSGI app. It answers any path it is mounted at."""

    def __init__(self, handler: BrowserTokenHandler) -> None:
        self._handler = handler

    def __call__(
        self, environ: Mapping[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        body = environ["wsgi.input"].read(length) if length > 0 else b""
        status, payload = self._handler(body, environ.get("QUERY_STRING", ""))
        data = json.dumps(payload).encode("utf-8")
        start_response(_REASONS[status], _HEADERS + [("Content-Length", str(len(data)))])
        return [data]


class _AsgiApp:
    """The handler as an ASGI app. Siren is asked on a worker thread."""

    def __init__(self, handler: BrowserTokenHandler) -> None:
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
            raise RuntimeError(f"The browser token handler serves HTTP, not {scope['type']}.")

        chunks: List[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        status, payload = await asyncio.to_thread(
            self._handler, b"".join(chunks), scope.get("query_string", b"")
        )
        data = json.dumps(payload).encode("utf-8")
        headers = [(k.lower().encode(), v.encode()) for k, v in _HEADERS]
        headers.append((b"content-length", str(len(data)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": data})


def _event_from(body: Body, query: Query) -> Optional[str]:
    decoded: Any = body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            decoded = json.loads(body) if body.strip() else None
        except ValueError:
            decoded = None

    event: Any = decoded.get("event") if isinstance(decoded, Mapping) else None
    if not isinstance(event, str):
        event = _query_value(query)
    if isinstance(event, str) and event.strip():
        return event.strip()
    return None


def _query_value(query: Query) -> Any:
    if isinstance(query, bytes):
        query = query.decode("utf-8", errors="replace")
    if isinstance(query, str):
        values = parse_qs(query).get("event")
        return values[0] if values else None
    if isinstance(query, Mapping):
        value = query.get("event")
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
        return value
    return None
