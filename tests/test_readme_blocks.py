"""Every Python block in README.md runs in a test, and the handler samples
pass ``mypy --strict`` with the annotations the README asks for.

Each block carries a ``<!-- readme-test: name -->`` marker. A block is either
run here, or named in COVERED with the test file that runs it. A block with no
marker, or one in neither list, fails the test, so a new sample can't go
unrun. The pass 3 re-check (review/sdk-dx/pass-3-review.md in the run folder)
found a typed sample that read a field Siren never sends while its test only
imported it. These tests close that gap.

Blocks that call Siren run against respx, which answers every route the block
calls and refuses any other, so nothing leaves the machine.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable, Dict, List

import httpx
import pytest
import respx

import siren
from siren import testing

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
BASE_URL = "https://api.sirenaffiliates.com/siren/v1"
SECRET = "ab" * 32
KINDS = json.loads((Path(__file__).parent / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())["kinds"]

BLOCK = re.compile(r"(?:<!-- readme-test: ([\w-]+) -->\n)?```python\n(.*?)```", re.S)


def blocks() -> List[tuple]:
    return [(found.group(1), found.group(2)) for found in BLOCK.finditer(README)]


def block(name: str) -> str:
    for found, source in blocks():
        if found == name:
            return source
    raise AssertionError(f"README has no Python block named {name}")


# Blocks another test file runs, with the file that runs it.
COVERED: Dict[str, str] = {
    "handler-flask": "test_readme_handler.py",
    "try-it": "test_readme_handler.py",
    "handler-django": "test_readme_handler.py",
    "handler-asgi": "test_readme_handler.py",
    "handler-wsgi": "test_readme_handler.py",
    "handler-call": "test_readme_handler.py",
    "register": "test_readme_handler.py",
    "options": "test_readme_handler.py",
    "split-pool": "test_readme_handler.py",
    "by-hand": "test_readme_handler.py",
    "features": "test_readme_handler.py",
    "server-send": "test_readme.py",
    "handler": "test_readme.py",
    # Each kind's function runs in the README's Flask app against Siren's
    # request for its kind (snippet(f"fn-{kind}")), and is type-checked below.
    **{f"fn-{kind}": "test_answer_handler_samples.py" for kind in KINDS},
}


def test_every_python_block_has_a_name():
    unnamed = [source.splitlines()[0] for name, source in blocks() if name is None]
    assert unnamed == [], f"Name these README blocks with <!-- readme-test: name -->: {unnamed}"


def test_every_python_block_is_run_by_a_test():
    names = {name for name, _ in blocks() if name is not None}
    unrun = names - set(COVERED) - set(RUN)
    assert unrun == set(), f"No test runs these README blocks: {sorted(unrun)}"
    assert set(COVERED) | set(RUN) <= names, "COVERED or RUN names a block the README no longer has"
    assert set(COVERED).isdisjoint(RUN)


@pytest.mark.parametrize("name", sorted(COVERED))
def test_the_covering_file_runs_the_block(name):
    text = (Path(__file__).parent / COVERED[name]).read_text()
    if name.startswith("fn-"):
        assert 'snippet(f"fn-{kind}")' in text
    else:
        assert f'"{name}"' in text, f"{COVERED[name]} doesn't name the {name} block"


# Blocks run here.


def _run(name: str, scope: Dict[str, Any]) -> str:
    """Runs the block in scope and returns what it printed."""
    printed = io.StringIO()
    with redirect_stdout(printed):
        exec(compile(block(name), f"README.md ({name})", "exec"), scope)
    return printed.getvalue()


def _handler_scope() -> Dict[str, Any]:
    client = siren.Siren(api_key="sk_test")
    return {"siren": siren, "client": client, "answers": client.apps.request_handler(SECRET)}


def run_typed() -> None:
    scope = _handler_scope()
    _run("typed", scope)
    answers = scope["answers"]
    result = answers.simulate(testing.app_request("distributionResolver", "split-by-score", secret=SECRET))
    assert result.error is None, result.error
    assert result.answer == KINDS["distributionResolver"]["answer"]
    # The sample answers later when there is nothing to share.
    data = dict(KINDS["distributionResolver"]["request"]["data"], metrics=[])
    later = answers.simulate(testing.app_request("distributionResolver", "split-by-score", secret=SECRET, data=data))
    assert later.error is None and later.answer is None and later.status == 200


ROW = {
    "id": "apr_" + "b3" * 16,
    "appId": 3,
    "feature": "app:12",
    "kind": "distributionResolver",
    "mode": "ask",
    "sendCount": 3,
    "lastError": "The app didn't answer.",
    "status": "needs_attention",
}


def run_requests(api: respx.MockRouter) -> None:
    listed = api.get("/apps/requests").respond(200, json={"requests": [ROW]})
    full = dict(ROW, data={}, answer=None, answeredByUser=None)
    read = api.get(f"/apps/requests/{ROW['id']}").respond(200, json=full)
    resent = api.post(f"/apps/requests/{ROW['id']}/resend").respond(200, json=ROW)
    printed = _run("requests", {"siren": siren, "ADMIN_API_KEY": "sk_live_" + "a" * 64})
    assert listed.called and read.called and resent.call_count == 1
    assert listed.calls.last.request.url.params["status"] == "needs_attention"
    assert ROW["id"] in printed


def run_record_sale(api: respx.MockRouter) -> None:
    sale = api.post("/event/sale").respond(200, json={"status": "created"}, headers={"X-Siren-OID": "4021"})
    printed = _run("record-sale", {})
    assert json.loads(sale.calls.last.request.content)["externalId"] == "cs_test_a1b2c3"
    assert printed.splitlines()[:2] == ["4021", "created"]


def run_sale_items(api: respx.MockRouter) -> None:
    sale = api.post("/event/sale").respond(200, json={"status": "created"})
    _run("sale-items", {"client": siren.Siren(api_key="sk_test")})
    assert len(json.loads(sale.calls.last.request.content)["items"]) == 2


def run_refund_visit(api: respx.MockRouter) -> None:
    refund = api.post("/event/refund").respond(200, json={"status": "recorded"})
    visit = api.post("/event/site-visited").respond(200, json={"status": "recorded"})
    _run("refund-visit", {"client": siren.Siren(api_key="sk_test")})
    assert refund.called and visit.called


def _signed(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def run_webhook_flask(api: respx.MockRouter) -> None:
    seen: List[Any] = []
    scope: Dict[str, Any] = {"handle_conversion": seen.append, "__name__": "readme_webhook"}
    _run("webhook-flask", scope)
    body = json.dumps({"conversionId": 991}).encode()
    web = scope["app"].test_client()
    headers = {"X-Siren-Signature": _signed(body, "whsec_..."), "X-Siren-Event": "conversion.approved", "X-Siren-Delivery": "evt_1"}
    assert web.post("/webhooks/siren", data=body, headers=headers).status_code == 204
    assert seen == [{"conversionId": 991}]
    headers["X-Siren-Signature"] = _signed(body, "whsec_other")
    assert web.post("/webhooks/siren", data=body, headers=headers).status_code == 400


def run_verify_signature(api: respx.MockRouter) -> None:
    body = b'{"conversionId": 991}'
    scope = {"client": siren.Siren(api_key="sk_test"), "raw_body": body, "secret": "whsec_x", "signature_header": _signed(body, "whsec_x")}
    _run("verify-signature", scope)


def run_subscriptions(api: respx.MockRouter) -> None:
    made = api.post("/webhooks").respond(201, json={"id": 6, "targetUrl": "https://example.com/webhooks/siren", "events": ["conversion.approved", "payout.paid"], "signingSecret": "whsec_only_once"})
    listed = api.get("/webhooks").respond(200, json=[])
    removed = api.delete("/webhooks/6").respond(200, json={"success": True})
    printed = _run("subscriptions", {"siren": siren, "client": siren.Siren(api_key="sk_test")})
    assert printed.strip() == "whsec_only_once"
    assert made.called and listed.called and removed.called


def run_store_scope(api: respx.MockRouter) -> None:
    listed = api.get("/conversions").respond(200, json={"items": [], "total": 0, "page": 1, "perPage": 50, "totalPages": 0})
    _run("store-scope", {"client": siren.Siren(api_key="sk_test")})
    assert listed.calls.last.request.url.params["propertyId"] == "5"


def run_api_keys(api: respx.MockRouter) -> None:
    made = api.post("/api-keys").respond(201, json={"id": 2, "keyPrefix": "sk_live_bbbb", "label": "Accounting export", "rawKey": "sk_live_x"})
    listed = api.get("/api-keys").respond(200, json=[])
    revoked = api.delete("/api-keys/2").respond(200)
    printed = _run("api-keys", {"client": siren.Siren(api_key="sk_test")})
    assert printed.strip() == "sk_live_x"
    assert made.called and listed.called and revoked.called


def run_reconciliation(api: respx.MockRouter) -> None:
    api.get("/conversions").respond(200, json={"items": [{"id": 1}], "total": 51, "page": 1, "perPage": 50, "totalPages": 2})
    for path in ("/transactions", "/obligations", "/payouts"):
        api.get(path).respond(200, json={"items": []})
    printed = _run("reconciliation", {"client": siren.Siren(api_key="sk_test")})
    assert printed.splitlines() == ["1", "51 2"]


def run_errors(api: respx.MockRouter) -> None:
    api.post("/event/refund").respond(404, json={"error": {"message": "No sale matches."}})
    printed = _run("errors", {"siren": siren, "client": siren.Siren(api_key="sk_test")})
    assert printed.strip() == "no matching sale"


def run_configuration(api: respx.MockRouter) -> None:
    scope: Dict[str, Any] = {"siren": siren}
    _run("configuration", scope)
    assert isinstance(scope["client"], siren.Siren)


class _Tokens:
    """Stands in for the browser token handler, which tests/test_readme.py
    runs through the "handler" block. These blocks only mount it."""

    def wsgi(self, environ, start_response):  # pragma: no cover - mounted, not called
        raise AssertionError

    async def asgi(self, scope, receive, send):  # pragma: no cover - mounted, not called
        raise AssertionError

    def __call__(self, body, query):
        return 200, {"token": "brw.fake", "event": query.get("event")}


def run_token_asgi(api: respx.MockRouter) -> None:
    routes: List[tuple] = []
    app = types.SimpleNamespace(add_route=lambda path, route, methods=None: routes.append((path, route, methods)))
    tokens = _Tokens()
    _run("token-asgi", {"app": app, "tokens": tokens})
    assert routes == [("/siren/token", tokens.asgi, ["GET", "POST"])]


def run_token_flask(api: respx.MockRouter) -> None:
    from flask import Flask

    app = Flask("readme_tokens")
    tokens = _Tokens()
    _run("token-flask", {"app": app, "tokens": tokens})
    assert app.wsgi_app.mounts == {"/siren/token": tokens.wsgi}


def run_token_django(api: respx.MockRouter) -> None:
    pytest.importorskip("django")
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(DEBUG=False, ALLOWED_HOSTS=["*"], DEFAULT_CHARSET="utf-8", USE_TZ=True)
        django.setup()
    from django.test import RequestFactory

    scope: Dict[str, Any] = {"tokens": _Tokens()}
    _run("token-django", scope)
    response = scope["siren_token"](RequestFactory().get("/siren/token", {"event": "signup-completed"}))
    assert response.status_code == 200
    assert json.loads(response.content) == {"token": "brw.fake", "event": "signup-completed"}


def run_on_event_test(api: respx.MockRouter) -> None:
    scope: Dict[str, Any] = {}
    _run("on-event-test", scope)
    assert scope["approved"] == [testing.sample_event_data("conversion.approved")["conversionId"]]


STORED = "apr_9f2c4e0b7a1d4c3e8b6f5a2d1c0e9b8a"


def run_replay(api: respx.MockRouter) -> None:
    row = dict(ROW, id=STORED, data=KINDS["distributionResolver"]["request"]["data"], answer=None, answeredByUser=None)
    read = api.get(f"/apps/requests/{STORED}").respond(200, json=row)
    scope = _handler_scope()
    _run("typed", scope)
    printed = _run("replay", scope)
    assert read.called
    assert printed.strip() == f"{KINDS['distributionResolver']['answer']} None"


def run_debug_lines(api: respx.MockRouter) -> None:
    import logging

    before = logging.getLogger("siren").level
    try:
        _run("debug-lines", {})
        assert logging.getLogger("siren").isEnabledFor(logging.DEBUG)
    finally:
        logging.getLogger("siren").setLevel(before)


RUN: Dict[str, Callable[..., None]] = {
    "typed": run_typed,
    "requests": run_requests,
    "record-sale": run_record_sale,
    "sale-items": run_sale_items,
    "refund-visit": run_refund_visit,
    "webhook-flask": run_webhook_flask,
    "verify-signature": run_verify_signature,
    "subscriptions": run_subscriptions,
    "store-scope": run_store_scope,
    "api-keys": run_api_keys,
    "reconciliation": run_reconciliation,
    "errors": run_errors,
    "configuration": run_configuration,
    "token-asgi": run_token_asgi,
    "token-flask": run_token_flask,
    "token-django": run_token_django,
    "on-event-test": run_on_event_test,
    "replay": run_replay,
    "debug-lines": run_debug_lines,
}


@pytest.mark.parametrize("name", sorted(RUN))
def test_the_block_runs(name, monkeypatch):
    monkeypatch.setenv("SIREN_API_KEY", "sk_live_" + "a" * 64)
    monkeypatch.setenv("SIREN_SIGNING_SECRET", SECRET)
    runner = RUN[name]
    if name == "typed":
        runner()
        return
    with respx.mock(base_url=BASE_URL, assert_all_called=False, assert_all_mocked=True) as api:
        runner(api)


# The handler samples under mypy --strict, annotated as the README asks.

ANSWER = {
    "distributionResolver": "DistributionResolverAnswer",
    "incentiveResolver": "IncentiveResolverAnswer",
    "incentiveType": "IncentiveTypeAnswer",
    "metricCalculation": "MetricCalculationAnswer",
    "metricFilter": "MetricFilterAnswer",
    "transactionFilter": "TransactionFilterAnswer",
    "engagementCalculation": "EngagementCalculationAnswer",
    "engagementFilter": "EngagementFilterAnswer",
    "programGroupSorter": "ProgramGroupSorterAnswer",
    "collaboratorGroupStructure": "CollaboratorGroupStructureAnswer",
    "payoutMethod": "PayoutMethodAnswer",
}
TELL = {"postConversionAction": "PostConversionActionTell", "postAllocationAction": "PostAllocationActionTell"}

PREAMBLE = '''"""Generated by tests/test_readme_blocks.py from README.md."""
from typing import Any, Dict, Union

import siren
from siren import *  # noqa: F403 - the README's types, by the names it uses
from siren import AppAnswerLater, AppAsk, AppTell

client = siren.Siren(api_key="sk_test")
answers = client.apps.request_handler("{secret}")


def add_to_crm(*args: object) -> None:
    pass


def note_share(*args: object) -> None:
    pass


def is_refunded(transaction_id: int) -> bool:
    return False


def is_staff(*args: object) -> bool:
    return False


def pay_once(*args: object) -> Dict[str, Any]:
    return {"ok": True, "id": "TX-1"}

'''


def _annotated(kind: str) -> str:
    """The kind's README function, annotated the way the typed sample is."""
    source = block(f"fn-{kind}")
    if kind in TELL:
        hint = f"(request: AppTell[{TELL[kind]}]) -> None:"
    else:
        question = ANSWER[kind].replace("Answer", "Question")
        hint = f"(request: AppAsk[{question}]) -> Union[{ANSWER[kind]}, AppAnswerLater]:"
    annotated, count = re.subn(r"\(request\):", hint, source, count=1)
    assert count == 1, f"fn-{kind} has no def ...(request):"
    return annotated


