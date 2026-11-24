"""Alex's ruling, 2026-11-24: the code says "feature" everywhere a developer
looks, as the docs and the screens do. Siren's contract
(contract/feature-rename.md, pkt/apps-feature-rename) renames the envelope's
``item`` to ``feature``, the routes to ``/apps/{app}/features``, and the
error code to ``app_features_need_key_and_address``. No old name stays."""

from __future__ import annotations

import json

import pytest

import siren
from siren import testing

SECRET = "cd" * 32
BASE = "https://api.sirenaffiliates.com/siren/v1"

FEATURE = {
    "id": 31,
    "ref": "app:31",
    "appId": 4,
    "kind": "distributionResolver",
    "key": "split-by-score",
    "name": "Split by score",
    "description": None,
    "settings": {},
    "status": "active",
    "dateCreated": "2026-11-24T09:00:00+00:00",
    "dateModified": "2026-11-24T09:00:00+00:00",
    "eventTypeId": None,
    "uses": 0,
    "usedBy": [],
}


def test_the_kinds_are_feature_kind_and_the_old_name_is_gone():
    assert siren.FeatureKind.DISTRIBUTION_RESOLVER == "distributionResolver"
    assert "FeatureKind" in siren.__all__
    assert not hasattr(siren, "AppItemKind")
    assert "AppItemKind" not in siren.__all__


def test_a_request_carries_its_feature():
    made = testing.app_request("payoutMethod", "acme-ledger", data=testing.sample_data("payoutMethod"), secret=SECRET)
    request = siren.Siren(api_key="sk_test").apps.verify_request(made.raw_body, made.headers, SECRET)
    assert request.feature == {"kind": "payoutMethod", "key": "acme-ledger", "id": "app:1"}
    assert not hasattr(request, "item")


def test_the_test_request_has_sirens_key_order():
    made = testing.app_request("payoutMethod", "acme-ledger", data=testing.sample_data("payoutMethod"), secret=SECRET)
    assert list(json.loads(made.raw_body)) == [
        "version", "id", "type", "mode", "app", "feature", "createdAt", "answerBy", "answerUrl", "data",
    ]


def test_a_body_with_item_and_no_feature_is_not_a_version_1_request():
    body = json.loads(testing.app_request("metricFilter", "refund-check", secret=SECRET).raw_body)
    body["item"] = body.pop("feature")
    request = testing.AppTestRequest.signed(json.dumps(body).encode(), SECRET)
    reply = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)(request.raw_body, request.headers)
    assert (reply.status, reply.body) == (400, {"error": "The request's `feature` must be an object with `kind`, `key` and `id`."})


def test_the_error_code_says_features():
    assert siren.ErrorCode.APP_FEATURES_NEED_KEY_AND_ADDRESS == "app_features_need_key_and_address"
    assert not any("ITEM" in name for name in vars(siren.ErrorCode))


