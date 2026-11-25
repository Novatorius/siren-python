"""DX pass 4 (review/sdk-dx/pass-4-review.md and pass-4-builder-spec.md in the
run folder): errors, testing and local development.

- The handler takes a ``logger``, writes one debug line per request, and
  keeps each failure to one short line, with a traceback only when the app's
  own function raised.
- A refused signature reaches ``on_error`` and the log, naming what to check,
  five lines a minute at most and then a count.
- A tell slower than Siren's 8 seconds reaches ``on_error`` and
  ``simulate()`` as ``AppSlowTellError``.
- ``siren.testing`` signs webhook events with Siren's sample payloads, and
  turns a stored request back into a test input.
- After a 422 ``app_answer_invalid`` the line says to reproduce the answer.
- A test request carries ``test``, false when absent.

Time comes from an injected clock, never a real wait.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, List

import pytest

import siren
from siren import SignatureVerificationError, testing
from siren.testing import AppTestRequest

from .conftest import API_KEY

ROOT = Path(__file__).resolve().parent.parent
SECRET = "cd" * 32
REQUEST_ID = "apr_" + "b339" * 8
ANSWER_PATH = f"/apps/requests/{REQUEST_ID}/answer"
GOOD = {"outcome": "allocated", "units": "USD", "shares": {"41": 6000, "43": 4000}}
DEBUG_LINE = re.compile(r"^Siren request handler(?: \((.*)\))?: (\d{3}) in (\d+) ms$")
MISMATCH_HINT = "Check SIREN_SIGNING_SECRET: it must be this app's signing secret, and it changes when the secret is rotated."
PROXY_HINT = "Check that a proxy passes Siren's X-Siren-* headers through."


class Clock:
    """Stands in for the handler's clock, in seconds. ``advance`` moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr("siren.app_requests._clock", fake)
    return fake


@pytest.fixture
def client(mocked_api):
    with siren.Siren(api_key=API_KEY) as instance:
        yield instance


def lines(caplog, name="siren"):
    return [(record.levelname, record.getMessage()) for record in caplog.records if record.name == name]


def run(answers, request):
    reply = answers(request.raw_body, request.headers)
    if reply.after is not None:
        reply.after()
    return reply


def tell(**options):
    return testing.app_request("postConversionAction", "tell-the-crm", secret=SECRET, id=REQUEST_ID, **options)


def ask(**options):
    return testing.app_request("distributionResolver", "split-by-score", secret=SECRET, id=REQUEST_ID, **options)


# The logger option and its levels -------------------------------------------


def test_the_handler_takes_a_logger(client):
    mine = logging.getLogger("my-app.siren")
    assert client.apps.request_handler(SECRET, logger=mine) is not None
    adapter = logging.LoggerAdapter(mine, {})
    assert client.apps.request_handler(SECRET, logger=adapter) is not None


@pytest.mark.parametrize("given", ["siren", print, object()])
def test_a_logger_that_is_not_one_is_refused_when_the_handler_is_made(client, given):
    with pytest.raises(siren.AppHandlerError, match="takes logger as a logging.Logger"):
        client.apps.request_handler(SECRET, logger=given)


def test_each_request_handled_writes_one_debug_line(client, clock, caplog):
    mine = logging.getLogger("my-app.siren")
    answers = client.apps.request_handler(SECRET, logger=mine)

    @answers.on("postConversionAction", "tell-the-crm")
    def crm(request):
        clock.advance(0.042)

    with caplog.at_level("DEBUG", logger="my-app.siren"):
        assert run(answers, tell()).status == 200

    assert lines(caplog, "my-app.siren") == [
        ("DEBUG", f"Siren request handler (postConversionAction tell-the-crm {REQUEST_ID}): 200 in 42 ms"),
    ]
    assert lines(caplog) == []


