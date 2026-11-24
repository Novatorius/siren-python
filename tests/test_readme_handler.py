"""The README's request handler snippets beyond the Flask sample, run as
written: each framework's mount, registering functions, the options, the
pool split and checking and answering by hand.

Each framework snippet follows the first sample's lines through its
function, as the README says, and the test plays Siren with its own signed
request for a distribution resolver
(tests/fixtures/apps-feature-contracts/requests.json). Django runs for real.
FastAPI is not a dependency of the SDK's tests, so a stand-in ``fastapi``
module records the route the snippet adds, and the test drives the ASGI app
the snippet mounted. Nothing leaves the machine."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
import pytest
import respx

import siren
from siren import testing
from siren._client import DEFAULT_BASE_URL

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
CONTRACTS = json.loads((ROOT / "tests" / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
SECRET = "cd" * 32
APP_KEY = "sk_test_fake_app_key"
ASK = "distributionResolver"
ANSWER_PATH = f"/apps/requests/{CONTRACTS['requestKey']}/answer"


def snippet(name: str) -> str:
    found = re.search(r"<!-- readme-test: " + re.escape(name) + r" -->\n```python\n(.*?)```", README, re.S)
    assert found, f"README has no snippet named {name}"
    return found.group(1)


FIRST = snippet("handler-flask")
# The first sample's lines through its function, which every mount keeps.
HEAD = FIRST[: FIRST.index("app = Flask")].replace("from flask import Flask\n", "")


def signed(kind: str = ASK):
    """Siren's own request for the kind, signed by ``siren.testing.app_request()``."""
    request = CONTRACTS["kinds"][kind]["request"]
    made = testing.app_request(kind, request["item"]["key"], data=request["data"], secret=SECRET, id=CONTRACTS["requestKey"], app=CONTRACTS["app"])
    return made.raw_body, made.headers


@pytest.fixture
def siren_api(monkeypatch):
    monkeypatch.setenv("SIREN_API_KEY", APP_KEY)
    monkeypatch.setenv("SIREN_SIGNING_SECRET", SECRET)
    # The first sample sets no base_url, so answers go to Siren Cloud's address.
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as mock:
        yield mock


class Answers:
    """The fake Siren's answer route: it records each posted answer."""

    def __init__(self, siren_api, status: int = 200, body: Any = None, seen: Any = None):
        self.posted: List[Any] = []
        self.done = threading.Event()

        def route(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen("answer")
            self.posted.append(json.loads(request.content))
            self.done.set()
            return httpx.Response(status, json=body if body is not None else {"status": "accepted"})

        siren_api.post(ANSWER_PATH).mock(side_effect=route)


def build(*snippets: str, resolver: bool = True, **scope: Any) -> Dict[str, Any]:
    """Runs the first sample's head, with the resolver's function in place of
    its own unless ``resolver`` is false, then the snippets."""
    head = HEAD
    if resolver:
        function = re.search(r"<!-- readme-test: fn-distributionResolver -->\n```python\n(.*?)```", README, re.S)
        assert function
        head = HEAD[: HEAD.index("@answers.on(")] + function.group(1)
    namespace: Dict[str, Any] = {"__name__": "readme", "add_to_crm": lambda *a: None, **scope}
    exec(compile(head + "\n" + "\n".join(snippets), "README.md", "exec"), namespace)
    return namespace


RESOLVED = CONTRACTS["kinds"][ASK]["answer"]


def test_django_urls_snippet_serves_the_handler(siren_api):
    pytest.importorskip("django")
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(DEBUG=False, ALLOWED_HOSTS=["*"], DEFAULT_CHARSET="utf-8", USE_TZ=True)
        django.setup()
    from django.test import RequestFactory
    from django.urls import resolve

    answers = Answers(siren_api)
    scope = build(snippet("handler-django"))
    urls = types.ModuleType("readme_urls")
    urls.urlpatterns = scope["urlpatterns"]  # type: ignore[attr-defined]
    sys.modules["readme_urls"] = urls
    try:
        view = resolve("/siren", urlconf="readme_urls").func
        body, headers = signed()
        extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items() if k != "Content-Type"}
        response = view(RequestFactory().post("/siren", data=body, content_type="application/json", **extra))
    finally:
        del sys.modules["readme_urls"]

    assert response.status_code == 200 and json.loads(response.content) == {}
    assert getattr(view, "csrf_exempt", False) is True
    assert answers.done.wait(5) and answers.posted == [RESOLVED]


