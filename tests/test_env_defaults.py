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
    with pytest.raises(ValueError, match=r"signing_secret.*SIREN_SIGNING_SECRET"):
        siren.Siren(api_key="sk_test").apps.request_handler()
