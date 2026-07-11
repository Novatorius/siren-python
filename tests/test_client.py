"""Request construction: auth header, base URL, timeouts, and retry policy."""

from __future__ import annotations

import httpx
import pytest
import respx

import siren
from tests.conftest import API_KEY, BASE_URL


class TestRequestConstruction:
    def test_authorization_bearer_header_and_default_base_url(self, client, mocked_api):
        route = mocked_api.post("/event/sale").respond(200, headers={"X-Siren-OID": "7"})

        client.events.sale(
            source="stripe", external_id="ord_1", total=9.99, tracking_id=42
        )

        assert route.called
        request = route.calls.last.request
        assert str(request.url) == f"{BASE_URL}/event/sale"
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["User-Agent"] == f"siren-python/{siren.__version__}"

    @respx.mock(base_url="http://localhost:8080/siren/v1")
    def test_custom_base_url(self, respx_mock):
        route = respx_mock.post("/event/sale").respond(200)
        client = siren.Siren(api_key=API_KEY, base_url="http://localhost:8080/siren/v1/")

        client.events.sale(source="woo", external_id="1", total=1.0, tracking_id=1)

        assert str(route.calls.last.request.url) == (
            "http://localhost:8080/siren/v1/event/sale"
        )

    def test_api_key_required(self):
        with pytest.raises(ValueError):
            siren.Siren(api_key="")

    def test_default_configuration(self, client):
        assert client.base_url == BASE_URL
        assert client._client.max_retries == 2
        assert client._client._client.timeout == httpx.Timeout(30.0)


class TestRetryPolicy:
    def test_retries_5xx_on_idempotent_read_then_succeeds(self, mocked_api, no_sleep):
        route = mocked_api.get("/conversions")
        route.side_effect = [
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(200, json=[]),
        ]
        client = siren.Siren(api_key=API_KEY, max_retries=2)

        page = client.conversions.list()

        assert route.call_count == 2
        assert page.data == []
        assert no_sleep == [0.5]  # first backoff step

    def test_retries_network_error_on_event_ingestion(self, mocked_api, no_sleep):
        route = mocked_api.post("/event/sale")
        route.side_effect = [
            httpx.ConnectError("connection refused"),
            httpx.Response(200, headers={"X-Siren-OID": "11"}),
        ]
        client = siren.Siren(api_key=API_KEY, max_retries=1)

        result = client.events.sale(
            source="stripe", external_id="ord_2", total=5.0, tracking_id=9
        )

        assert route.call_count == 2
        assert result.opportunity_id == 11

    def test_rate_limit_honors_retry_after(self, mocked_api, no_sleep):
        route = mocked_api.get("/payouts")
        route.side_effect = [
            httpx.Response(
                429,
                headers={"Retry-After": "3"},
                json={"error": {"message": "slow down", "code": "rate_limited"}},
            ),
            httpx.Response(200, json=[]),
        ]
        client = siren.Siren(api_key=API_KEY, max_retries=1)

        client.payouts.list()

        assert route.call_count == 2
        assert no_sleep == [3.0]

    def test_exhausted_retries_raise_last_error(self, mocked_api, no_sleep):
        mocked_api.get("/transactions").respond(503, json={"error": {"message": "down"}})
        client = siren.Siren(api_key=API_KEY, max_retries=2)

        with pytest.raises(siren.ApiError):
            client.transactions.list()

        assert mocked_api.calls.call_count == 3  # initial + 2 retries
        assert no_sleep == [0.5, 1.0]  # exponential backoff

    def test_non_retryable_status_fails_immediately(self, client, mocked_api, no_sleep):
        route = mocked_api.get("/conversions").respond(
            401, json={"error": {"message": "bad key"}}
        )

        with pytest.raises(siren.AuthenticationError):
            client.conversions.list()

        assert route.call_count == 1
        assert no_sleep == []

    def test_no_auto_retry_on_create_subscription(self, mocked_api, no_sleep):
        route = mocked_api.post("/webhooks").respond(
            500, json={"error": {"message": "boom"}}
        )
        client = siren.Siren(api_key=API_KEY, max_retries=3)

        with pytest.raises(siren.ApiError):
            client.webhooks.subscriptions.create(
                target_url="https://example.com/hook", events=["*"]
            )

        assert route.call_count == 1
        assert no_sleep == []

    def test_no_auto_retry_on_create_api_key(self, mocked_api, no_sleep):
        route = mocked_api.post("/api-keys").respond(
            502, json={"error": {"message": "bad gateway"}}
        )
        client = siren.Siren(api_key=API_KEY, max_retries=3)

        with pytest.raises(siren.ApiError):
            client.api_keys.create(label="Production server")

        assert route.call_count == 1
        assert no_sleep == []

    def test_network_failure_raises_connection_error(self, mocked_api, no_sleep):
        mocked_api.get("/conversions").side_effect = httpx.ConnectError("dns failure")
        client = siren.Siren(api_key=API_KEY, max_retries=1)

        with pytest.raises(siren.ConnectionError):
            client.conversions.list()

        assert mocked_api.calls.call_count == 2  # initial + 1 retry