@pytest.fixture(scope="module")
def mypy_errors(tmp_path_factory) -> Dict[str, List[str]]:
    pytest.importorskip("mypy")
    work = tmp_path_factory.mktemp("readme_mypy")
    files = {"readme_typed.py": block("typed")}
    files.update({f"readme_fn_{kind}.py": _annotated(kind) for kind in KINDS})
    preamble = PREAMBLE.replace("{secret}", SECRET)
    for name, source in files.items():
        (work / name).write_text(preamble + source)
    run = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental", "--cache-dir", str(work / ".cache"), "--no-color-output",
         "--hide-error-context", "--show-error-codes", "--disable-error-code", "no-redef", *sorted(files)],
        cwd=work,
        env={**os.environ, "MYPYPATH": str(Path(siren.__file__).resolve().parent.parent)},
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode in (0, 1), run.stdout + run.stderr
    errors: Dict[str, List[str]] = {name: [] for name in files}
    for line in run.stdout.splitlines():
        found = re.match(r"^([\w.]+):(\d+): error: (.*)$", line)
        if found:
            errors.setdefault(found.group(1), []).append(f"{found.group(2)}: {found.group(3)}")
    return errors


@pytest.mark.parametrize("name", ["readme_typed.py"] + [f"readme_fn_{kind}.py" for kind in sorted(KINDS)])
def test_the_handler_sample_passes_strict_mypy(mypy_errors, name):
    assert mypy_errors[name] == []


