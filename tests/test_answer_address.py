"""The handler posts an ask's answer only to the client's own base address,
and refuses an ask whose signed ``answerUrl`` points at another Siren, with a
500 and an ``on_error`` message naming both addresses, so the fault shows on
the first request instead of hours later."""

from __future__ import annotations

import json
from typing import Any, List

import pytest
import respx

import siren
from siren import testing

SECRET = "cd" * 32


def resolver(client: siren.Siren, errors: List[Any]):
    answers = client.apps.request_handler(SECRET, on_error=lambda error, request: errors.append(error))
    answers.on("metricFilter", "refund-check", lambda request: {"outcome": "allow"})
    return answers


def test_an_ask_from_another_siren_gets_500_and_names_both_addresses():
    errors: List[Any] = []
    with respx.mock(assert_all_called=False) as network:
        answers = resolver(siren.Siren(api_key="sk_test"), errors)
        request = testing.app_request("metricFilter", "refund-check", secret=SECRET, base_url="https://preview.example.com/siren/v1")

        reply = answers(request.raw_body, request.headers)

        assert reply.status == 500 and reply.after is None
        assert "https://preview.example.com/siren/v1" in reply.body["error"]
        assert len(errors) == 1 and isinstance(errors[0], siren.SirenError)
        message = str(errors[0])
        assert "https://preview.example.com/siren/v1/apps/requests/" in message
        assert "https://api.sirenaffiliates.com/siren/v1" in message
        assert "Set base_url to https://preview.example.com/siren/v1" in message
        assert network.calls.call_count == 0


def test_simulate_shows_the_same_500():
    errors: List[Any] = []
    answers = resolver(siren.Siren(api_key="sk_test", base_url="http://localhost:8080/siren/v1"), errors)

    result = answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET))

    assert result.status == 500 and result.answer is None and len(errors) == 1


@pytest.mark.parametrize(
    "client_base, signed_base",
    [
        ("https://api.sirenaffiliates.com/siren/v1", "https://app.sirenaffiliates.com/siren/v1"),
        ("https://app.sirenaffiliates.com/siren/v1", "https://api.sirenaffiliates.com/siren/v1"),
        ("http://localhost:8080/siren/v1", "http://localhost:8080/siren/v1"),
    ],
)
def test_the_same_siren_passes(client_base, signed_base):
    errors: List[Any] = []
    answers = resolver(siren.Siren(api_key="sk_test", base_url=client_base), errors)

    result = answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET, base_url=signed_base))

    assert (result.status, result.answer, errors) == (200, {"outcome": "allow"}, [])


@pytest.mark.parametrize(
    "client_base, signed_base",
    [
        ("https://api.sirenaffiliates.com/siren/v1", "http://app.sirenaffiliates.com/siren/v1"),
        ("http://localhost:8080/siren/v1", "http://localhost:8081/siren/v1"),
        ("https://api.sirenaffiliates.com/siren/v1", "https://evil.example/siren/v1"),
    ],
)
def test_another_scheme_port_or_host_fails(client_base, signed_base):
    errors: List[Any] = []
    answers = resolver(siren.Siren(api_key="sk_test", base_url=client_base), errors)

    result = answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET, base_url=signed_base))

    assert result.status == 500 and len(errors) == 1


def test_an_answer_for_the_app_host_is_posted_to_the_clients_api_host():
    """Siren Cloud's two hosts reach one service, so an ask signed with an
    answerUrl on app.sirenaffiliates.com passes for a client on
    api.sirenaffiliates.com, and the answer goes to the client's base."""
    with respx.mock(base_url="https://api.sirenaffiliates.com/siren/v1", assert_all_called=False) as network:
        request = testing.app_request("metricFilter", "refund-check", secret=SECRET, base_url="https://app.sirenaffiliates.com/siren/v1")
        route = network.post(f"/apps/requests/{json.loads(request.raw_body)['id']}/answer").respond(200, json={"status": "accepted"})
        answers = resolver(siren.Siren(api_key="sk_test"), [])

        reply = answers(request.raw_body, request.headers)
        assert reply.status == 200 and reply.after is not None
        reply.after()

        assert route.call_count == 1
        assert str(route.calls[0].request.url).startswith("https://api.sirenaffiliates.com/")


def test_a_tell_has_no_answer_address_to_check():
    seen: List[str] = []
    answers = siren.Siren(api_key="sk_test", base_url="http://localhost:8080/siren/v1").apps.request_handler(SECRET)
    answers.on("postConversionAction", "tell-the-crm", lambda request: seen.append(request.id))

    result = answers.simulate(testing.app_request("postConversionAction", "tell-the-crm", secret=SECRET))

    assert result.status == 200 and len(seen) == 1
