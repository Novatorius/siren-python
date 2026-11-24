"""The request handler: one address that checks what Siren sends an app,
routes it to the app's function by kind and key, replies, and posts an ask's
answer. Each test names the row of section 3.1 or the step of section 3.2 of
the run's design note (design/sdk-answer-handler.md) that it pins.

Requests are Siren's own, from tests/fixtures/apps-feature-contracts, which
Siren's AppFeatureContractsTest holds to what its builders send. They are
signed here with a test secret at the current time, the way
WebhookSignature::headers() signs them."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import pytest

import siren
from siren.app_requests import MAX_BODY_BYTES, AppRequestHandler, AppRequestReply

from .conftest import API_KEY

SECRET = "ab" * 32
FIXTURES = Path(__file__).parent / "fixtures" / "apps-feature-contracts"
CONTRACTS = json.loads((FIXTURES / "requests.json").read_text())
REQUEST_ID = CONTRACTS["requestKey"]
API_BASE = CONTRACTS["apiBase"]
ANSWER_PATH = f"/apps/requests/{REQUEST_ID}/answer"
PARSED_WORDS = "Mount the handler before any JSON body parser, or give its route the raw body."


def contract(kind: str) -> dict:
    return CONTRACTS["kinds"][kind]


def body_of(kind: str) -> str:
    return json.dumps(contract(kind)["request"])


def sign(body: str, *, event: str = "app.request", secret: str = SECRET, t: Optional[int] = None) -> Dict[str, str]:
    """The headers WebhookSignature::headers() sends, in its order."""
    t = int(time.time()) if t is None else t
    v1 = hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Siren-Signature": "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest(),
        "X-Siren-Event": event,
        "X-Siren-Delivery": f"{REQUEST_ID}.1",
        "User-Agent": "Siren-Webhooks/1.0",
        "X-Siren-Timestamp": str(t),
        "X-Siren-Signature-V2": f"t={t},v1={v1}",
        "Idempotency-Key": REQUEST_ID,
    }


@pytest.fixture
def app_client():
    """A client made with the app's key at the fixture's API address."""
    with siren.Siren(api_key=API_KEY, base_url=API_BASE) as instance:
        yield instance


@pytest.fixture
def siren_api():
    import respx

    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        yield mock


class Errors:
    """An on_error that keeps what it was handed."""

    def __init__(self) -> None:
        self.seen: List[Tuple[BaseException, Any]] = []

    def __call__(self, error: BaseException, subject: Any = None) -> None:
        self.seen.append((error, subject))


@pytest.fixture
def errors():
    return Errors()


def handler(app_client, errors, **options) -> AppRequestHandler:
    return app_client.apps.request_handler(SECRET, on_error=errors, **options)


def assert_json_reply(reply: AppRequestReply) -> None:
    headers = dict(reply.headers)
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(reply.content) == reply.body


# Section 3.1, row by row ---------------------------------------------------


class TestRow1MethodNotPost:
    @pytest.mark.parametrize("method", ["GET", "PUT", "HEAD"])
    def test_405_with_an_error_and_nothing_else(self, app_client, errors, method):
        called = []
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", called.append)
        body = body_of("postConversionAction")

        reply = answers(body, sign(body), method=method)

        assert reply.status == 405
        assert set(reply.body) == {"error"}
        assert dict(reply.headers)["Allow"] == "POST"
        assert reply.after is None and called == [] and errors.seen == []
        assert_json_reply(reply)


class TestRow2BodyAlreadyParsed:
    @pytest.mark.parametrize("parsed", [{"version": 1}, ["a"], None])
    def test_500_with_the_words_and_on_error_gets_a_siren_error(self, app_client, errors, parsed):
        called = []
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", called.append)
        body = body_of("postConversionAction")

        reply = answers(parsed, sign(body))

        assert reply.status == 500
        assert PARSED_WORDS in reply.body["error"]
        assert called == []
        assert len(errors.seen) == 1
        error, subject = errors.seen[0]
        assert type(error) is siren.SirenError
        assert PARSED_WORDS in str(error)
        assert subject is None
        assert_json_reply(reply)

    def test_wsgi_answers_500_when_middleware_already_read_the_input(self, app_client, errors):
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", lambda request: None)
        body = body_of("postConversionAction")
        stream = io.BytesIO(body.encode())
        stream.read()  # A middleware parsed it first.

        status, _, payload, _ = run_wsgi(answers.wsgi, body, sign(body), stream=stream)

        assert status == "500 Internal Server Error"
        assert PARSED_WORDS in payload["error"]
        assert isinstance(errors.seen[0][0], siren.SirenError)


class TestRow3SignatureChecks:
    @pytest.mark.parametrize(
        "change",
        ["no signature", "unreadable signature", "wrong secret", "tampered body", "timestamp differs", "too old", "too far ahead"],
    )
    def test_401_for_each_of_verify_requests_checks(self, app_client, errors, change):
        called = []
        answers = handler(app_client, errors).on("distributionResolver", "split-by-score", called.append)
        body = body_of("distributionResolver")
        headers = sign(body)
        if change == "no signature":
            del headers["X-Siren-Signature-V2"]
        elif change == "unreadable signature":
            headers["X-Siren-Signature-V2"] = "v1=abc"
        elif change == "wrong secret":
            headers = sign(body, secret="cd" * 32)
        elif change == "tampered body":
            body = body.replace("10000", "90000")
        elif change == "timestamp differs":
            headers["X-Siren-Timestamp"] = str(int(headers["X-Siren-Timestamp"]) + 1)
        elif change == "too old":
            headers = sign(body, t=int(time.time()) - 301)
        else:
            headers = sign(body, t=int(time.time()) + 301)

        reply = answers(body, headers)

        assert reply.status == 401
        assert set(reply.body) == {"error"}
        assert reply.after is None and called == [] and errors.seen == []
        assert_json_reply(reply)

    def test_tolerance_seconds_widens_the_window(self, app_client, errors):
        answers = handler(app_client, errors, tolerance_seconds=900).on("postConversionAction", lambda request: None)
        body = body_of("postConversionAction")

        assert answers(body, sign(body, t=int(time.time()) - 600)).status == 200

    def test_an_events_signature_is_checked_the_same_way(self, app_client, errors):
        called = []
        answers = handler(app_client, errors).on_event("conversion.approved", called.append)
        body = '{"conversionId": 991}'
        headers = sign(body, event="conversion.approved", t=int(time.time()) - 301)

        assert answers(body, headers).status == 401
        assert called == []


class TestRow4BodyNotARequest:
    def test_400_for_a_signed_body_that_is_not_json(self, app_client, errors):
        answers = handler(app_client, errors).on("postConversionAction", lambda request: None)
        body = "not json"

        reply = answers(body, sign(body))

        assert reply.status == 400 and set(reply.body) == {"error"}
        assert errors.seen == []
        assert_json_reply(reply)

    @pytest.mark.parametrize("change", [{"version": 2}, {"mode": "later"}, {"id": "apr_short"}, {"answerUrl": None}])
    def test_400_for_an_app_request_that_is_not_a_version_1_envelope(self, app_client, errors, change):
        called = []
        answers = handler(app_client, errors).on("distributionResolver", called.append)
        envelope = {**contract("distributionResolver")["request"], **change}
        body = json.dumps(envelope)

        reply = answers(body, sign(body))

        assert reply.status == 400 and called == []

    def test_a_request_with_no_event_header_is_read_as_an_app_request(self, app_client, errors):
        answers = handler(app_client, errors).on("postConversionAction", lambda request: None)
        body = json.dumps({"version": 1})
        headers = sign(body)
        del headers["X-Siren-Event"]

        assert answers(body, headers).status == 400

    @pytest.mark.parametrize("body", ["not json", "[1, 2]"])
    def test_400_for_an_event_body_that_is_not_a_json_object(self, app_client, errors, body):
        called = []
        answers = handler(app_client, errors).on_event("conversion.approved", called.append)

        assert answers(body, sign(body, event="conversion.approved")).status == 400
        assert called == []


class TestRow5Tell:
    def test_200_once_the_function_returns(self, app_client, errors):
        seen = []
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", seen.append)
        body = body_of("postConversionAction")

        reply = answers(body, sign(body))

        assert reply.status == 200 and reply.body == {}
        assert reply.after is None
        assert [request.id for request in seen] == [REQUEST_ID]
        assert seen[0].data == contract("postConversionAction")["request"]["data"]
        assert errors.seen == []
        assert_json_reply(reply)

    def test_500_when_the_function_throws_and_on_error_gets_it(self, app_client, errors):
        def fails(request):
            raise RuntimeError("The CRM is down, token sk_live_secret")

        answers = handler(app_client, errors).on("postAllocationAction", "note-the-share", fails)
        body = body_of("postAllocationAction")

        reply = answers(body, sign(body))

        assert reply.status == 500
        assert reply.body == {"error": "The function for postAllocationAction note-the-share failed, so Siren will send the request again."}
        assert "sk_live_secret" not in reply.content.decode()
        error, subject = errors.seen[0]
        assert isinstance(error, RuntimeError)
        assert subject.id == REQUEST_ID

    def test_an_async_function_is_awaited(self, app_client, errors):
        seen = []

        async def tell(request):
            await asyncio.sleep(0)
            seen.append(request.id)

        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", tell)
        body = body_of("postConversionAction")

        assert answers(body, sign(body)).status == 200
        assert seen == [REQUEST_ID]


class TestRow6Ask:
    def test_200_at_once_then_the_function_runs_after_the_reply(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        seen = []

        def split(request):
            seen.append(request.id)
            return contract("distributionResolver")["answer"]

        answers = handler(app_client, errors).on("distributionResolver", "split-by-score", split)
        body = body_of("distributionResolver")

        reply = answers(body, sign(body))

        assert reply.status == 200 and reply.body == {}
        assert seen == [] and not route.called
        reply.after()
        assert seen == [REQUEST_ID]
        assert route.call_count == 1
        assert json.loads(route.calls[0].request.content) == contract("distributionResolver")["answer"]
        assert errors.seen == []

    def test_a_function_that_returns_later_posts_nothing(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        answers = handler(app_client, errors)
        answers.on("incentiveResolver", "split-the-reward", lambda request: answers.later())
        body = body_of("incentiveResolver")

        reply = answers(body, sign(body))
        reply.after()

        assert reply.status == 200 and not route.called and errors.seen == []

    def test_a_function_that_returns_nothing_posts_nothing_and_reports_it(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        answers = handler(app_client, errors).on("incentiveResolver", "split-the-reward", lambda request: None)
        body = body_of("incentiveResolver")

        reply = answers(body, sign(body))
        reply.after()

        assert reply.status == 200 and not route.called
        [(error, _)] = errors.seen
        assert "split-the-reward returned nothing" in str(error)

    def test_an_async_function_is_awaited_after_the_reply(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})

        async def split(request):
            await asyncio.sleep(0)
            return contract("incentiveResolver")["answer"]

        answers = handler(app_client, errors).on("incentiveResolver", split)
        body = body_of("incentiveResolver")

        answers(body, sign(body)).after()

        assert json.loads(route.calls[0].request.content) == contract("incentiveResolver")["answer"]


class TestRow7NoFunction:
    def test_404_naming_the_kind_and_key(self, app_client, errors):
        answers = handler(app_client, errors).on("distributionResolver", "another-key", lambda request: None)
        body = body_of("distributionResolver")

        reply = answers(body, sign(body))

        assert reply.status == 404
        assert reply.body == {"error": "No function handles distributionResolver split-by-score."}
        assert reply.after is None and errors.seen == []
        assert_json_reply(reply)

    def test_a_function_for_the_kind_takes_every_key(self, app_client, errors):
        seen = []
        answers = handler(app_client, errors).on("postConversionAction", lambda request: seen.append("kind"))
        body = body_of("postConversionAction")

        assert answers(body, sign(body)).status == 200
        assert seen == ["kind"]

    def test_a_function_for_the_key_wins_over_one_for_the_kind(self, app_client, errors):
        seen = []
        answers = (
            handler(app_client, errors)
            .on("postConversionAction", lambda request: seen.append("kind"))
            .on("postConversionAction", "tell-the-crm", lambda request: seen.append("key"))
        )
        body = body_of("postConversionAction")

        answers(body, sign(body))

        assert seen == ["key"]


class TestRow8EventWithFunction:
    def test_200_once_it_returns(self, app_client, errors):
        seen = []
        answers = handler(app_client, errors).on_event("conversion.approved", seen.append)
        body = '{"conversionId": 991}'

        reply = answers(body, sign(body, event="conversion.approved"))

        assert reply.status == 200 and reply.body == {}
        assert isinstance(seen[0], siren.WebhookEvent)
        assert (seen[0].type, seen[0].data, seen[0].delivery_id) == ("conversion.approved", {"conversionId": 991}, f"{REQUEST_ID}.1")
        assert_json_reply(reply)

    def test_an_envelope_is_read_as_construct_event_reads_one(self, app_client, errors):
        seen = []
        answers = handler(app_client, errors).on_event("conversion.approved", seen.append)
        body = json.dumps({"version": 1, "id": f"{REQUEST_ID}.1", "type": "conversion.approved", "data": {"conversionId": 991}})

        answers(body, sign(body, event="conversion.approved"))

        assert seen[0].data == {"conversionId": 991}

    def test_500_when_it_throws(self, app_client, errors):
        def fails(event):
            raise RuntimeError("down")

        answers = handler(app_client, errors).on_event("conversion.approved", fails)
        body = '{"conversionId": 991}'

        reply = answers(body, sign(body, event="conversion.approved"))

        assert reply.status == 500
        assert reply.body == {"error": "The function for the conversion.approved event failed, so Siren will send it again."}
        assert isinstance(errors.seen[0][0], RuntimeError)
        assert isinstance(errors.seen[0][1], siren.WebhookEvent)


class TestRow9EventWithoutFunction:
    def test_200_and_nothing_runs(self, app_client, errors):
        answers = handler(app_client, errors).on_event("conversion.approved", lambda event: None)
        body = '{"payoutId": 5}'

        reply = answers(body, sign(body, event="payout.paid"))

        assert reply.status == 200 and reply.body == {}
        assert errors.seen == []


def test_no_reply_holds_the_secret_or_the_body(app_client, errors):
    def fails(request):
        raise RuntimeError(SECRET)

    answers = handler(app_client, errors).on("postConversionAction", fails)
    body = body_of("postConversionAction")
    replies = [
        answers(body, sign(body), method="GET"),
        answers({"parsed": True}, sign(body)),
        answers(body, sign(body, secret="cd" * 32)),
        answers("not json", sign("not json")),
        answers(body, sign(body)),
        answers(body_of("distributionResolver"), sign(body_of("distributionResolver"))),
    ]

    for reply in replies:
        assert SECRET not in reply.content.decode()
        assert body not in reply.content.decode() and "conversionId" not in reply.content.decode()


# Section 3.2, step by step -------------------------------------------------


def ask(app_client, errors, answer: Any, **options) -> None:
    """Sends the distribution ask through a handler whose function returns
    `answer`, and runs the work after the reply."""
    answers = handler(app_client, errors, **options).on("distributionResolver", "split-by-score", lambda request: answer)
    body = body_of("distributionResolver")
    reply = answers(body, sign(body))
    assert reply.status == 200
    reply.after()


GOOD_ANSWER = contract("distributionResolver")["answer"]


class TestStep1TheAnswerIsChecked:
    @pytest.mark.parametrize(
        "answer, words",
        [
            (["allocated"], "takes the answer as a mapping"),
            ({"units": "USD"}, "needs a text outcome"),
            ({"outcome": ""}, "needs a text outcome"),
            ({"outcome": "allocated", "note": "x" * 16384}, "at most 16384 bytes"),
            ({"outcome": "allocated", "value": float("nan")}, "could not write the answer as JSON"),
        ],
    )
    def test_a_bad_answer_goes_to_on_error_and_is_not_sent(self, app_client, errors, siren_api, answer, words):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})

        ask(app_client, errors, answer)

        assert not route.called
        error, subject = errors.seen[0]
        assert isinstance(error, ValueError) and words in str(error)
        assert subject.id == REQUEST_ID


class TestStep2TheAnswerIsPosted:
    def test_posted_to_the_answer_route_with_the_apps_key(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})

        ask(app_client, errors, GOOD_ANSWER)

        sent = route.calls[0].request
        assert sent.url == f"{API_BASE}{ANSWER_PATH}"
        assert sent.headers["Authorization"] == f"Bearer {API_KEY}"
        assert json.loads(sent.content) == GOOD_ANSWER
        assert errors.seen == []


class TestStep3TheAnswerIsRetried:
    @pytest.mark.parametrize(
        "failure",
        [httpx.Response(503), httpx.Response(429), httpx.Response(500), httpx.ConnectError("Connection refused")],
        ids=["503", "429", "500", "lost connection"],
    )
    def test_resent_on_a_lost_connection_429_or_5xx_and_the_same_bytes_go(self, app_client, errors, siren_api, failure):
        route = siren_api.post(ANSWER_PATH).mock(
            side_effect=[failure, httpx.Response(200, json={"status": "accepted", "duplicate": True})]
        )

        ask(app_client, errors, GOOD_ANSWER)

        assert route.call_count == 2
        assert route.calls[0].request.content == route.calls[1].request.content
        assert errors.seen == []

    def test_resent_up_to_max_retries_then_on_error_gets_the_failure(self, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(503)
        with siren.Siren(api_key=API_KEY, base_url=API_BASE, max_retries=3) as client:
            ask(client, errors, GOOD_ANSWER)

        assert route.call_count == 4
        assert isinstance(errors.seen[0][0], siren.ApiError)

    def test_requests_answer_itself_is_still_sent_once(self, app_client, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(503)

        with pytest.raises(siren.ApiError):
            app_client.apps.requests.answer(REQUEST_ID, GOOD_ANSWER)

        assert route.call_count == 1


class TestStep4RefusalsAreNotResent:
    @pytest.mark.parametrize(
        "status, code, error_class",
        [
            (422, "app_answer_invalid", siren.ValidationError),
            (409, "app_request_already_answered", siren.ConflictError),
            (409, "app_request_dropped", siren.ConflictError),
            (403, "app_answer_forbidden", siren.PermissionError),
            (404, "app_request_not_found", siren.NotFoundError),
        ],
    )
    def test_on_error_gets_the_typed_error_and_it_is_sent_once(self, app_client, errors, siren_api, status, code, error_class):
        route = siren_api.post(ANSWER_PATH).respond(status, json={"error": {"message": "Refused.", "code": code}})

        ask(app_client, errors, GOOD_ANSWER)

        assert route.call_count == 1
        error, subject = errors.seen[0]
        assert isinstance(error, error_class)
        assert (error.status_code, error.code) == (status, code)
        assert subject.id == REQUEST_ID

    def test_an_uncoded_401_gets_the_app_code_and_is_sent_once(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(401, json={"error": "Unauthorized"})

        ask(app_client, errors, GOOD_ANSWER)

        assert route.call_count == 1
        error = errors.seen[0][0]
        assert isinstance(error, siren.AuthenticationError)
        assert error.code == siren.ErrorCode.APP_ANSWER_UNAUTHORIZED


class TestStep5NothingIsAdded:
    def test_a_function_that_throws_goes_to_on_error_and_nothing_is_posted(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})

        def fails(request):
            raise RuntimeError("The ledger is down.")

        answers = handler(app_client, errors).on("distributionResolver", fails)
        body = body_of("distributionResolver")
        reply = answers(body, sign(body))
        reply.after()

        assert reply.status == 200
        assert not route.called
        assert isinstance(errors.seen[0][0], RuntimeError)

    def test_the_handler_keeps_no_state_so_a_second_send_runs_again(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        answers = handler(app_client, errors).on("distributionResolver", lambda request: GOOD_ANSWER)
        body = body_of("distributionResolver")

        answers(body, sign(body)).after()
        answers(body, sign(body)).after()

        assert route.call_count == 2


# Options, registration and the default on_error ----------------------------


class TestOptions:
    def test_run_later_is_handed_the_work_and_the_reply_carries_none(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        queued = []
        answers = handler(app_client, errors, run_later=queued.append).on("distributionResolver", lambda request: GOOD_ANSWER)
        body = body_of("distributionResolver")

        reply = answers(body, sign(body))

        assert reply.status == 200 and reply.after is None
        assert len(queued) == 1 and not route.called
        queued[0]()
        assert route.call_count == 1

    def test_run_later_that_throws_goes_to_on_error(self, app_client, errors):
        def broken_queue(work):
            raise RuntimeError("The queue is down.")

        answers = handler(app_client, errors, run_later=broken_queue).on("distributionResolver", lambda request: GOOD_ANSWER)
        body = body_of("distributionResolver")

        assert answers(body, sign(body)).status == 200
        assert str(errors.seen[0][0]) == "The queue is down."

    def test_the_default_on_error_logs_to_the_siren_logger(self, app_client, caplog):
        answers = app_client.apps.request_handler(SECRET)
        body = body_of("postConversionAction")

        with caplog.at_level("ERROR", logger="siren"):
            answers({"parsed": True}, sign(body))

        assert PARSED_WORDS in caplog.text

    def test_an_on_error_that_throws_is_logged_and_the_reply_stands(self, app_client, caplog):
        def broken(error, subject=None):
            raise RuntimeError("on_error broke")

        answers = app_client.apps.request_handler(SECRET, on_error=broken)
        body = body_of("postConversionAction")

        with caplog.at_level("ERROR", logger="siren"):
            assert answers({"parsed": True}, sign(body)).status == 500

        assert "on_error broke" in caplog.text

    @pytest.mark.parametrize(
        "args, kwargs",
        [
            (("",), {}),
            ((None,), {}),
            ((SECRET,), {"tolerance_seconds": 0}),
            ((SECRET,), {"tolerance_seconds": 3601}),
            ((SECRET,), {"run_later": "queue"}),
            ((SECRET,), {"on_error": "log"}),
        ],
    )
    def test_bad_options_are_refused(self, app_client, args, kwargs):
        with pytest.raises(ValueError):
            app_client.apps.request_handler(*args, **kwargs)


class TestRegistration:
    def test_on_works_as_a_decorator_and_hands_the_function_back(self, app_client, errors):
        answers = handler(app_client, errors)

        @answers.on("postConversionAction", "tell-the-crm")
        def tell(request):
            return None

        @answers.on("postAllocationAction")
        def every_allocation(request):
            return None

        @answers.on_event("conversion.approved")
        def noted(event):
            return None

        assert callable(tell) and tell.__name__ == "tell"
        assert every_allocation.__name__ == "every_allocation" and noted.__name__ == "noted"
        body = body_of("postAllocationAction")
        assert answers(body, sign(body)).status == 200

    def test_on_and_on_event_chain(self, app_client, errors):
        answers = handler(app_client, errors)

        assert answers.on("postConversionAction", "tell-the-crm", lambda r: None) is answers
        assert answers.on("postAllocationAction", lambda r: None) is answers
        assert answers.on_event("conversion.approved", lambda e: None) is answers

    @pytest.mark.parametrize(
        "args",
        [
            ("", "key", lambda r: None),
            ("postConversionAction", "", lambda r: None),
            ("postConversionAction", "key", "not callable"),
            (None, lambda r: None),
        ],
    )
    def test_on_refuses_a_bad_kind_key_or_function(self, app_client, errors, args):
        with pytest.raises(ValueError):
            handler(app_client, errors).on(*args)

    def test_a_second_function_for_the_same_kind_and_key_is_refused(self, app_client, errors):
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", lambda r: None)

        with pytest.raises(ValueError, match="already has a function"):
            answers.on("postConversionAction", "tell-the-crm", lambda r: None)

    @pytest.mark.parametrize("event", ["", "app.request"])
    def test_on_event_refuses_an_empty_type_and_app_request(self, app_client, errors, event):
        with pytest.raises(ValueError):
            handler(app_client, errors).on_event(event, lambda e: None)


# The faces: WSGI, ASGI, Flask, Django ---------------------------------------


def run_wsgi(app, body: str, headers: Dict[str, str], *, method: str = "POST", stream=None):
    """Calls a WSGI app the way a server does. Returns the status, headers,
    JSON body and the iterable, whose close() the caller makes."""
    data = body.encode()
    environ: Dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/siren",
        "QUERY_STRING": "",
        "SERVER_NAME": "acme.example",
        "SERVER_PORT": "443",
        "wsgi.url_scheme": "https",
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": stream if stream is not None else io.BytesIO(data),
    }
    for name, value in headers.items():
        if name == "Content-Type":
            environ["CONTENT_TYPE"] = value
        else:
            environ["HTTP_" + name.upper().replace("-", "_")] = value
    started: Dict[str, Any] = {}

    def start_response(status, response_headers, exc_info=None):
        started["status"] = status
        started["headers"] = dict(response_headers)

    iterable = app(environ, start_response)
    payload = b"".join(iterable)
    return started["status"], started["headers"], json.loads(payload), iterable


class TestWsgi:
    def test_a_tell_answers_200_with_the_headers(self, app_client, errors):
        seen = []
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", seen.append)
        body = body_of("postConversionAction")

        status, headers, payload, iterable = run_wsgi(answers.wsgi, body, sign(body))
        iterable.close()

        assert (status, payload) == ("200 OK", {})
        assert headers["Content-Type"] == "application/json" and headers["Cache-Control"] == "no-store"
        assert headers["Content-Length"] == "2"
        assert len(seen) == 1

    def test_an_asks_work_runs_from_close_after_the_body_is_sent(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        answers = handler(app_client, errors).on("distributionResolver", lambda request: GOOD_ANSWER)
        body = body_of("distributionResolver")

        status, _, payload, iterable = run_wsgi(answers.wsgi, body, sign(body))

        assert (status, payload) == ("200 OK", {})
        assert not route.called
        iterable.close()
        assert route.call_count == 1

    def test_405_404_and_401_get_their_status_lines(self, app_client, errors):
        answers = handler(app_client, errors).on("postConversionAction", "another", lambda r: None)
        body = body_of("postConversionAction")

        assert run_wsgi(answers.wsgi, body, sign(body), method="GET")[0] == "405 Method Not Allowed"
        assert run_wsgi(answers.wsgi, body, sign(body))[0] == "404 Not Found"
        assert run_wsgi(answers.wsgi, body, sign(body, secret="cd" * 32))[0] == "401 Unauthorized"

    def test_run_later_gets_the_work_at_close(self, app_client, errors):
        queued = []
        answers = handler(app_client, errors, run_later=queued.append).on("distributionResolver", lambda r: GOOD_ANSWER)
        body = body_of("distributionResolver")

        iterable = run_wsgi(answers.wsgi, body, sign(body))[3]
        assert queued == []
        iterable.close()
        assert len(queued) == 1


def run_asgi(app, body: Union[str, bytes], headers: Dict[str, str], *, method: str = "POST", chunks: int = 2, order: Optional[List[str]] = None):
    """Calls an ASGI app. Each message sent is appended to ``order``, so a
    test can put its own steps in the same list and compare the two."""
    data = body.encode() if isinstance(body, str) else body
    size = max(1, len(data) // chunks + 1)
    parts = [data[i : i + size] for i in range(0, len(data), size)] or [b""]
    messages = [{"type": "http.request", "body": part, "more_body": i < len(parts) - 1} for i, part in enumerate(parts)]
    sent: List[Dict[str, Any]] = []
    order = [] if order is None else order

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        order.append(message["type"])

    scope = {
        "type": "http",
        "method": method,
        "path": "/siren",
        "query_string": b"",
        "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()],
    }
    asyncio.run(app(scope, receive, send))
    start, reply = sent[0], sent[1]
    return start["status"], dict(start["headers"]), json.loads(reply["body"]), order


class TestAsgi:
    def test_a_sync_tell_runs_on_a_worker_thread_before_the_reply(self, app_client, errors):
        threads = []
        answers = handler(app_client, errors).on("postConversionAction", lambda r: threads.append(threading.current_thread()))
        body = body_of("postConversionAction")

        status, headers, payload, _ = run_asgi(answers.asgi, body, sign(body))

        assert (status, payload) == (200, {})
        assert headers[b"content-type"] == b"application/json" and headers[b"cache-control"] == b"no-store"
        assert threads and threads[0] is not threading.main_thread()

    def test_an_async_tell_is_awaited_and_a_throw_answers_500(self, app_client, errors):
        async def fails(request):
            raise RuntimeError("down")

        answers = handler(app_client, errors).on("postConversionAction", fails)
        body = body_of("postConversionAction")

        assert run_asgi(answers.asgi, body, sign(body))[0] == 500
        assert isinstance(errors.seen[0][0], RuntimeError)

    def test_an_asks_work_runs_after_the_reply_is_sent(self, app_client, errors, siren_api):
        order: List[str] = []
        siren_api.post(ANSWER_PATH).mock(side_effect=lambda request: order.append("posted") or httpx.Response(200, json={"status": "accepted"}))

        async def split(request):
            order.append("function")
            return GOOD_ANSWER

        answers = handler(app_client, errors).on("distributionResolver", split)
        body = body_of("distributionResolver")

        status, _, payload, _ = run_asgi(answers.asgi, body, sign(body), order=order)

        assert (status, payload) == (200, {})
        # One list holds the reply's messages and the work, so it shows the reply went first.
        assert order == ["http.response.start", "http.response.body", "function", "posted"]
        assert errors.seen == []

    def test_a_sync_ask_runs_on_a_worker_thread(self, app_client, errors, siren_api):
        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        answers = handler(app_client, errors).on("distributionResolver", lambda r: GOOD_ANSWER)
        body = body_of("distributionResolver")

        assert run_asgi(answers.asgi, body, sign(body))[0] == 200
        assert route.call_count == 1

    def test_run_later_gets_the_work_after_the_reply(self, app_client, errors):
        queued = []
        answers = handler(app_client, errors, run_later=queued.append).on("distributionResolver", lambda r: GOOD_ANSWER)
        body = body_of("distributionResolver")

        assert run_asgi(answers.asgi, body, sign(body))[0] == 200
        assert len(queued) == 1

    def test_401_and_405(self, app_client, errors):
        answers = handler(app_client, errors).on("postConversionAction", lambda r: None)
        body = body_of("postConversionAction")

        assert run_asgi(answers.asgi, body, sign(body, secret="cd" * 32))[0] == 401
        assert run_asgi(answers.asgi, body, sign(body), method="GET")[0] == 405

    def test_completes_a_lifespan(self, app_client, errors):
        app = handler(app_client, errors).asgi
        messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message["type"])

        asyncio.run(app({"type": "lifespan"}, receive, send))

        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


class TestFlask:
    @pytest.fixture(autouse=True)
    def flask_installed(self):
        pytest.importorskip("flask")

    def flask_app(self, answers):
        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/siren", view_func=answers.flask, methods=["POST"])
        return app

    def test_a_tell_answers_200(self, app_client, errors):
        seen = []
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", seen.append)
        body = body_of("postConversionAction")

        response = self.flask_app(answers).test_client().post("/siren", data=body, headers=sign(body))

        assert response.status_code == 200 and response.get_json() == {}
        assert response.headers["Cache-Control"] == "no-store"
        assert len(seen) == 1

    def test_an_asks_work_runs_when_the_response_closes(self, app_client, errors, siren_api):
        from werkzeug.test import EnvironBuilder

        route = siren_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
        answers = handler(app_client, errors).on("distributionResolver", lambda r: GOOD_ANSWER)
        body = body_of("distributionResolver")
        environ = EnvironBuilder(path="/siren", method="POST", data=body, headers=sign(body)).get_environ()
        started = {}

        iterable = self.flask_app(answers).wsgi_app(environ, lambda status, headers, exc_info=None: started.update(status=status))
        assert json.loads(b"".join(iterable)) == {}
        assert started["status"] == "200 OK" and not route.called
        iterable.close()
        assert route.call_count == 1

    def test_500_when_a_before_request_hook_ate_the_stream(self, app_client, errors):
        answers = handler(app_client, errors).on("postConversionAction", lambda r: None)
        app = self.flask_app(answers)

        @app.before_request
        def eat():
            from flask import request

            request.stream.read()

        body = body_of("postConversionAction")
        response = app.test_client().post("/siren", data=body, headers=sign(body))

        assert response.status_code == 500
        assert PARSED_WORDS in response.get_json()["error"]

    def test_the_view_is_the_same_object_each_time(self, app_client, errors):
        answers = handler(app_client, errors)

        assert answers.flask is answers.flask


@pytest.fixture
def django_ready():
    pytest.importorskip("django")
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(DEBUG=False, ALLOWED_HOSTS=["*"], DEFAULT_CHARSET="utf-8", USE_TZ=True)
        django.setup()


def django_request(body: str, headers: Dict[str, str], method: str = "POST"):
    from django.test import RequestFactory

    extra = {"HTTP_" + name.upper().replace("-", "_"): value for name, value in headers.items() if name != "Content-Type"}
    return RequestFactory().generic(method, "/siren", data=body.encode(), content_type="application/json", **extra)


class TestDjango:
    def test_the_view_is_csrf_exempt(self, app_client, errors):
        assert handler(app_client, errors).django.csrf_exempt is True

    def test_a_tell_answers_200(self, app_client, errors, django_ready):
        seen = []
        answers = handler(app_client, errors).on("postConversionAction", "tell-the-crm", seen.append)
        body = body_of("postConversionAction")

        response = answers.django(django_request(body, sign(body)))

        assert response.status_code == 200 and json.loads(response.content) == {}
        assert response["Cache-Control"] == "no-store" and response["Content-Type"] == "application/json"
        assert len(seen) == 1

    def test_an_ask_answers_200_and_its_work_runs_on_a_thread(self, app_client, errors, siren_api, django_ready):
        posted = threading.Event()
        siren_api.post(ANSWER_PATH).mock(side_effect=lambda request: posted.set() or httpx.Response(200, json={"status": "accepted"}))
        answers = handler(app_client, errors).on("distributionResolver", lambda r: GOOD_ANSWER)
        body = body_of("distributionResolver")

        response = answers.django(django_request(body, sign(body)))

        assert response.status_code == 200
        assert posted.wait(5)

    def test_500_when_the_body_stream_was_already_read(self, app_client, errors, django_ready):
        answers = handler(app_client, errors).on("postConversionAction", lambda r: None)
        body = body_of("postConversionAction")
        request = django_request(body, sign(body))
        request.read()

        response = answers.django(request)

        assert response.status_code == 500
        assert PARSED_WORDS in json.loads(response.content)["error"]

    def test_405_for_a_get(self, app_client, errors, django_ready):
        answers = handler(app_client, errors)
        body = body_of("postConversionAction")

        assert answers.django(django_request(body, sign(body), method="GET")).status_code == 405


def test_no_framework_is_imported_until_its_face_is_used():
    import subprocess
    import sys

    script = (
        "import sys, siren\n"
        "answers = siren.Siren(api_key='sk').apps.request_handler('s')\n"
        "answers.flask, answers.django, answers.asgi, answers.wsgi\n"
        "print(sorted(name for name in ('flask', 'django', 'starlette', 'fastapi') if name in sys.modules))\n"
    )

    output = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout

    assert output.strip() == "[]"


# Parity with Node: a body over 1 MiB answers 413 before anything is read or checked.

TOO_BIG = b"x" * (MAX_BODY_BYTES + 1)


class TestBodyOverTheLimit:
    def test_the_plain_call_answers_413_before_the_signature(self, app_client, errors):
        called = []
        answers = handler(app_client, errors).on("postConversionAction", called.append)

        reply = answers(TOO_BIG, sign(TOO_BIG.decode()))

        assert reply.status == 413
        assert reply.body == {"error": f"The request body is over {MAX_BODY_BYTES} bytes, more than Siren sends."}
        assert called == [] and errors.seen == []
        assert_json_reply(reply)

    def test_a_body_of_exactly_the_limit_is_checked_as_usual(self, app_client, errors):
        body = b"x" * MAX_BODY_BYTES

        assert handler(app_client, errors)(body, sign(body.decode())).status == 400

    def test_a_get_still_answers_405_first(self, app_client, errors):
        assert handler(app_client, errors)(TOO_BIG, {}, method="GET").status == 405

    def test_wsgi_answers_413_from_the_length_without_reading_the_input(self, app_client, errors):
        class Unread(io.BytesIO):
            def read(self, *args):
                raise AssertionError("the handler read a body over the limit")

        answers = handler(app_client, errors)
        status, _, payload, _ = run_wsgi(answers.wsgi, TOO_BIG.decode(), sign("x"), stream=Unread())

        assert status.startswith("413 ")  # The phrase changed name in Python 3.13.
        assert "more than Siren sends" in payload["error"]

    def test_wsgi_reads_a_body_with_no_length_only_up_to_the_limit(self, app_client, errors):
        stream = io.BytesIO(TOO_BIG + b"y" * 10)
        environ = {"REQUEST_METHOD": "POST", "wsgi.input": stream, "wsgi.input_terminated": True}
        started = {}

        payload = b"".join(handler(app_client, errors).wsgi(environ, lambda status, headers: started.update(status=status)))

        assert started["status"].startswith("413 ")
        assert stream.tell() == MAX_BODY_BYTES + 1
        assert "more than Siren sends" in json.loads(payload)["error"]

    def test_asgi_answers_413_and_stops_reading(self, app_client, errors):
        chunk = {"type": "http.request", "body": b"x" * (MAX_BODY_BYTES // 2 + 1), "more_body": True}
        messages = [dict(chunk), dict(chunk), dict(chunk), {"type": "http.request", "body": b"", "more_body": False}]
        sent: List[Dict[str, Any]] = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/siren", "query_string": b"", "headers": []}
        asyncio.run(handler(app_client, errors).asgi(scope, receive, send))

        assert sent[0]["status"] == 413
        assert "more than Siren sends" in json.loads(sent[1]["body"])["error"]
        assert len(messages) == 2  # It stopped at the chunk that passed the limit.

    def test_flask_answers_413_from_the_length_without_reading(self, app_client, errors):
        pytest.importorskip("flask")
        from flask import Flask
        from werkzeug.test import EnvironBuilder

        class Unread(io.BytesIO):
            def read(self, *args):
                raise AssertionError("the handler read a body over the limit")

        app = Flask(__name__)
        app.add_url_rule("/siren", view_func=handler(app_client, errors).flask, methods=["POST"])
        environ = EnvironBuilder(path="/siren", method="POST", data=TOO_BIG, headers=sign("x")).get_environ()
        environ["wsgi.input"] = Unread()
        started = {}

        payload = b"".join(app.wsgi_app(environ, lambda status, headers, exc_info=None: started.update(status=status)))

        assert started["status"].startswith("413 ")
        assert "more than Siren sends" in json.loads(payload)["error"]

    def test_django_answers_413_from_the_length_without_reading(self, app_client, errors, django_ready):
        class Unread(io.BytesIO):
            def read(self, *args):
                raise AssertionError("the handler read a body over the limit")

        request = django_request(TOO_BIG.decode(), sign("x"))
        request._stream = Unread()

        response = handler(app_client, errors).django(request)

        assert response.status_code == 413
        assert "more than Siren sends" in json.loads(response.content)["error"]