# The answer types take any mapping for shares, rewards and parents, so a
# split_pool() result fits under strict mypy. The SDK must then write any
# mapping as JSON, not only a dict.


def test_an_answer_whose_rewards_are_a_read_only_mapping_simulates_and_posts(monkeypatch):
    from types import MappingProxyType

    question = KINDS["incentiveResolver"]["request"]["data"]
    rewards = MappingProxyType({str(c["conversionId"]): 0 for c in question["conversions"]})
    client = siren.Siren(api_key="sk_test")
    answers = client.apps.request_handler(SECRET)
    answers.on("incentiveResolver", "k", lambda request: {"outcome": "rewards", "units": question["pool"]["units"], "rewards": rewards})
    result = answers.simulate(testing.app_request("incentiveResolver", "k", secret=SECRET))
    assert result.error is None, result.error
    assert result.answer["rewards"] == dict(rewards)

    with respx.mock(base_url=BASE_URL, assert_all_mocked=True) as api:
        posted = api.post(f"/apps/requests/{ROW['id']}/answer").respond(200, json={"status": "accepted"})
        client.apps.requests.answer(ROW["id"], {"outcome": "rewards", "units": "USD", "rewards": rewards})
    assert json.loads(posted.calls.last.request.content)["rewards"] == dict(rewards)
