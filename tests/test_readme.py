"""The README's app token snippets, run as written against a fake Siren on
127.0.0.1. The fake plays the token route and a custom event's send address
the way docs/architecture/apps-event-tokens.md (sections 1 and 2) describes
them. Nothing leaves the machine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
import pytest

import siren

ROOT = Path(__file__).resolve().parent.parent
APP_KEY = "sk_test_fake_app_key"


def snippet(name: str, language: str = "python") -> str:
    readme = (ROOT / "README.md").read_text()
    found = re.search(r"<!-- readme-test: " + re.escape(name) + r" -->\n```" + language + r"\n(.*?)```", readme, re.S)
    assert found, f"README has no snippet named {name}"
    return found.group(1)


class FakeSiren(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else {}
        auth = self.headers.get("Authorization", "")
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        self.path = url.path
        FakeSiren.seen.append((self.path, auth, body))

        if self.path == "/siren/v1/oauth/token":
            if body.get("grant_type") != "client_credentials" or body.get("client_secret") != APP_KEY:
                return self._json(401, {"error": "invalid_client", "error_description": "Siren doesn't know this key, or it was revoked."})
            scope = body.get("scope", "")
            if scope == "":
                return self._json(200, {"access_token": "srv.fake-token", "token_type": "Bearer", "expires_in": 1200})
            if scope == "browser:signup-completed":
                return self._json(200, {"access_token": "brw.fake-token", "token_type": "Bearer", "expires_in": 600, "scope": scope})
            return self._json(400, {"error": "invalid_scope", "error_description": f'This app has no active custom event "{scope[8:]}".'})

        match = re.fullmatch(r"/siren/v1/event/([^/?]+)", self.path)
        if match:
            # A server send carries the server token as a bearer, and a
            # browser send carries the browser token as ?access_token=.
            if auth != "Bearer srv.fake-token" and query.get("access_token") != ["brw.fake-token"]:
                return self._json(401, {"error": "This access token has expired."})
            # Siren reads the collaborator who gets credit from the top of
            # the body (CustomEventIngestion::ingest()).
            if "collaboratorId" not in body:
                return self._json(
                    400, {"error": {"message": "collaboratorId is required", "context": {"type": "VALIDATION_FAILED"}}}
                )
            return self._json(200, {"status": "recorded"}, {"X-Siren-OID": "9001"})

        return self._json(404, {"error": "Unknown route"})

    def _json(self, status, payload, headers=None):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture(scope="module")
def siren_url():
    server = _serve(ThreadingHTTPServer(("127.0.0.1", 0), FakeSiren))
    yield f"http://127.0.0.1:{server.server_address[1]}/siren/v1"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def fresh_log():
    FakeSiren.seen = []


def _handler_from_snippet(monkeypatch, siren_url, key=APP_KEY):
    monkeypatch.setenv("SIREN_API_KEY", key)
    monkeypatch.setenv("SIREN_BASE_URL", siren_url)
    scope: dict = {}
    exec(compile(snippet("handler"), "README.md (handler)", "exec"), scope)
    return scope["tokens"]


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def test_the_server_send_snippet_trades_the_key_and_sends_with_the_token(siren_url, tmp_path):
    script = tmp_path / "send.py"
    script.write_text(snippet("server-send"))
    env = {**os.environ, "SIREN_API_KEY": APP_KEY, "SIREN_BASE_URL": siren_url}

    run = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=30)

    assert run.returncode == 0, run.stderr
    assert run.stdout == "recorded\n"
    assert FakeSiren.seen == [
        ("/siren/v1/oauth/token", "", {"grant_type": "client_credentials", "client_secret": APP_KEY}),
        ("/siren/v1/event/signup-completed", "Bearer srv.fake-token", {"collaboratorId": 12, "plan": "pro"}),
    ]


def test_the_handler_snippet_served_over_wsgi_answers_each_status(monkeypatch, siren_url):
    tokens = _handler_from_snippet(monkeypatch, siren_url)
    server = _serve(make_server("127.0.0.1", 0, tokens.wsgi, handler_class=_QuietHandler))
    page = f"http://127.0.0.1:{server.server_address[1]}/siren/token"
    try:
        ok = httpx.post(page, json={"event": "signup-completed"})
        again = httpx.get(page, params={"event": "signup-completed"})
        missing = httpx.get(page)
        outside = httpx.get(page, params={"event": "trial-started"})
    finally:
        server.shutdown()
        server.server_close()

    assert ok.status_code == 200
    assert ok.json() == {"token": "brw.fake-token", "expiresIn": 600, "url": f"{siren_url}/event/signup-completed"}
    assert ok.headers["content-type"] == "application/json"
    assert ok.headers["cache-control"] == "no-store"
    assert (again.status_code, again.json()["token"]) == (200, "brw.fake-token")
    assert missing.status_code == 400 and "error" in missing.json()
    assert (outside.status_code, outside.json()) == (400, {"error": "This server gives no browser token for that event."})
    assert [s[2].get("scope") for s in FakeSiren.seen] == ["browser:signup-completed"], (
        "One grant served both page loads, and refused requests never reach Siren."
    )


def test_the_handler_snippet_over_asgi_answers_502_when_siren_refuses_the_key(monkeypatch, siren_url):
    tokens = _handler_from_snippet(monkeypatch, siren_url, key="sk_test_revoked")
    sent = []
    messages = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"event=signup-completed", "headers": []}
    asyncio.run(tokens.asgi(scope, receive, send))

    assert sent[0]["status"] == 502
    assert json.loads(sent[1]["body"]) == {"error": "Siren could not be reached for a browser token. Try again shortly."}


def test_the_handler_snippet_answers_400_when_siren_refuses_the_event(monkeypatch, siren_url):
    monkeypatch.setenv("SIREN_API_KEY", APP_KEY)
    monkeypatch.setenv("SIREN_BASE_URL", siren_url)
    scope: dict = {}
    exec(compile(snippet("handler").replace('events=["signup-completed"]', "events=None"), "README.md", "exec"), scope)

    status, body = scope["tokens"](None, {"event": "trial-started"})

    assert (status, body) == (400, {"error": 'This app has no active custom event "trial-started".'})


def test_the_examples_folder_holds_the_readme_code():
    send = (ROOT / "examples" / "send_custom_event.py").read_text()
    assert send.endswith(snippet("server-send"))
    for name in ("fastapi_app.py", "flask_app.py", "django_views.py"):
        source = (ROOT / "examples" / name).read_text()
        assert "client.events.browser_token_handler(" in source, name
        assert "client.apps.request_handler(" in source and "@answers.on(" in source, name
        compile(source, name, "exec")


def test_the_page_snippet_sends_a_body_siren_accepts(siren_url):
    page = snippet("page", "html")
    assert (ROOT / "examples" / "page.html").read_text() == page
    call = re.search(r"sirenTrack\('([\w-]+)', (\{.*?\})\);", page)
    assert call, "The page snippet calls sirenTrack()."
    # The page's object literal, as JSON: quote the keys and the strings.
    literal = re.sub(r"([{,]\s*)(\w+):", r'\1"\2":', call.group(2))
    data = json.loads(re.sub(r"'([^']*)'", r'"\1"', literal))

    with siren.Siren(api_key=APP_KEY, base_url=siren_url) as client:
        token = client.events.browser_token(call.group(1))
    send = f"{token.url}?access_token={token.token}"

    accepted = httpx.post(send, json=data)
    assert (accepted.status_code, accepted.json().get("status")) == (200, "recorded"), "Siren takes the body the README page sends."

    data.pop("collaboratorId")
    assert httpx.post(send, json=data).status_code == 400, "The fake refuses a body with no collaboratorId, as Siren does."