def test_fastapi_snippet_mounts_the_asgi_app(siren_api, monkeypatch):
    routes: List[tuple] = []

    class FastAPI:
        def add_route(self, path, route, methods=None):
            routes.append((path, route, methods))

    monkeypatch.setitem(sys.modules, "fastapi", types.SimpleNamespace(FastAPI=FastAPI))
    answers = Answers(siren_api)
    scope = build(snippet("handler-asgi"))
    assert [(path, methods) for path, _, methods in routes] == [("/siren", ["POST"])]
    assert routes[0][1] is scope["answers"].asgi

    body, headers = signed()
    sent: List[Dict[str, Any]] = []
    incoming = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return incoming.pop(0)

    async def send(message):
        sent.append(message)

    asgi_scope = {
        "type": "http",
        "method": "POST",
        "path": "/siren",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    asyncio.run(routes[0][1](asgi_scope, receive, send))

    assert sent[0]["status"] == 200 and json.loads(sent[1]["body"]) == {}
    assert answers.done.wait(5) and answers.posted == [RESOLVED]


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def test_wsgi_snippet_is_the_module_application(siren_api):
    answers = Answers(siren_api)
    scope = build(snippet("handler-wsgi"))
    server = make_server("127.0.0.1", 0, scope["application"], handler_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body, headers = signed()
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/siren", data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            status, reply = response.status, json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)

    assert (status, reply) == (200, {})
    assert answers.done.wait(5) and answers.posted == [RESOLVED]


def test_plain_call_snippet_replies_before_it_answers(siren_api):
    order: List[str] = []
    answers = Answers(siren_api, seen=order.append)
    scope = build(snippet("handler-call"), send_reply=lambda status, headers, content: order.append(f"reply {status}"))

    body, headers = signed()
    scope["siren_view"](body, headers, "POST")

    assert order == ["reply 200", "answer"]
    assert answers.posted == [RESOLVED]


def test_register_snippet_takes_a_kind_function_and_an_event_function(siren_api):
    answers = Answers(siren_api)
    seen: List[Any] = []
    scope = build(
        snippet("register"),
        resolver=False,
        split=lambda request: RESOLVED,
        noted=lambda event: seen.append(event.type),
    )
    handler = scope["answers"]

    body, headers = signed()
    reply = handler(body, headers)
    assert reply.status == 200 and reply.after is not None
    reply.after()
    assert answers.posted == [RESOLVED]

    event = json.dumps({"conversionId": 991}).encode()
    t = int(time.time())
    v1 = hmac.new(SECRET.encode(), f"{t}.".encode() + event, hashlib.sha256).hexdigest()
    event_headers = {
        "X-Siren-Event": "conversion.approved",
        "X-Siren-Delivery": "dlv_1",
        "X-Siren-Timestamp": str(t),
        "X-Siren-Signature-V2": f"t={t},v1={v1}",
        "X-Siren-Signature": "sha256=" + hmac.new(SECRET.encode(), event, hashlib.sha256).hexdigest(),
    }
    assert handler(event, event_headers).status == 200
    assert seen == ["conversion.approved"]


def test_options_snippet_runs_asks_on_the_pool_and_reports_refusals(siren_api, caplog):
    refusal = {"error": {"code": "app_answer_invalid", "message": "shares has no amount for metric 43"}}
    answers = Answers(siren_api, status=422, body=refusal)
    scope = build(snippet("options"))
    handler = scope["answers"]
    handler.on(ASK, "split-by-score", lambda request: RESOLVED)

    body, headers = signed()
    with caplog.at_level("WARNING", logger="my-app"):
        reply = handler(body, headers)
        assert reply.status == 200 and reply.after is None, "run_later took the work"
        scope["workers"].shutdown(wait=True)

    assert answers.posted == [RESOLVED]
    assert len(siren_api.calls) == 1, "a refusal is never sent again"
    [record] = caplog.records
    assert CONTRACTS["requestKey"] in record.getMessage() and "metric 43" in record.getMessage()


def test_split_pool_snippet_prints_what_its_comments_say(capsys):
    code = snippet("split-pool")
    with siren.Siren(api_key=APP_KEY) as client:
        exec(compile(code, "README.md", "exec"), {"client": client})
    printed = capsys.readouterr().out.splitlines()
    said = [line.split("# ", 1)[1] for line in code.splitlines() if line.startswith("print(")]
    assert printed == said


def test_by_hand_snippet_checks_and_answers_once():
    body, headers = signed("payoutMethod")
    scope: Dict[str, Any] = {"APP_API_KEY": APP_KEY, "APP_SIGNING_SECRET": SECRET}
    with respx.mock(base_url="https://api.sirenaffiliates.com/siren/v1", assert_all_called=True) as api:
        route = api.post(ANSWER_PATH).mock(return_value=httpx.Response(200, json={"status": "accepted", "duplicate": True}))
        exec(compile(snippet("by-hand"), "README.md", "exec"), scope)
        scope["siren_request"](body, headers)

    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == {"outcome": "paid", "reference": "TX-1"}
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {APP_KEY}"
    with pytest.raises(siren.SignatureVerificationError):
        scope["siren_request"](body + b" ", headers)


def test_try_it_snippet_runs_as_a_pytest_file(siren_api):
    """The README's "Try it without Siren" tests, run against the first
    sample with the distribution resolver's function added, as the README
    says. ``simulate()`` posts nothing, so the fake Siren sees no call."""
    app = build(resolver=False)
    function = re.search(r"<!-- readme-test: fn-distributionResolver -->\n```python\n(.*?)```", README, re.S)
    assert function
    exec(compile(function.group(1), "README.md", "exec"), app)
    my_app = types.ModuleType("my_app")
    my_app.answers = app["answers"]  # type: ignore[attr-defined]
    sys.modules["my_app"] = my_app
    try:
        tests: Dict[str, Any] = {"__name__": "test_my_app"}
        exec(compile(snippet("try-it"), "README.md (try-it)", "exec"), tests)
    finally:
        del sys.modules["my_app"]
    ran = [name for name in tests if name.startswith("test_")]
    assert ran == ["test_tells_the_crm", "test_splits_by_score"]
    for name in ran:
        tests[name]()
    assert len(siren_api.calls) == 0
