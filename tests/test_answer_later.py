"""DX pass 2, finding 3: "answer later" is something an ask's function says,
by returning ``answers.later()``. An ask's function that returns nothing is
an error, so a forgotten ``return`` shows in the log and in ``simulate()``
instead of leaving Siren to wait out three answer windows."""

from __future__ import annotations

import json

import pytest
import respx

import siren
from siren import testing

from .conftest import API_KEY, BASE_URL

SECRET = "cd" * 32
REQUEST_ID = "apr_" + "b339" * 8
ANSWER_PATH = f"/apps/requests/{REQUEST_ID}/answer"
NOTHING = (
    "The function for distributionResolver split-by-score returned nothing. "
    "Return the answer, or return answers.later() when you will answer with apps.requests.answer()."
)
GOOD = {"outcome": "allocated", "units": "USD", "shares": {"41": 6000, "43": 4000}}


class Errors:
    def __init__(self):
        self.seen = []

    def __call__(self, error, subject=None):
        self.seen.append((error, subject))


@pytest.fixture
def setup(mocked_api):
    errors = Errors()
    route = mocked_api.post(ANSWER_PATH).respond(200, json={"status": "accepted"})
    with siren.Siren(api_key=API_KEY) as client:
        yield client, client.apps.request_handler(SECRET, on_error=errors), errors, route


def ask():
    return testing.app_request("distributionResolver", "split-by-score", secret=SECRET, id=REQUEST_ID)


def call(answers, request):
    reply = answers(request.raw_body, request.headers)
    assert reply.status == 200
    reply.after()
    return reply


def test_later_is_a_marker_the_handler_hands_out():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)
    assert answers.later() is answers.later()
    assert answers.later() is not None


def test_an_ask_that_returns_later_posts_nothing_and_reports_nothing(setup):
    client, answers, errors, route = setup
    answers.on("distributionResolver", "split-by-score", lambda request: answers.later())

    call(answers, ask())

    assert not route.called and errors.seen == []


def test_an_ask_that_returns_nothing_is_an_error_and_posts_nothing(setup):
    client, answers, errors, route = setup
    answers.on("distributionResolver", "split-by-score", lambda request: None)

    call(answers, ask())

    assert not route.called
    [(error, subject)] = errors.seen
    assert isinstance(error, siren.SirenError) and str(error) == NOTHING
    assert subject.id == REQUEST_ID


def test_an_async_ask_under_asgi_that_returns_nothing_is_the_same_error(setup):
    import asyncio

    client, answers, errors, route = setup

    async def forgot(request):
        GOOD  # builds the answer and drops it

    answers.on("distributionResolver", "split-by-score", forgot)
    request = ask()
    sent = []

    async def run():
        messages = [{"type": "http.request", "body": request.raw_body, "more_body": False}]

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(k.lower().encode(), v.encode()) for k, v in request.headers.items()],
        }
        await answers.asgi(scope, receive, send)

    asyncio.run(run())

    assert sent[0]["status"] == 200 and not route.called
    assert [str(error) for error, _ in errors.seen] == [NOTHING]


def test_a_tell_that_returns_nothing_is_fine_and_its_value_is_ignored(setup):
    client, answers, errors, route = setup
    answers.on("postConversionAction", "tell-the-crm", lambda request: None)
    answers.on("postAllocationAction", "tell-the-crm", lambda request: {"outcome": "ignored"})

    for kind in ("postConversionAction", "postAllocationAction"):
        request = testing.app_request(kind, "tell-the-crm", secret=SECRET)
        reply = answers(request.raw_body, request.headers)
        assert reply.status == 200 and reply.after is None

    assert errors.seen == [] and not route.called


def test_simulate_returns_the_error_for_an_ask_that_returns_nothing():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)
    answers.on("distributionResolver", "split-by-score", lambda request: None)

    result = answers.simulate(ask())

    assert (result.status, result.answer) == (200, None)
    assert isinstance(result.error, siren.SirenError) and str(result.error) == NOTHING


def test_simulate_shows_later_as_no_answer_and_no_error():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)
    answers.on("distributionResolver", "split-by-score", lambda request: answers.later())

    result = answers.simulate(ask())

    assert (result.status, result.answer, result.error) == (200, None, None)


def test_the_answer_later_path_still_answers_by_hand(setup):
    client, answers, errors, route = setup
    queued = []

    @answers.on("distributionResolver", "split-by-score")
    def queue_it(request):
        queued.append(request.id)
        return answers.later()

    call(answers, ask())
    receipt = client.apps.requests.answer(queued[0], GOOD)

    assert receipt.status == "accepted" and route.call_count == 1
    assert json.loads(route.calls[0].request.content) == GOOD and errors.seen == []
