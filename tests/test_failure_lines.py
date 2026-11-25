"""DX pass 2, finding 4: the default ``on_error`` line says when Siren sends a
failed request again. An ask already got its 200, so Siren sends it again only
when its answer window closes, and the line says when and how to resend it
sooner. A tell's 500 comes back in about a minute."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import siren
from siren import testing
from siren.testing import AppTestRequest

from .conftest import API_KEY

SECRET = "cd" * 32
REQUEST_ID = "apr_" + "b339" * 8
ANSWER_PATH = f"/apps/requests/{REQUEST_ID}/answer"
GOOD = {"outcome": "allocated", "units": "USD", "shares": {"41": 6000, "43": 4000}}
RESEND = f"Resend it sooner with `siren apps resend {REQUEST_ID}`."


def ask(answer_by=None):
    request = testing.app_request("distributionResolver", "split-by-score", secret=SECRET, id=REQUEST_ID)
    if answer_by is None:
        return request
    body = request.body
    body["answerBy"] = answer_by
    return AppTestRequest.signed(json.dumps(body).encode(), SECRET)


def run(answers, request):
    reply = answers(request.raw_body, request.headers)
    if reply.after is not None:
        reply.after()
    return reply


def window_close(request):
    closes = datetime.fromisoformat(request.body["answerBy"]).astimezone(timezone.utc)
    # The line names the date too when the window closes on another UTC day.
    if closes.date() != datetime.now(timezone.utc).date():
        return closes.strftime("%Y-%m-%d %H:%M UTC")
    return closes.strftime("%H:%M UTC")


@pytest.fixture
def answers(mocked_api):
    with siren.Siren(api_key=API_KEY) as client:
        yield client.apps.request_handler(SECRET)


def lines(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "siren"]


def test_an_ask_that_raises_says_when_siren_sends_it_again(answers, caplog):
    def broken(request):
        raise RuntimeError("ledger down")

    answers.on("distributionResolver", "split-by-score", broken)
    request = ask()
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, request)

    [line] = lines(caplog)
    assert line == (
        f"Siren request handler (distributionResolver split-by-score {REQUEST_ID}): ledger down. "
        f"Siren sends it again after {window_close(request)}, when its answer window closes. {RESEND}"
    )
    assert caplog.records[0].exc_info is not None


def test_an_ask_whose_answer_by_is_tomorrow_names_the_day(answers, caplog):
    answers.on("distributionResolver", "split-by-score", lambda request: None)
    later = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=9, minute=5, second=0, microsecond=0)
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, ask(later.isoformat()))

    [line] = lines(caplog)
    assert f"Siren sends it again after {later:%Y-%m-%d} 09:05 UTC, when its answer window closes. {RESEND}" in line
    assert "returned nothing" in line


def test_an_ask_without_a_readable_answer_by_leaves_the_time_out(answers, caplog):
    answers.on("distributionResolver", "split-by-score", lambda request: {"no": "outcome"})
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, ask("when the window closes"))

    [line] = lines(caplog)
    assert line.endswith(f"Siren sends it again when its answer window closes. {RESEND}")
    assert "needs a text outcome" in line


def test_a_refused_post_says_when_siren_sends_it_again(answers, mocked_api, caplog):
    # A 422 app_answer_invalid says to reproduce the answer instead
    # (tests/test_dx_pass4.py), since a resend would only repeat it.
    mocked_api.post(ANSWER_PATH).respond(400, json={"error": {"message": "The request could not be read."}})
    answers.on("distributionResolver", "split-by-score", lambda request: GOOD)
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, ask())

    [line] = lines(caplog)
    assert "when its answer window closes" in line and line.endswith(RESEND)


def test_a_lost_post_says_when_siren_sends_it_again(answers, mocked_api, caplog):
    mocked_api.post(ANSWER_PATH).mock(side_effect=httpx.ConnectError("refused"))
    answers.on("distributionResolver", "split-by-score", lambda request: GOOD)
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, ask())

    [line] = lines(caplog)
    assert line.endswith(RESEND)


@pytest.mark.parametrize("code", ["app_request_already_answered", "app_request_dropped"])
def test_a_409_says_nothing_about_a_next_send_since_none_comes(answers, mocked_api, caplog, code):
    mocked_api.post(ANSWER_PATH).respond(409, json={"error": {"code": code, "message": "Siren took no answer."}})
    answers.on("distributionResolver", "split-by-score", lambda request: GOOD)
    with caplog.at_level("ERROR", logger="siren"):
        run(answers, ask())

    [line] = lines(caplog)
    assert "sends it again" not in line and "resend" not in line


def test_a_tell_that_raises_says_about_a_minute(answers, caplog):
    def broken(request):
        raise RuntimeError("crm down")

    answers.on("postConversionAction", "tell-the-crm", broken)
    request = testing.app_request("postConversionAction", "tell-the-crm", secret=SECRET, id=REQUEST_ID)
    with caplog.at_level("ERROR", logger="siren"):
        reply = run(answers, request)

    assert reply.status == 500
    [line] = lines(caplog)
    assert line == f"Siren request handler (postConversionAction tell-the-crm {REQUEST_ID}): crm down. Siren sends it again in about a minute."


def test_an_on_error_of_the_apps_own_gets_the_error_alone(mocked_api):
    seen = []
    with siren.Siren(api_key=API_KEY) as client:
        answers = client.apps.request_handler(SECRET, on_error=lambda error, request: seen.append(str(error)))

        def broken(request):
            raise RuntimeError("ledger down")

        answers.on("distributionResolver", "split-by-score", broken)
        run(answers, ask())

    assert seen == ["ledger down"]
