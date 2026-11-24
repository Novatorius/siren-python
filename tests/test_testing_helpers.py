"""``siren.testing``: a signed request for any kind, each kind's sample
question, and the handler's ``simulate()``, which runs a request through the
handler without posting the answer. The README's own sample tests sign with
``app_request()`` too, so the helper and the samples can't drift."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest
import respx

import siren
from siren import testing

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = json.loads((ROOT / "tests" / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
SECRET = "cd" * 32
TELLS = {"postConversionAction", "postAllocationAction"}


def test_the_shipped_sample_data_is_sirens_fixture_for_every_kind():
    assert sorted(testing.KINDS) == sorted(CONTRACTS["kinds"])
    for kind, entry in CONTRACTS["kinds"].items():
        assert testing.sample_data(kind) == entry["request"]["data"], kind


def test_sample_data_hands_out_a_copy():
    first = testing.sample_data("distributionResolver")
    first["pool"]["value"] = 1
    assert testing.sample_data("distributionResolver")["pool"]["value"] == 10000


def test_sample_data_refuses_a_kind_siren_does_not_send():
    with pytest.raises(ValueError, match="distributionResolver"):
        testing.sample_data("customEvent")


def test_the_sample_data_ships_inside_the_package():
    import siren as package

    assert (Path(package.__file__).parent / "_samples.json").is_file()


@pytest.mark.parametrize("kind", sorted(CONTRACTS["kinds"]))
def test_app_request_signs_a_request_siren_would_send(kind):
    request = testing.app_request(kind, "my-feature", secret=SECRET)

    assert request.method == "POST"
    body = json.loads(request.raw_body)
    t = int(request.headers["X-Siren-Timestamp"])
    v1 = hmac.new(SECRET.encode(), f"{t}.".encode() + request.raw_body, hashlib.sha256).hexdigest()
    assert request.headers["X-Siren-Signature-V2"] == f"t={t},v1={v1}"
    assert request.headers["X-Siren-Event"] == "app.request"
    assert body["feature"]["kind"] == kind and body["feature"]["key"] == "my-feature"
    assert body["mode"] == ("tell" if kind in TELLS else "ask")
    assert body["data"] == CONTRACTS["kinds"][kind]["request"]["data"]
    assert re.fullmatch(r"apr_[0-9a-f]{32}", body["id"])
    if kind in TELLS:
        assert "answerUrl" not in body and "answerBy" not in body
    else:
        assert body["answerUrl"] == f"https://api.sirenaffiliates.com/siren/v1/apps/requests/{body['id']}/answer"
    # The SDK's own check takes it.
    checked = siren.Siren(api_key="sk_test").apps.verify_request(request.raw_body, request.headers, SECRET)
    assert checked.feature == body["feature"]


def test_app_request_takes_data_an_id_and_an_address():
    request = testing.app_request(
        "distributionResolver",
        "split-by-score",
        secret=SECRET,
        data={"pool": {"value": 5}},
        id="apr_" + "a" * 32,
        app="acme-crm",
        base_url="http://localhost:8080/siren/v1/",
        timestamp=1794918003,
    )
    body = json.loads(request.raw_body)
    assert body["data"] == {"pool": {"value": 5}}
    assert body["id"] == "apr_" + "a" * 32 and body["app"] == "acme-crm"
    assert body["answerUrl"] == "http://localhost:8080/siren/v1/apps/requests/apr_" + "a" * 32 + "/answer"
    assert body["createdAt"] == "2026-11-17T12:20:03+00:00" and body["answerBy"] == "2026-11-17T13:20:03+00:00"
    assert request.headers["X-Siren-Timestamp"] == "1794918003"


def test_app_request_reads_the_secret_from_the_environment(monkeypatch):
    monkeypatch.setenv("SIREN_SIGNING_SECRET", SECRET)
    request = testing.app_request("metricFilter", "refund-check")
    siren.Siren(api_key="sk_test").apps.verify_request(request.raw_body, request.headers, SECRET)


def test_app_request_without_a_secret_says_where_to_put_one(monkeypatch):
    monkeypatch.delenv("SIREN_SIGNING_SECRET", raising=False)
    with pytest.raises(ValueError, match="SIREN_SIGNING_SECRET"):
        testing.app_request("metricFilter", "refund-check")


def test_app_request_refuses_a_bad_id():
    with pytest.raises(ValueError, match="apr_"):
        testing.app_request("metricFilter", "refund-check", secret=SECRET, id="apr_x1")


def test_simulate_runs_an_ask_and_hands_back_the_answer_without_posting(monkeypatch):
    monkeypatch.delenv("SIREN_SIGNING_SECRET", raising=False)
    with respx.mock(assert_all_called=False) as network:
        client = siren.Siren(api_key="sk_test")
        answers = client.apps.request_handler(SECRET)

        @answers.on("distributionResolver", "split-by-score")
        def split(request):
            pool = request.data["pool"]
            shares = client.apps.split_pool(pool, {m["metricId"]: m["score"] for m in request.data["metrics"]})
            return {"outcome": "allocated", "units": pool["units"], "shares": shares}

        result = answers.simulate(testing.app_request("distributionResolver", "split-by-score", secret=SECRET))

        assert (result.status, result.body) == (200, {})
        assert result.answer == {"outcome": "allocated", "units": "USD", "shares": {"41": 6000, "43": 4000}}
        assert network.calls.call_count == 0


def test_simulate_runs_a_tell_and_says_the_status():
    seen = []
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)
    answers.on("postConversionAction", "tell-the-crm", lambda request: seen.append(request.id))

    result = answers.simulate(testing.app_request("postConversionAction", "tell-the-crm", secret=SECRET, id="apr_" + "b" * 32))

    assert (result.status, result.answer) == (200, None) and seen == ["apr_" + "b" * 32]


def test_simulate_shows_the_reply_for_a_request_the_handler_refuses():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)

    unhandled = answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET))
    wrong_secret = answers.simulate(testing.app_request("metricFilter", "refund-check", secret="ef" * 32))

    assert (unhandled.status, unhandled.body) == (404, {"error": "No function handles metricFilter refund-check."})
    assert wrong_secret.status == 401


def test_simulate_returns_what_the_function_raised_instead_of_raising():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)

    @answers.on("metricFilter", "refund-check")
    def broken(request):
        raise RuntimeError("the refund lookup is down")

    result = answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET))

    assert (result.status, result.answer) == (200, None)
    assert isinstance(result.error, RuntimeError) and str(result.error) == "the refund lookup is down"


def test_simulate_returns_a_tells_failure_with_its_500():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)

    @answers.on("postConversionAction", "tell-the-crm")
    def broken(request):
        raise RuntimeError("the crm is down")

    result = answers.simulate(testing.app_request("postConversionAction", "tell-the-crm", secret=SECRET))

    assert result.status == 500
    assert result.body == {"error": "The function for postConversionAction tell-the-crm failed, so Siren will send the request again."}
    assert isinstance(result.error, RuntimeError)


def test_simulate_returns_an_answer_the_handler_would_not_post():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)
    answers.on("metricFilter", "refund-check", lambda request: {"allow": True})

    result = answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET))

    assert result.answer is None
    assert isinstance(result.error, ValueError) and "outcome" in str(result.error)


def test_a_refused_request_has_no_error_in_the_result():
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)

    assert answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET)).error is None


@pytest.mark.parametrize("bad", [None, "POST /siren", {"raw_body": b"{}"}])
def test_simulate_still_raises_for_something_that_is_not_a_request(bad):
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)

    with pytest.raises(TypeError, match="siren.testing.app_request"):
        answers.simulate(bad)