class TestFeatures:
    def test_create_posts_to_the_features_route_once(self, client, mocked_api):
        route = mocked_api.post("/apps/acme-payouts/features").respond(201, json=FEATURE)
        feature = client.apps.features.create(
            "acme-payouts", {"kind": "distributionResolver", "key": "split-by-score", "name": "Split by score"}
        )
        assert feature["key"] == "split-by-score"
        assert json.loads(route.calls.last.request.content) == {
            "kind": "distributionResolver", "key": "split-by-score", "name": "Split by score",
        }

    def test_create_is_never_retried(self, client, mocked_api):
        route = mocked_api.post("/apps/acme-payouts/features").respond(503, json={"error": {"message": "busy"}})
        with pytest.raises(siren.ApiError):
            client.apps.features.create("acme-payouts", {"kind": "payoutMethod", "name": "Ledger"})
        assert route.call_count == 1

    def test_create_refuses_a_field_siren_does_not_read(self, client):
        with pytest.raises(ValueError, match="apps.features.create\\(\\) does not take status"):
            client.apps.features.create("acme-payouts", {"kind": "payoutMethod", "name": "Ledger", "status": "active"})

    @pytest.mark.parametrize("missing", ["kind", "name"])
    def test_create_needs_a_kind_and_a_name(self, client, missing):
        params = {"kind": "payoutMethod", "name": "Ledger"}
        del params[missing]
        with pytest.raises(ValueError, match=f"apps.features.create\\(\\) needs a {missing}"):
            client.apps.features.create("acme-payouts", params)

    def test_create_names_the_features_code_when_the_app_lacks_an_address(self, client, mocked_api):
        mocked_api.post("/apps/acme-payouts/features").respond(
            409,
            json={"error": {
                "code": "app_features_need_key_and_address",
                "message": 'An app with features needs a key and an address, and "acme-payouts" would lack an address. '
                "Siren asks the app at its address, and the app answers with its key.",
                "context": {"reason": "app_features_need_key_and_address", "missing": ["address"]},
            }},
        )
        with pytest.raises(siren.ConflictError) as refused:
            client.apps.features.create("acme-payouts", {"kind": "payoutMethod", "name": "Ledger"})
        assert refused.value.code == siren.ErrorCode.APP_FEATURES_NEED_KEY_AND_ADDRESS

    def test_retrieve_reads_one_feature(self, client, mocked_api):
        mocked_api.get("/apps/acme-payouts/features/31").respond(200, json={**FEATURE, "openRequests": 0})
        assert client.apps.features.retrieve("acme-payouts", 31)["id"] == 31

    def test_update_patches_and_refuses_kind_and_key(self, client, mocked_api):
        route = mocked_api.patch("/apps/acme-payouts/features/31").respond(200, json={**FEATURE, "name": "By score"})
        assert client.apps.features.update("acme-payouts", 31, {"name": "By score"})["name"] == "By score"
        assert json.loads(route.calls.last.request.content) == {"name": "By score"}
        for field in ("kind", "key"):
            with pytest.raises(ValueError, match=f"cannot change {field}. A feature's kind and key never change"):
                client.apps.features.update("acme-payouts", 31, {field: "x"})

    def test_delete_answers_archived_and_the_feature(self, client, mocked_api):
        mocked_api.delete("/apps/acme-payouts/features/31").respond(200, json={"archived": True, "feature": FEATURE})
        removed = client.apps.features.delete("acme-payouts", 31)
        assert removed["archived"] is True and removed["feature"]["id"] == 31

    def test_delete_of_an_unused_feature_answers_no_feature(self, client, mocked_api):
        mocked_api.delete("/apps/acme-payouts/features/31").respond(200, json={"archived": False, "feature": None})
        assert client.apps.features.delete("acme-payouts", 31) == {"archived": False, "feature": None}

    def test_delete_refuses_the_old_body(self, client, mocked_api):
        mocked_api.delete("/apps/acme-payouts/features/31").respond(200, json={"archived": False, "item": None})
        with pytest.raises(siren.SirenError, match="not \\{archived, feature\\}"):
            client.apps.features.delete("acme-payouts", 31)

    def test_a_feature_not_found_raises_not_found(self, client, mocked_api):
        mocked_api.get("/apps/acme-payouts/features/99").respond(404, json={"error": {"message": "Feature not found."}})
        with pytest.raises(siren.NotFoundError, match="Feature not found."):
            client.apps.features.retrieve("acme-payouts", 99)

    @pytest.mark.parametrize("app", ["", "Acme", "acme--payouts", 4])
    def test_a_bad_app_key_is_refused_before_sending(self, client, app):
        with pytest.raises(ValueError, match="app key"):
            client.apps.features.retrieve(app, 31)

    @pytest.mark.parametrize("feature_id", [0, -1, "31", True, 1.5])
    def test_a_bad_id_is_refused_before_sending(self, client, feature_id):
        with pytest.raises(ValueError, match="feature id"):
            client.apps.features.retrieve("acme-payouts", feature_id)

    def test_the_old_routes_are_not_called(self, client, mocked_api):
        old = mocked_api.route(url__regex=r".*/items.*").respond(200, json={})
        mocked_api.get("/apps/acme-payouts/features/31").respond(200, json=FEATURE)
        client.apps.features.retrieve("acme-payouts", 31)
        assert old.call_count == 0
        assert not hasattr(client.apps, "items")
