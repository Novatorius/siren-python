"""The browser token address on the app's server: the plain call, and its
WSGI and ASGI apps."""

from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest

import siren
from siren.browser_tokens import NO_EVENT, NOT_OFFERED, UNREACHABLE

from .conftest import API_KEY, BASE_URL

URL = f"{BASE_URL}/event/demo-booked"
GRANT = {"access_token": "brw.one", "token_type": "Bearer", "expires_in": 600, "scope": "browser:demo-booked"}
OK = {"token": "brw.one", "expiresIn": 600, "url": URL}


@pytest.fixture
def quick_client():
    with siren.Siren(api_key=API_KEY, max_retries=0) as instance:
        yield instance


def _granted(mocked_api):
    return mocked_api.post("/oauth/token").respond(200, json=GRANT)


class TestPlainCall:
    def test_200_for_an_event_in_a_json_body(self, client, mocked_api):
        _granted(mocked_api)
        handler = client.events.browser_token_handler()

        assert handler(b'{"event": "demo-booked"}') == (200, OK)

    def test_200_for_an_event_in_the_query(self, client, mocked_api):
        grant = _granted(mocked_api)
        handler = client.events.browser_token_handler()

        assert handler(None, {"event": "demo-booked"}) == (200, OK)
        assert handler("", "event=demo-booked") == (200, OK)
        assert handler(b"", {"event": ["demo-booked"]}) == (200, OK)
        assert json.loads(grant.calls.last.request.content)["scope"] == "browser:demo-booked"

    def test_200_for_a_body_the_framework_already_decoded(self, client, mocked_api):
        _granted(mocked_api)

        assert client.events.browser_token_handler()({"event": "demo-booked"}) == (200, OK)

    def test_one_token_serves_many_page_loads(self, client, mocked_api):
        grant = _granted(mocked_api)
        handler = client.events.browser_token_handler()

        handler(None, {"event": "demo-booked"})
        handler(None, {"event": "demo-booked"})

        assert grant.call_count == 1

    @pytest.mark.parametrize(
        "body,query",
        [(None, None), (b"", {}), (b"{}", None), (b'{"event": ""}', None), (b'{"event": 42}', None), (b"not json", None), (None, "event=")],
    )
    def test_400_when_no_event_is_named(self, client, mocked_api, body, query):
        handler = client.events.browser_token_handler()

        assert handler(body, query) == (400, {"error": NO_EVENT})
        assert len(mocked_api.calls) == 0, "Siren is never asked."

    def test_400_for_an_event_outside_the_list(self, client, mocked_api):
        handler = client.events.browser_token_handler(events=["trial-started"])

        assert handler(None, {"event": "demo-booked"}) == (400, {"error": NOT_OFFERED})
        assert len(mocked_api.calls) == 0

    def test_400_with_sirens_words_when_siren_refuses_the_event(self, client, mocked_api):
        mocked_api.post("/oauth/token").respond(
            400, json={"error": "invalid_scope", "error_description": 'The event "demo-booked" allows no domain, so no browser may send it.'}
        )
        handler = client.events.browser_token_handler(events=["demo-booked"])

        assert handler(None, {"event": "demo-booked"}) == (
            400,
            {"error": 'The event "demo-booked" allows no domain, so no browser may send it.'},
        )

    @pytest.mark.parametrize(
        "answer",
        [
            httpx.ConnectError("Connection refused"),
            httpx.Response(401, json={"error": "invalid_client", "error_description": "Siren doesn't know this key."}),
            httpx.Response(400, json={"error": "unauthorized_client", "error_description": "Only an app's key can get an access token."}),
            httpx.Response(503, json={"error": "temporarily_unavailable", "error_description": "Try again shortly."}),
        ],
        ids=["no connection", "key refused", "key of no app", "server error"],
    )
    def test_502_when_siren_cannot_give_a_token(self, quick_client, mocked_api, answer):
        mocked_api.post("/oauth/token").mock(side_effect=[answer])
        handler = quick_client.events.browser_token_handler()

        status, body = handler(None, {"event": "demo-booked"})

        assert (status, body) == (502, {"error": UNREACHABLE})
        assert API_KEY not in json.dumps(body)

    def test_refuses_an_events_option_that_is_not_a_list(self, client):
        with pytest.raises(ValueError):
            client.events.browser_token_handler(events="demo-booked")


def _wsgi(app, method="GET", query="", body=b""):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/",
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(body)) if body else "",
        "CONTENT_TYPE": "application/json",
        "wsgi.input": io.BytesIO(body),
    }
    started = {}

    def start_response(status, headers):
        started["status"] = status
        started["headers"] = dict(headers)

    data = b"".join(app(environ, start_response))
    return started["status"], started["headers"], json.loads(data)


class TestWsgi:
    def test_answers_200_from_a_json_body(self, client, mocked_api):
        _granted(mocked_api)
        app = client.events.browser_token_handler().wsgi

        status, headers, body = _wsgi(app, "POST", body=b'{"event": "demo-booked"}')

        assert status == "200 OK"
        assert headers["Content-Type"] == "application/json"
        assert headers["Cache-Control"] == "no-store"
        assert body == OK

    def test_answers_400_and_502(self, quick_client, mocked_api):
        mocked_api.post("/oauth/token").mock(side_effect=httpx.ConnectError("Connection refused"))
        app = quick_client.events.browser_token_handler(events=["demo-booked"]).wsgi

        assert _wsgi(app)[0] == "400 Bad Request"
        assert _wsgi(app, query="event=other")[2] == {"error": NOT_OFFERED}
        assert _wsgi(app, query="event=demo-booked")[:3:2] == ("502 Bad Gateway", {"error": UNREACHABLE})


def _asgi(app, method="GET", query=b"", body=b""):
    sent = []
    messages = [
        {"type": "http.request", "body": body[:5], "more_body": len(body) > 5},
        {"type": "http.request", "body": body[5:], "more_body": False},
    ]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": "/", "query_string": query, "headers": []}
    asyncio.run(app(scope, receive, send))
    start, payload = sent
    return start["status"], dict(start["headers"]), json.loads(payload["body"])


class TestAsgi:
    def test_answers_200_from_a_body_in_two_chunks(self, client, mocked_api):
        _granted(mocked_api)
        app = client.events.browser_token_handler().asgi

        status, headers, body = _asgi(app, "POST", body=b'{"event": "demo-booked"}')

        assert status == 200
        assert headers[b"content-type"] == b"application/json"
        assert headers[b"cache-control"] == b"no-store"
        assert body == OK

    def test_answers_400_and_502(self, quick_client, mocked_api):
        mocked_api.post("/oauth/token").mock(side_effect=httpx.ConnectError("Connection refused"))
        app = quick_client.events.browser_token_handler(events=["demo-booked"]).asgi

        assert _asgi(app)[0] == 400
        assert _asgi(app, query=b"event=other")[2] == {"error": NOT_OFFERED}
        assert _asgi(app, query=b"event=demo-booked")[::2] == (502, {"error": UNREACHABLE})

    def test_completes_a_lifespan(self, client):
        app = client.events.browser_token_handler().asgi
        messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message["type"])

        asyncio.run(app({"type": "lifespan"}, receive, send))

        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