def test_the_debug_line_names_an_event_by_its_type_and_delivery(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    answers.on_event("conversion.approved", lambda event: None)
    event = testing.webhook_event("conversion.approved", secret=SECRET, delivery_id="evt_" + "a1" * 16)
    with caplog.at_level("DEBUG", logger="siren"):
        assert run(answers, event).status == 200

    assert lines(caplog) == [("DEBUG", f"Siren request handler (conversion.approved evt_{'a1' * 16}): 200 in 0 ms")]


def test_a_refused_request_gets_a_debug_line_without_a_name(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    with caplog.at_level("DEBUG", logger="siren"):
        assert answers(b"{}", {}, method="GET").status == 405

    assert lines(caplog) == [("DEBUG", "Siren request handler: 405 in 0 ms")]


def test_the_debug_line_goes_out_with_an_on_error_of_the_apps_own(client, clock, caplog):
    seen: List[Any] = []
    answers = client.apps.request_handler(SECRET, on_error=lambda error, request: seen.append(error))
    answers.on("postConversionAction", "tell-the-crm", lambda request: 1 / 0)
    with caplog.at_level("DEBUG", logger="siren"):
        assert run(answers, tell()).status == 500

    assert [type(error) for error in seen] == [ZeroDivisionError]
    assert lines(caplog) == [("DEBUG", f"Siren request handler (postConversionAction tell-the-crm {REQUEST_ID}): 500 in 0 ms")]


def test_simulate_writes_no_debug_line(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    answers.on("postConversionAction", "tell-the-crm", lambda request: None)
    with caplog.at_level("DEBUG", logger="siren"):
        assert answers.simulate(tell()).status == 200
    assert lines(caplog) == []


def test_the_asgi_face_writes_the_debug_line_too(client, clock, caplog):
    import asyncio

    answers = client.apps.request_handler(SECRET)
    answers.on("postConversionAction", "tell-the-crm", lambda request: None)
    request = tell()
    sent: List[Any] = []

    async def receive():
        return {"type": "http.request", "body": request.raw_body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "headers": [(k.lower().encode(), v.encode()) for k, v in request.headers.items()]}
    with caplog.at_level("DEBUG", logger="siren"):
        asyncio.run(answers.asgi(scope, receive, send))

    assert sent[0]["status"] == 200
    assert lines(caplog) == [("DEBUG", f"Siren request handler (postConversionAction tell-the-crm {REQUEST_ID}): 200 in 0 ms")]


def test_a_401_on_the_answer_post_writes_at_most_two_lines_and_no_traceback(client, mocked_api, caplog):
    mocked_api.post(ANSWER_PATH).respond(401, json={"error": {"message": "Unauthenticated."}})
    answers = client.apps.request_handler(SECRET)
    answers.on("distributionResolver", "split-by-score", lambda request: GOOD)
    with caplog.at_level("DEBUG", logger="siren"):
        run(answers, ask())

    records = [record for record in caplog.records if record.name == "siren"]
    assert 1 <= len(records) <= 2
    written = "\n".join(logging.Formatter().format(record) for record in records)
    assert len(written.splitlines()) <= 2, written
    assert "Traceback" not in written
    [failure] = [record for record in records if record.levelno == logging.ERROR]
    assert failure.exc_info is None
    assert "(app_answer_unauthorized)" in failure.getMessage()
    assert "(cause: Unauthenticated)" in failure.getMessage()


def test_the_apps_own_function_keeps_its_traceback(client, caplog):
    answers = client.apps.request_handler(SECRET)

    def broken(request):
        raise RuntimeError("crm down")

    answers.on("postConversionAction", "tell-the-crm", broken)
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, tell())
    [record] = [record for record in caplog.records if record.name == "siren"]
    assert record.exc_info is not None


def test_a_logger_that_raises_takes_neither_the_reply_nor_the_process(client):
    class Broken(logging.Logger):
        def handle(self, record):
            raise RuntimeError("log disk full")

    broken = Broken("broken")
    broken.setLevel(logging.DEBUG)
    answers = client.apps.request_handler(SECRET, logger=broken)
    answers.on("postConversionAction", "tell-the-crm", lambda request: 1 / 0)
    assert run(answers, tell()).status == 500


# Finding 1: a refused signature reaches on_error and the log -----------------


def resigned(request: AppTestRequest, **headers: Any) -> AppTestRequest:
    merged = dict(request.headers)
    for name, value in headers.items():
        name = name.replace("_", "-")
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
    return AppTestRequest(request.method, merged, request.raw_body)


def wrong_secret():
    return testing.app_request("postConversionAction", "tell-the-crm", secret="ab" * 32, id=REQUEST_ID)


REFUSALS = {
    "signature_mismatch": lambda: wrong_secret(),
    "signature_missing": lambda: resigned(tell(), **{"X-Siren-Signature-V2": None}),
    "signature_malformed": lambda: resigned(tell(), **{"X-Siren-Signature-V2": "sha256=abc"}),
    "timestamp_mismatch": lambda: resigned(tell(), **{"X-Siren-Timestamp": "12"}),
    "timestamp_out_of_tolerance": lambda: tell(timestamp=1_000_000),
}


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_a_refused_signature_reaches_on_error_with_its_code(client, code):
    seen: List[Any] = []
    answers = client.apps.request_handler(SECRET, on_error=lambda error, request: seen.append((error, request)))
    answers.on("postConversionAction", "tell-the-crm", lambda request: None)
    reply = run(answers, REFUSALS[code]())

    assert reply.status == 401
    [(error, request)] = seen
    assert isinstance(error, SignatureVerificationError) and isinstance(error, siren.SirenError)
    assert error.code == code
    assert request is None
    assert reply.body == {"error": error.message}


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_verify_request_raises_the_same_codes(client, code):
    request = REFUSALS[code]()
    with pytest.raises(SignatureVerificationError) as raised:
        client.apps.verify_request(request.raw_body, request.headers, SECRET)
    assert raised.value.code == code


def test_a_wrong_secret_warns_and_names_the_env_var(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    with caplog.at_level("WARNING", logger="siren"):
        assert run(answers, wrong_secret()).status == 401

    assert lines(caplog) == [
        (
            "WARNING",
            "Siren request handler: refused a request with 401. The request signature does not match. "
            "Pass the raw body exactly as received and the signing secret of this app. " + MISMATCH_HINT,
        )
    ]


@pytest.mark.parametrize("code", ["signature_missing", "signature_malformed", "timestamp_mismatch"])
def test_a_header_a_proxy_could_have_dropped_says_to_check_the_proxy(client, clock, caplog, code):
    answers = client.apps.request_handler(SECRET)
    with caplog.at_level("WARNING", logger="siren"):
        run(answers, REFUSALS[code]())
    [(level, line)] = lines(caplog)
    assert level == "WARNING" and line.startswith("Siren request handler: refused a request with 401. ")
    assert line.endswith(PROXY_HINT)


def test_a_stale_time_says_to_check_the_clock(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    with caplog.at_level("WARNING", logger="siren"):
        run(answers, REFUSALS["timestamp_out_of_tolerance"]())
    [(level, line)] = lines(caplog)
    assert "more than 300 seconds from this machine's clock" in line and "Check the clock" in line
    assert PROXY_HINT not in line and MISMATCH_HINT not in line


def test_a_refused_webhook_event_is_reported_too(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    answers.on_event("conversion.approved", lambda event: None)
    event = testing.webhook_event("conversion.approved", secret="ab" * 32)
    with caplog.at_level("WARNING", logger="siren"):
        assert run(answers, event).status == 401
    [(level, line)] = lines(caplog)
    assert line.endswith(MISMATCH_HINT)


def test_after_five_refusals_a_minute_the_rest_are_counted(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    with caplog.at_level("WARNING", logger="siren"):
        for _ in range(8):
            run(answers, wrong_secret())
            clock.advance(5)
        assert len(lines(caplog)) == 5
        clock.advance(30)  # 70 seconds after the first
        run(answers, wrong_secret())

    written = [line for _, line in lines(caplog)]
    assert len(written) == 7
    assert written[5] == (
        "3 more requests failed the signature check in the last minute. "
        "When every request fails, SIREN_SIGNING_SECRET is wrong or was rotated."
    )
    assert written[6].endswith(MISMATCH_HINT)


def test_one_counted_refusal_reads_in_the_singular(client, clock, caplog):
    answers = client.apps.request_handler(SECRET)
    with caplog.at_level("WARNING", logger="siren"):
        for _ in range(6):
            run(answers, wrong_secret())
        clock.advance(61)
        run(answers, wrong_secret())
    assert lines(caplog)[5][1].startswith("1 more request failed the signature check in the last minute.")


# Finding 3: a tell slower than Siren's 8 seconds ------------------------------


SLOW = (
    "postConversionAction tell-the-crm took 9.0 s. Siren stops waiting at 8 s and sends the tell again, "
    "so it ran twice. Save what you need, hand the slow part to run_later or your own queue, and return."
)


def slow_handler(client, clock, **options):
    answers = client.apps.request_handler(SECRET, **options)

    @answers.on("postConversionAction", "tell-the-crm")
    def crm(request):
        clock.advance(9)

    return answers


def test_the_limit_is_siren_s_eight_seconds():
    assert siren.TELL_TIME_LIMIT_SECONDS == 8
    assert issubclass(siren.AppSlowTellError, siren.SirenError)


def test_a_slow_tell_still_replies_200_and_reaches_on_error(client, clock):
    seen: List[Any] = []
    answers = slow_handler(client, clock, on_error=lambda error, request: seen.append((error, request)))
    assert run(answers, tell()).status == 200
    [(error, request)] = seen
    assert isinstance(error, siren.AppSlowTellError)
    assert str(error) == SLOW
    assert request.id == REQUEST_ID


def test_a_slow_tell_warns_by_default(client, clock, caplog):
    answers = slow_handler(client, clock)
    with caplog.at_level("WARNING", logger="siren"):
        run(answers, tell())
    assert lines(caplog) == [("WARNING", f"Siren request handler (postConversionAction tell-the-crm {REQUEST_ID}): {SLOW}")]


def test_simulate_returns_the_slow_tell_as_its_error(client, clock):
    result = slow_handler(client, clock).simulate(tell())
    assert result.status == 200
    assert isinstance(result.error, siren.AppSlowTellError) and str(result.error) == SLOW


def test_a_tell_within_the_limit_is_no_error(client, clock):
    answers = client.apps.request_handler(SECRET)
    answers.on("postConversionAction", "tell-the-crm", lambda request: clock.advance(7.9))
    assert answers.simulate(tell()).error is None


def test_a_slow_tell_under_asgi_reaches_on_error(client, clock):
    import asyncio

    seen: List[Any] = []
    answers = slow_handler(client, clock, on_error=lambda error, request: seen.append(error))
    request = tell()
    sent: List[Any] = []

    async def receive():
        return {"type": "http.request", "body": request.raw_body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "headers": [(k.lower().encode(), v.encode()) for k, v in request.headers.items()]}
    asyncio.run(answers.asgi(scope, receive, send))
    assert sent[0]["status"] == 200
    assert [type(error) for error in seen] == [siren.AppSlowTellError]


# Finding 4: signed webhook events ---------------------------------------------


def test_webhook_event_signs_the_event_as_siren_signs_one_to_an_app(monkeypatch):
    event = testing.webhook_event("conversion.approved", secret=SECRET, timestamp=1_700_000_000)
    body = event.raw_body
    assert json.loads(body) == testing.sample_event_data("conversion.approved")
    headers = event.headers
    assert headers["X-Siren-Event"] == "conversion.approved"
    assert re.fullmatch(r"evt_[0-9a-f]{32}", headers["X-Siren-Delivery"])
    assert headers["X-Siren-Timestamp"] == "1700000000"
    v1 = hmac.new(SECRET.encode(), b"1700000000." + body, hashlib.sha256).hexdigest()
    assert headers["X-Siren-Signature-V2"] == f"t=1700000000,v1={v1}"
    assert headers["X-Siren-Signature"] == "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert "Idempotency-Key" not in headers

    monkeypatch.setenv("SIREN_SIGNING_SECRET", SECRET)
    mine = testing.webhook_event("payout.paid", {"payoutId": 7}, delivery_id="evt_" + "0" * 32)
    assert json.loads(mine.raw_body) == {"payoutId": 7}
    assert mine.headers["X-Siren-Delivery"] == "evt_" + "0" * 32


def test_simulate_runs_an_on_event_function_on_a_signed_event(client):
    answers = client.apps.request_handler(SECRET)
    approved: List[Any] = []
    answers.on_event("conversion.approved", lambda event: approved.append(event.data["conversionId"]))
    result = answers.simulate(testing.webhook_event("conversion.approved", secret=SECRET))
    assert (result.status, result.error) == (200, None)
    assert approved == [testing.sample_event_data("conversion.approved")["conversionId"]]


@pytest.mark.parametrize("given", ["", "conversion", "Conversion.Approved", "app.request.x", 7])
def test_webhook_event_refuses_a_type_that_is_not_one(given):
    with pytest.raises(ValueError, match="takes an event type such as conversion.approved"):
        testing.webhook_event(given, secret=SECRET)


def test_sample_event_data_is_a_fresh_copy_for_each_documented_event():
    first = testing.sample_event_data("conversion.approved")
    first["conversionId"] = -1
    assert testing.sample_event_data("conversion.approved")["conversionId"] != -1
    assert len(testing.EVENT_TYPES) == 60


@pytest.mark.parametrize("event", ["credit.issued", "credit.redeemed", "currency.created", "currency.deleted", "nothing.here"])
def test_an_event_siren_documents_no_payload_for_needs_its_data(event):
    with pytest.raises(ValueError, match=r"Pass the data to webhook_event\(type, data\)"):
        testing.sample_event_data(event)
    assert testing.webhook_event(event, {"id": 1}, secret=SECRET).body == {"id": 1}


def test_the_event_samples_name_their_source():
    shipped = json.loads((ROOT / "src" / "siren" / "_event_samples.json").read_text())
    assert "docs/api/webhooks-events.md" in shipped["source"]
    assert shipped["events"]["sale.created"] == {"opportunityId": 101, "attributed": True, "source": "stripe", "bindingId": "example"}


@pytest.mark.skipif(not os.environ.get("SIREN_CHECKOUT"), reason="set SIREN_CHECKOUT to a Siren checkout to compare")
def test_the_event_samples_match_siren_s_event_reference():
    run = subprocess.run(
        [sys.executable, "scripts/siren_event_samples.py", os.environ["SIREN_CHECKOUT"], "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr


# The replay helper --------------------------------------------------------------

ROW = {
    "id": REQUEST_ID,
    "appId": 3,
    "feature": "app:12",
    "kind": "distributionResolver",
    "mode": "ask",
    "status": "needs_attention",
    "sendCount": 3,
    "answerBy": "2026-11-20T16:58:00+00:00",
    "createdAt": "2026-11-20T15:58:00+00:00",
    "data": testing.sample_data("distributionResolver"),
    "answer": None,
}


def test_a_stored_row_needs_the_features_key():
    with pytest.raises(ValueError) as raised:
        testing.app_request_from_record(ROW, secret=SECRET)
    assert str(raised.value) == (
        "siren.testing.app_request_from_record() needs the feature's key: the record names its feature as app:12, "
        "without the key. Pass key, which `siren apps show <app>` lists beside each feature."
    )


def test_a_stored_row_becomes_the_request_siren_sent(client):
    request = testing.app_request_from_record(ROW, key="split-by-score", secret=SECRET, timestamp=1_700_000_000)
    body = request.body
    assert body["id"] == REQUEST_ID and body["mode"] == "ask"
    assert body["feature"] == {"kind": "distributionResolver", "key": "split-by-score", "id": "app:12"}
    assert body["answerBy"] == ROW["answerBy"] and body["createdAt"] == ROW["createdAt"]
    assert body["answerUrl"] == f"https://api.sirenaffiliates.com/siren/v1/apps/requests/{REQUEST_ID}/answer"
    assert body["data"] == ROW["data"]
    assert "test" not in body
    assert request.headers["X-Siren-Delivery"] == f"{REQUEST_ID}.1"
    assert client.apps.verify_request(request.raw_body, request.headers, SECRET, now=1_700_000_000).id == REQUEST_ID


def test_a_stored_row_as_json_text_and_a_raw_body_both_replay():
    text = json.dumps(ROW)
    assert testing.app_request_from_record(text, key="split-by-score", secret=SECRET).body["id"] == REQUEST_ID
    raw = ask().body
    replayed = testing.app_request_from_record(raw, secret=SECRET).body
    assert replayed["feature"] == raw["feature"] and replayed["data"] == raw["data"]


def test_a_stored_test_request_stays_a_test():
    assert testing.app_request_from_record(dict(ROW, test=True), key="split-by-score", secret=SECRET).body["test"] is True


@pytest.mark.parametrize("record", ["not json", [], {"id": REQUEST_ID, "kind": "distributionResolver"}])
def test_a_record_that_is_not_a_stored_request_is_refused(record):
    with pytest.raises(ValueError, match=r"app_request_from_record\(\)"):
        testing.app_request_from_record(record, key="split-by-score", secret=SECRET)


# The 422 line -------------------------------------------------------------------


def test_after_a_422_the_line_says_to_reproduce_the_answer(client, mocked_api, caplog):
    mocked_api.post(ANSWER_PATH).respond(
        422, json={"error": {"code": "app_answer_invalid", "message": "The shares must add up to the pool or less."}}
    )
    answers = client.apps.request_handler(SECRET)
    answers.on("distributionResolver", "split-by-score", lambda request: GOOD)
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, ask())

    [(level, line)] = lines(caplog)
    assert line == (
        f"Siren request handler (distributionResolver split-by-score {REQUEST_ID}): "
        "The shares must add up to the pool or less (app_answer_invalid). "
        "Your function's answer broke Siren's rule. Reproduce it with simulate() on this request's data, fix it, "
        f"then resend with `siren apps resend {REQUEST_ID}`."
    )


# Test requests --------------------------------------------------------------------


def test_app_request_builds_a_test_request_on_request():
    made = ask(test=True)
    assert list(made.body)[:5] == ["version", "id", "type", "mode", "test"]
    assert made.body["test"] is True
    assert "test" not in ask().body


def test_the_request_reads_test_false_when_absent(client):
    assert client.apps.verify_request(ask(test=True).raw_body, ask(test=True).headers, SECRET).test is True
    plain = ask()
    assert client.apps.verify_request(plain.raw_body, plain.headers, SECRET).test is False


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_a_test_field_that_is_not_true_or_false_is_refused(client, value):
    body = ask().body
    body["test"] = value
    request = AppTestRequest.signed(json.dumps(body).encode(), SECRET)
    answers = client.apps.request_handler(SECRET)
    answers.on("distributionResolver", "split-by-score", lambda asked: GOOD)
    reply = run(answers, request)
    assert (reply.status, reply.body) == (400, {"error": "The request's `test` must be true or false."})


def test_the_stored_row_type_names_test():
    assert "test" in siren.types.AppRequestRecord.__annotations__


# The README -----------------------------------------------------------------------

README = (ROOT / "README.md").read_text()
WORDS = " ".join(README.split())


def section(heading: str) -> str:
    start = README.index(heading)
    rest = README[start + len(heading):]
    end = re.search(r"^## ", rest, re.M)
    return rest[: end.start()] if end else rest


def test_the_readme_teaches_local_development_without_a_tunnel():
    local = section("## Develop on your machine")
    assert "sk_test_" in local and "siren apps apply" in local and ".env.sandbox" in local
    assert "siren listen --forward-to localhost:3000/siren" in local
    assert "siren trigger ask <app> <feature>" in local
    assert "next release" in local
    for tunnel in ("ngrok", "tunnel to", "cloudflared", "localtunnel"):
        assert tunnel not in README


def test_the_readme_says_once_how_to_run_the_command_line_tool_with_npx():
    assert README.count("npx @novatorius/siren") == 1
    note = README[README.index("npx @novatorius/siren") - 600 : README.index("npx @novatorius/siren") + 600]
    assert "siren apps rotate-key" in note


def test_the_readme_says_a_test_request_moves_no_money():
    assert "must never move money or write records on a test request" in WORDS
    assert "app_request(kind, key, test=True)" in README


def test_the_readme_reply_table_holds_the_tell_limit():
    assert "A tell must finish within 8 seconds" in WORDS
    assert "AppSlowTellError" in README


def test_the_readme_names_the_logger_option():
    assert "`logger` is where the default `on_error` writes" in WORDS
    assert "Siren request handler: refused a request with 401." in WORDS
