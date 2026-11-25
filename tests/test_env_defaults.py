"""The client reads ``SIREN_API_KEY`` and the request handler reads
``SIREN_SIGNING_SECRET`` when none is passed. A value passed in wins, and a
missing one still refuses, naming the argument and the variable."""

from __future__ import annotations

import pytest

import siren
from siren import testing

SECRET = "cd" * 32


def test_the_client_reads_the_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("SIREN_API_KEY", "sk_test_from_env")
    assert siren.Siren()._client.api_key == "sk_test_from_env"


def test_a_key_passed_in_wins(monkeypatch):
    monkeypatch.setenv("SIREN_API_KEY", "sk_test_from_env")
    assert siren.Siren(api_key="sk_test_passed")._client.api_key == "sk_test_passed"


def test_no_key_names_the_argument_and_the_variable(monkeypatch):
    monkeypatch.delenv("SIREN_API_KEY", raising=False)
    with pytest.raises(ValueError, match=r"api_key.*SIREN_API_KEY"):
        siren.Siren()


def test_the_handler_reads_the_secret_from_the_environment(monkeypatch):
    monkeypatch.setenv("SIREN_SIGNING_SECRET", SECRET)
    answers = siren.Siren(api_key="sk_test").apps.request_handler()
    answers.on("metricFilter", "refund-check", lambda request: {"outcome": "allow"})

    assert answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET)).status == 200


def test_a_secret_passed_in_wins(monkeypatch):
    monkeypatch.setenv("SIREN_SIGNING_SECRET", "ef" * 32)
    answers = siren.Siren(api_key="sk_test").apps.request_handler(SECRET)
    answers.on("metricFilter", "refund-check", lambda request: {"outcome": "allow"})

    assert answers.simulate(testing.app_request("metricFilter", "refund-check", secret=SECRET)).status == 200


def test_no_secret_names_the_argument_and_the_variable(monkeypatch):
    monkeypatch.delenv("SIREN_SIGNING_SECRET", raising=False)
    with pytest.raises(siren.AppHandlerError, match=r"signing_secret.*SIREN_SIGNING_SECRET"):
        siren.Siren(api_key="sk_test").apps.request_handler()


# SIREN_BASE_URL: `siren apps apply` writes it to .env.sandbox beside the
# key, and a sandbox ask's answerUrl is on the sandbox host.

SANDBOX = "https://sandbox.siren.test/siren/v1"


def test_the_client_reads_the_base_url_from_the_environment(monkeypatch):
    monkeypatch.setenv("SIREN_BASE_URL", SANDBOX)
    assert siren.Siren(api_key="sk_test").base_url == SANDBOX


def test_a_base_url_passed_in_wins(monkeypatch):
    monkeypatch.setenv("SIREN_BASE_URL", SANDBOX)
    assert siren.Siren(api_key="sk_test", base_url="http://localhost:8080/siren/v1").base_url == "http://localhost:8080/siren/v1"


def test_no_base_url_is_siren_clouds(monkeypatch):
    monkeypatch.delenv("SIREN_BASE_URL", raising=False)
    assert siren.Siren(api_key="sk_test").base_url == "https://api.sirenaffiliates.com/siren/v1"
    monkeypatch.setenv("SIREN_BASE_URL", "")
    assert siren.Siren(api_key="sk_test").base_url == "https://api.sirenaffiliates.com/siren/v1"


def test_a_sandbox_ask_is_answered_at_the_sandbox(monkeypatch):
    monkeypatch.setenv("SIREN_BASE_URL", SANDBOX)
    monkeypatch.setenv("SIREN_SIGNING_SECRET", SECRET)
    answers = siren.Siren(api_key="sk_test").apps.request_handler()
    answers.on("metricFilter", "refund-check", lambda request: {"outcome": "allow"})
    # The testing helper signs for the same host by default, as a sandbox does.
    request = testing.app_request("metricFilter", "refund-check")
    assert request.body["answerUrl"].startswith(SANDBOX + "/apps/requests/")
    result = answers.simulate(request)
    assert (result.status, result.error) == (200, None)


def test_the_readme_says_apply_writes_the_base_url_the_client_reads():
    from pathlib import Path

    readme = " ".join((Path(__file__).resolve().parent.parent / "README.md").read_text().split())
    local = readme[readme.index("## Develop on your machine"):readme.index("## Feature kinds")]
    assert "SIREN_BASE_URL" in local
    assert "reads `SIREN_BASE_URL`" in readme
