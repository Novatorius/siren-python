"""API key scopes, ledger and webhook access with a key, and Siren's refusals."""

from __future__ import annotations

import json

import pytest

import siren
from siren import ApiKeyScope
from tests.conftest import API_KEY


def _coded(message, code, context=None):
    return {"error": {"message": message, "code": code, "context": context if context is not None else []}}


def _no_retry_client() -> siren.Siren:
    return siren.Siren(api_key=API_KEY, max_retries=0)


class TestScopes:
    def test_scope_values_are_stable(self):
        # Siren grants these only by their exact names, so the values are a
        # contract. Changing one would mint keys that open nothing.
        assert {scope.name: scope.value for scope in ApiKeyScope} == {
            "ALL_EVENTS": "event:*",
            "FULL_ACCESS": "*",
            "SALE": "event:sale",
            "REFUND": "event:refund",
            "SITE_VISITED": "event:site-visited",
            "LEDGER_CONVERSIONS_READ": "ledger:conversions:read",
            "LEDGER_TRANSACTIONS_READ": "ledger:transactions:read",
            "LEDGER_OBLIGATIONS_READ": "ledger:obligations:read",
            "LEDGER_PAYOUTS_READ": "ledger:payouts:read",
            "WEBHOOK_SUBSCRIPTIONS_READ": "webhook-subscriptions:read",
            "WEBHOOK_SUBSCRIPTIONS_MANAGE": "webhook-subscriptions:manage",
        }
        assert [scope.value for scope in siren.LEDGER_READ_SCOPES] == [
            "ledger:conversions:read",
            "ledger:transactions:read",
            "ledger:obligations:read",
            "ledger:payouts:read",
        ]

    def test_event_scope(self):
        assert siren.event_scope("loyalty-points-earned") == "event:loyalty-points-earned"
        with pytest.raises(ValueError):
            siren.event_scope("")

    def test_create_sends_ledger_and_webhook_scopes(self, client, mocked_api):
        route = mocked_api.post("/api-keys").respond(201, json={"id": 5, "rawKey": "sk_live_x"})

        client.api_keys.create(
            label="Accounting export",
            scopes=[
                *siren.LEDGER_READ_SCOPES,
                ApiKeyScope.WEBHOOK_SUBSCRIPTIONS_READ,
                "webhook-subscriptions:manage",
            ],
        )

        assert json.loads(route.calls.last.request.content) == {
            "label": "Accounting export",
            "scopes": [
                "ledger:conversions:read",
                "ledger:transactions:read",
                "ledger:obligations:read",
                "ledger:payouts:read",
                "webhook-subscriptions:read",
                "webhook-subscriptions:manage",
            ],
        }


class TestKeyAccess:
    def test_conversions_with_key_narrowed_to_one_store(self, client, mocked_api):
        route = mocked_api.get("/conversions").respond(200, json={"items": [{"id": 9}], "total": 1})

        page = client.conversions.list(property_id=5, status="approved")

        request = route.calls.last.request
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert dict(request.url.params) == {"propertyId": "5", "status": "approved"}
        assert list(page) == [{"id": 9}]

    def test_property_id_is_left_out_when_not_given(self, client, mocked_api):
        route = mocked_api.get("/payouts").respond(200, json={"items": [], "total": 0})

        client.payouts.list()

        assert "propertyId" not in route.calls.last.request.url.params

    def test_webhook_subscriptions_with_key(self, client, mocked_api):
        create = mocked_api.post("/webhooks").respond(201, json={"id": 4, "signingSecret": "whsec_x"})
        mocked_api.get("/webhooks").respond(200, json=[{"id": 4}])

        client.webhooks.subscriptions.create(target_url="https://example.com/hook", events=["*"])
        assert create.calls.last.request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert [sub.id for sub in client.webhooks.subscriptions.list()] == [4]


class TestRefusals:
    def test_missing_scope_lists_every_scope_that_opens_the_route(self, mocked_api):
        mocked_api.get("/webhooks").respond(
            403,
            json=_coded(
                "This API key does not have the 'webhook-subscriptions:read' scope.",
                "missing_scope",
                {"scope": "webhook-subscriptions:read", "anyOf": ["webhook-subscriptions:read", "webhook-subscriptions:manage"]},
            ),
        )

        with pytest.raises(siren.MissingScopeError) as excinfo:
            _no_retry_client().webhooks.subscriptions.list()

        error = excinfo.value
        assert isinstance(error, siren.PermissionError)
        assert error.code == siren.ErrorCode.MISSING_SCOPE
        assert error.scope == "webhook-subscriptions:read"
        assert error.any_of == ["webhook-subscriptions:read", "webhook-subscriptions:manage"]

    def test_missing_scope_without_a_list(self, mocked_api):
        mocked_api.get("/conversions").respond(403, json=_coded("This older organization key sends events only.", "missing_scope"))

        with pytest.raises(siren.MissingScopeError) as excinfo:
            _no_retry_client().conversions.list()

        assert excinfo.value.any_of == []
        assert excinfo.value.scope is None

    def test_store_key_on_payouts(self, mocked_api):
        mocked_api.get("/payouts").respond(
            403,
            json=_coded(
                "This API key is bound to one store, and these records are not kept by store.",
                "store_key_not_allowed",
                {"propertyId": 5},
            ),
        )

        with pytest.raises(siren.StoreKeyNotAllowedError) as excinfo:
            _no_retry_client().payouts.list()

        assert isinstance(excinfo.value, siren.PermissionError)
        assert excinfo.value.code == siren.ErrorCode.STORE_KEY_NOT_ALLOWED
        assert excinfo.value.property_id == 5

    def test_key_on_a_session_route(self, mocked_api):
        route = mocked_api.get("/api-keys").respond(
            401, json=_coded("This route does not accept API keys.", "api_key_not_accepted")
        )

        with pytest.raises(siren.ApiKeyNotAcceptedError) as excinfo:
            siren.Siren(api_key=API_KEY).api_keys.list()

        assert isinstance(excinfo.value, siren.AuthenticationError)
        assert excinfo.value.status_code == 401
        assert route.call_count == 1

    def test_plain_401_stays_an_authentication_error(self, mocked_api):
        mocked_api.get("/conversions").respond(401, json={"error": {"message": "Invalid API key.", "context": []}})

        with pytest.raises(siren.AuthenticationError) as excinfo:
            _no_retry_client().conversions.list()

        assert type(excinfo.value) is siren.AuthenticationError

    def test_a_code_on_another_status_keeps_the_status_class(self, mocked_api):
        mocked_api.get("/conversions").respond(400, json=_coded("Bad.", "missing_scope"))

        with pytest.raises(siren.BadRequestError):
            _no_retry_client().conversions.list()
