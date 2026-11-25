"""A custom event goes out with a short-lived app token the SDK gets from the
app's key on its own (slice C13, apps-event-tokens, section 4)."""

from __future__ import annotations

import json
import threading

import httpx
import pytest

import siren
from siren import _tokens

from .conftest import API_KEY, BASE_URL


@pytest.fixture
def clock(monkeypatch):
    """A clock the test moves by hand."""
    now = {"t": 1000.0}
    monkeypatch.setattr(_tokens, "_clock", lambda: now["t"])
    return now


def _grant(token, expires_in=1200):
    return httpx.Response(
        200, json={"access_token": token, "token_type": "Bearer", "expires_in": expires_in}
    )


def _oauth_error(status, code, description="Refused."):
    return httpx.Response(status, json={"error": code, "error_description": description})


def _sent():
    return httpx.Response(200, json={"status": "recorded"}, headers={"X-Siren-OID": "77"})


def _calls(mocked_api):
    return [f"{c.request.method} {c.request.url.path}" for c in mocked_api.calls]


def _auth(call):
    return call.request.headers.get("Authorization")


def _json(call):
    return json.loads(call.request.content)


TOKEN_PATH = "POST /siren/v1/oauth/token"


class TestServerSends:
    def test_first_custom_event_trades_the_key_and_sends_with_the_token(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(return_value=_grant("srv.one"))
        send = mocked_api.post("/event/demo-booked").mock(return_value=_sent())

        result = client.events.ingest("demo-booked", {"email": "a@example.com"})

        assert _calls(mocked_api) == [TOKEN_PATH, "POST /siren/v1/event/demo-booked"]
        assert _json(grant.calls.last) == {
            "grant_type": "client_credentials",
            "client_secret": API_KEY,
        }
        assert _auth(grant.calls.last) is None, "The key goes in the body, not as a bearer."
        assert _auth(send.calls.last) == "Bearer srv.one"
        assert _json(send.calls.last) == {"email": "a@example.com"}
        assert result.opportunity_id == 77

    def test_keeps_the_token_for_later_sends(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(return_value=_grant("srv.one"))
        mocked_api.post(url__regex=r"/event/.+").mock(return_value=_sent())

        client.events.ingest("demo-booked", {})
        clock["t"] += 600
        client.events.ingest("demo-booked", {})
        client.events.ingest("trial-started", {})

        assert grant.call_count == 1
        assert _auth(mocked_api.calls.last) == "Bearer srv.one"

    def test_reuses_the_token_with_exactly_two_minutes_left(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(return_value=_grant("srv.one"))
        mocked_api.post("/event/demo-booked").mock(return_value=_sent())

        client.events.ingest("demo-booked", {})
        clock["t"] += 1200 - 120
        client.events.ingest("demo-booked", {})

        assert grant.call_count == 1

    def test_asks_for_a_new_token_when_fewer_than_two_minutes_are_left(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(side_effect=[_grant("srv.one"), _grant("srv.two")])
        mocked_api.post("/event/demo-booked").mock(return_value=_sent())

        client.events.ingest("demo-booked", {})
        clock["t"] += 1200 - 119
        client.events.ingest("demo-booked", {})

        assert grant.call_count == 2
        assert _auth(mocked_api.calls.last) == "Bearer srv.two"

    def test_threads_that_ask_at_once_share_one_request(self, client, mocked_api, clock):
        release = threading.Event()
        entered = threading.Event()

        def slow_grant(request):
            entered.set()
            release.wait(5)
            return _grant("srv.one")

        grant = mocked_api.post("/oauth/token").mock(side_effect=slow_grant)
        mocked_api.post("/event/demo-booked").mock(return_value=_sent())

        errors = []

        def send():
            try:
                client.events.ingest("demo-booked", {})
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=send) for _ in range(8)]
        threads[0].start()
        assert entered.wait(5)
        for thread in threads[1:]:
            thread.start()
        threading.Event().wait(0.2)  # let the others reach the lock
        release.set()
        for thread in threads:
            thread.join(5)

        assert errors == []
        assert grant.call_count == 1
        sends = [c for c in mocked_api.calls if c.request.url.path.endswith("/event/demo-booked")]
        assert [_auth(c) for c in sends] == ["Bearer srv.one"] * 8

    def test_a_401_drops_the_token_gets_a_new_one_and_sends_once_more(self, client, mocked_api, clock, no_sleep):
        mocked_api.post("/oauth/token").mock(side_effect=[_grant("srv.revoked"), _grant("srv.fresh")])
        mocked_api.post("/event/demo-booked").mock(
            side_effect=[httpx.Response(401, json={"error": "This access token has expired."}), _sent()]
        )

        result = client.events.ingest("demo-booked", {"n": 1})

        assert _calls(mocked_api) == [
            TOKEN_PATH,
            "POST /siren/v1/event/demo-booked",
            TOKEN_PATH,
            "POST /siren/v1/event/demo-booked",
        ]
        assert _auth(mocked_api.calls.last) == "Bearer srv.fresh"
        assert _json(mocked_api.calls.last) == {"n": 1}
        assert result.http_status == 200
        assert no_sleep == [], "A new token is not a backoff retry."

    def test_a_second_401_surfaces_as_the_usual_authentication_error(self, client, mocked_api, clock):
        mocked_api.post("/oauth/token").mock(side_effect=[_grant("srv.one"), _grant("srv.two")])
        mocked_api.post("/event/demo-booked").mock(
            return_value=httpx.Response(401, json={"error": "This access token has expired."})
        )

        with pytest.raises(siren.AuthenticationError) as raised:
            client.events.ingest("demo-booked", {})

        assert raised.value.status_code == 401
        assert len(mocked_api.calls) == 4, "One retry, not a loop."

    def test_a_dropped_token_is_not_used_again(self, client, mocked_api, clock):
        mocked_api.post("/oauth/token").mock(side_effect=[_grant("srv.one"), _grant("srv.two")])
        mocked_api.post("/event/demo-booked").mock(
            side_effect=[httpx.Response(401, json={"error": "expired"}), _sent(), _sent()]
        )

        client.events.ingest("demo-booked", {})
        client.events.ingest("demo-booked", {})

        assert _calls(mocked_api).count(TOKEN_PATH) == 2
        assert _auth(mocked_api.calls.last) == "Bearer srv.two"

    def test_a_key_of_no_app_sends_with_the_key_and_stops_asking(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(
            return_value=_oauth_error(400, "unauthorized_client", "Only an app's key can get an access token.")
        )
        send = mocked_api.post("/event/demo-booked").mock(return_value=_sent())

        client.events.ingest("demo-booked", {})
        client.events.ingest("demo-booked", {})

        assert grant.call_count == 1
        assert [_auth(c) for c in send.calls] == [f"Bearer {API_KEY}"] * 2

    def test_a_key_siren_does_not_know_surfaces_from_the_grant_without_a_send(self, client, mocked_api, clock):
        mocked_api.post("/oauth/token").mock(
            return_value=_oauth_error(401, "invalid_client", "Siren doesn't know this key, or it was revoked.")
        )

        with pytest.raises(siren.AuthenticationError) as raised:
            client.events.ingest("demo-booked", {})

        assert raised.value.code == "invalid_client"
        assert raised.value.message == "Siren doesn't know this key, or it was revoked."
        assert _calls(mocked_api) == [TOKEN_PATH]

    def test_any_other_refused_grant_surfaces_as_it_is(self, client, mocked_api, clock):
        mocked_api.post("/oauth/token").mock(
            return_value=_oauth_error(400, "invalid_request", "client_secret is required.")
        )

        with pytest.raises(siren.BadRequestError, match="client_secret is required."):
            client.events.ingest("demo-booked", {})

    def test_every_other_call_keeps_sending_the_key(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(return_value=_grant("srv.one"))
        mocked_api.post(url__regex=r"/event/.+").mock(return_value=_sent())
        mocked_api.get("/conversions").respond(200, json=[])

        client.events.ingest("demo-booked", {})
        client.events.sale(source="stripe", external_id="cs_1", total=9.99, tracking_id=3)
        client.events.refund(source="stripe", external_id="cs_1")
        client.events.site_visited(collaborator_id=3)
        list(client.conversions.list())

        assert grant.call_count == 1
        for call in mocked_api.calls[2:]:
            assert _auth(call) == f"Bearer {API_KEY}", call.request.url

    def test_a_grant_that_fails_to_connect_is_retried_like_any_read(self, client, mocked_api, clock, no_sleep):
        mocked_api.post("/oauth/token").mock(
            side_effect=[httpx.ConnectError("Connection refused"), _grant("srv.one")]
        )
        mocked_api.post("/event/demo-booked").mock(return_value=_sent())

        client.events.ingest("demo-booked", {})

        assert no_sleep == [0.5]
        assert _auth(mocked_api.calls.last) == "Bearer srv.one"


class TestBrowserToken:
    def test_asks_for_the_events_scope_and_answers_token_seconds_and_address(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(return_value=_grant("brw.one", 600))

        token = client.events.browser_token("demo-booked")

        assert _json(grant.calls.last) == {
            "grant_type": "client_credentials",
            "client_secret": API_KEY,
            "scope": "browser:demo-booked",
        }
        assert token == siren.BrowserToken(token="brw.one", expires_in=600, url=f"{BASE_URL}/event/demo-booked")
        assert token.to_dict() == {"token": "brw.one", "expiresIn": 600, "url": f"{BASE_URL}/event/demo-booked"}

    def test_reused_until_fewer_than_sixty_seconds_are_left(self, client, mocked_api, clock):
        grant = mocked_api.post("/oauth/token").mock(side_effect=[_grant("brw.one", 600), _grant("brw.two", 600)])

        client.events.browser_token("demo-booked")
        clock["t"] += 540
        held = client.events.browser_token("demo-booked")
        clock["t"] += 1
        fresh = client.events.browser_token("demo-booked")

        assert (held.token, held.expires_in) == ("brw.one", 60)
        assert (fresh.token, fresh.expires_in) == ("brw.two", 600)
        assert grant.call_count == 2

    def test_each_event_gets_its_own_token_apart_from_the_server_token(self, client, mocked_api, clock):
        mocked_api.post("/oauth/token").mock(
            side_effect=[_grant("brw.a", 600), _grant("brw.b", 600), _grant("srv.one")]
        )
        mocked_api.post("/event/demo-booked").mock(return_value=_sent())

        a = client.events.browser_token("demo-booked")
        b = client.events.browser_token("trial started")
        client.events.ingest("demo-booked", {})

        assert (a.token, b.token) == ("brw.a", "brw.b")
        assert b.url == f"{BASE_URL}/event/trial%20started"
        assert _auth(mocked_api.calls.last) == "Bearer srv.one"
        assert _json(mocked_api.calls[2]) == {"grant_type": "client_credentials", "client_secret": API_KEY}

    def test_surfaces_sirens_refusal(self, client, mocked_api, clock):
        mocked_api.post("/oauth/token").mock(
            return_value=_oauth_error(400, "invalid_scope", 'The event "demo-booked" allows no domain.')
        )

        with pytest.raises(siren.BadRequestError) as raised:
            client.events.browser_token("demo-booked")

        assert raised.value.code == "invalid_scope"
        assert raised.value.message == 'The event "demo-booked" allows no domain.'

    def test_refuses_an_empty_event_key(self, client):
        with pytest.raises(ValueError):
            client.events.browser_token("")
