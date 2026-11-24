"""The README's handler samples, one per feature kind, run as written.

The README's first sample (``handler-flask``) is a whole Flask app with the
post-conversion action's function. Each kind's section has its own function
(``fn-<kind>``), the question Siren sends (``question-<kind>``) and, for an
ask, the answer the function gives (``answer-<kind>``). Each test puts the
kind's function in place of the first sample's, runs the app on 127.0.0.1,
and plays Siren: it signs Siren's own request for the kind
(tests/fixtures/apps-feature-contracts/requests.json, a copy of Siren's
tests/Fixtures file, which Siren's builders and feature page are held to)
with a test secret through ``siren.testing.app_request()`` and delivers it
over HTTP. A tell gets its 200 once the
function returns. An ask gets its 200 before anything is posted, then the
fake Siren takes the posted answer, which must equal the README's answer and
keep the kind's rules. The README's question must equal the fixture's.

Only what a sample leaves to the developer is stubbed: ``add_to_crm``,
``note_share``, ``is_refunded``, ``is_staff`` and ``pay_once``, and the two
environment variables."""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, get_args
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
import pytest

import siren
from siren import testing
from siren._client import DEFAULT_BASE_URL

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
CONTRACTS = json.loads((ROOT / "tests" / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
SECRET = "cd" * 32
APP_KEY = "sk_test_fake_app_key"

KINDS = [
    "postConversionAction",
    "postAllocationAction",
    "distributionResolver",
    "incentiveResolver",
    "incentiveType",
    "metricCalculation",
    "metricFilter",
    "transactionFilter",
    "engagementCalculation",
    "engagementFilter",
    "programGroupSorter",
    "collaboratorGroupStructure",
    "payoutMethod",
]
TELLS = {"postConversionAction", "postAllocationAction"}


def snippet(name: str, language: str = "python") -> str:
    found = re.search(r"<!-- readme-test: " + re.escape(name) + r" -->\n```" + language + r"\n(.*?)```", README, re.S)
    assert found, f"README has no {language} snippet named {name}"
    return found.group(1)


FIRST = snippet("handler-flask")
FIRST_FUNCTION = FIRST[FIRST.index("@answers.on(") : FIRST.index("app = Flask")]


def sample(kind: str) -> str:
    """The first sample with the kind's function in place of its own."""
    return FIRST.replace(FIRST_FUNCTION, snippet(f"fn-{kind}") + "\n")


def test_every_kind_siren_offers_has_a_section():
    assert sorted(KINDS) == sorted(CONTRACTS["kinds"])
    assert {kind.value for kind in siren.FeatureKind} - set(KINDS) == {"customEvent"}


def test_the_first_sample_holds_the_post_conversion_function():
    assert FIRST_FUNCTION == snippet("fn-postConversionAction") + "\n"


@pytest.mark.parametrize("kind", KINDS)
def test_each_sample_is_ten_to_sixteen_lines(kind):
    assert 10 <= len(sample(kind).splitlines()) <= 16


@pytest.mark.parametrize("kind", KINDS)
def test_the_readme_question_is_what_siren_sends(kind):
    assert json.loads(snippet(f"question-{kind}", "json")) == CONTRACTS["kinds"][kind]["request"]["data"]


@pytest.fixture(autouse=True)
def flask_installed():
    pytest.importorskip("flask")


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass


def signed_delivery(kind: str):
    """Siren's own request for the kind, signed now with the test secret by
    ``siren.testing.app_request()``, the helper the README's "Try it without
    Siren" teaches, so the helper and the samples can't drift."""
    request = CONTRACTS["kinds"][kind]["request"]
    signed = testing.app_request(
        kind,
        request["feature"]["key"],
        data=request["data"],
        secret=SECRET,
        id=CONTRACTS["requestKey"],
        app=CONTRACTS["app"],
    )
    return signed.raw_body, signed.headers


def keeps_the_rules(kind: str, question: Dict[str, Any], answer: Dict[str, Any]) -> None:
    """The rules each kind's section states, which Siren refuses an answer
    for breaking with 422 app_answer_invalid."""

    def whole(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def fields(typed: Any) -> None:
        # An answer with variants is a Union of TypedDicts, one per outcome.
        keys = set().union(*(variant.__annotations__ for variant in get_args(typed) or (typed,)))
        assert set(answer) <= keys, (kind, sorted(answer))

    outcome = answer["outcome"]
    if kind == "distributionResolver":
        fields(siren.DistributionResolverAnswer)
        pool = question["pool"]
        step = 10 ** (pool["currency"]["scale"] - pool["currency"]["decimalPlaces"])
        assert outcome == "allocated" and answer["units"] == pool["units"]
        assert set(answer["shares"]) == {str(m["metricId"]) for m in question["metrics"]}
        assert all(whole(v) and v % step == 0 for v in answer["shares"].values())
        assert sum(answer["shares"].values()) <= pool["value"]
    elif kind == "incentiveResolver":
        fields(siren.IncentiveResolverAnswer)
        pool = question["pool"]
        assert outcome == "rewards" and answer["units"] == pool["units"]
        assert set(answer["rewards"]) == {str(c["conversionId"]) for c in question["conversions"]}
        assert all(v is None or (whole(v) and v <= pool["value"]) for v in answer["rewards"].values())
    elif kind == "incentiveType":
        if outcome == "none":
            assert answer == {"outcome": "none"}
        else:
            fields(siren.IncentiveTypeAwardAnswer)
            ids = answer["engagementIds"]
            assert outcome == "award" and answer["units"] == question["program"]["units"] and whole(answer["pool"])
            assert ids and len(set(ids)) == len(ids) and set(ids) <= {e["engagementId"] for e in question["engagements"]}
    elif kind in ("metricCalculation", "engagementCalculation"):
        if kind == "engagementCalculation":
            fields(siren.EngagementCalculationAnswer)
        results = answer["results"]
        assert outcome == ("scores" if kind == "metricCalculation" else "engagements") and len(results) <= 50
        assert len({r["collaboratorId"] for r in results}) == len(results)
        assert all(whole(r["score"]) for r in results)
    elif kind in ("metricFilter", "engagementFilter"):
        if kind == "engagementFilter":
            fields(siren.EngagementFilterAnswer)
        assert outcome in ("allow", "suppress")
        if outcome == "suppress":
            assert isinstance(answer["reason"], str) and 1 <= len(answer["reason"]) <= 200
    elif kind == "transactionFilter":
        fields(siren.TransactionFilterAnswer)
        include = answer["include"]
        assert outcome == "filtered" and len(set(include)) == len(include)
        assert set(include) <= {line["id"] for line in question["lines"]}
    elif kind == "programGroupSorter":
        fields(siren.ProgramGroupSorterAnswer)
        assert outcome == "sorted" and sorted(answer["programIds"]) == sorted(p["id"] for p in question["programs"])
    elif kind == "collaboratorGroupStructure":
        fields(siren.CollaboratorGroupStructureChainAnswer if answer["shape"] == "chain" else siren.CollaboratorGroupStructureTreeAnswer)
        members = sorted(m["collaboratorId"] for m in question["members"])
        assert outcome == "structure"
        if answer["shape"] == "chain":
            assert sorted(answer["order"]) == members
        else:
            assert sorted(int(k) for k in answer["parents"]) == members
    elif kind == "payoutMethod":
        if outcome == "paid":
            assert set(answer) == {"outcome", "reference"} and 1 <= len(answer["reference"]) <= 191
        else:
            assert outcome == "failed" and set(answer) == {"outcome", "reason"} and len(answer["reason"]) <= 500
    else:  # pragma: no cover
        raise AssertionError(f"no rules for {kind}")


@pytest.mark.parametrize("kind", KINDS)
def test_each_sample_answers_siren_as_its_kind_needs(kind, monkeypatch):
    import respx

    monkeypatch.setenv("SIREN_API_KEY", APP_KEY)
    monkeypatch.setenv("SIREN_SIGNING_SECRET", SECRET)
    replied, answered = threading.Event(), threading.Event()
    posted: List[Dict[str, Any]] = []
    stubbed: List[tuple] = []

    def answer_route(request: httpx.Request) -> httpx.Response:
        # Siren gets the answer only after the app replied 200. If the app
        # posted before replying, this would wait out and fail.
        assert replied.wait(5), "the answer was posted before the 200 went back"
        posted.append({"auth": request.headers["Authorization"], "body": json.loads(request.content)})
        answered.set()
        return httpx.Response(200, json={"status": "accepted"})

    def stub(name: str, result: Any = None):
        def stand_in(*args: Any) -> Any:
            stubbed.append((name,) + args)
            return result

        return stand_in

    # The first sample sets no base_url, so its answers go to Siren Cloud's address.
    with respx.mock(base_url=DEFAULT_BASE_URL, assert_all_called=False) as siren_api:
        siren_api.post(f"/apps/requests/{CONTRACTS['requestKey']}/answer").mock(side_effect=answer_route)
        scope: Dict[str, Any] = {
            "__name__": "sample",
            "add_to_crm": stub("add_to_crm"),
            "note_share": stub("note_share"),
            "is_refunded": stub("is_refunded", True),
            "is_staff": stub("is_staff", True),
            "pay_once": stub("pay_once", {"ok": True, "id": "acme-ledger-5521"}),
        }
        exec(compile(sample(kind), f"README sample for {kind}", "exec"), scope)
        server = make_server("127.0.0.1", 0, scope["app"], handler_class=Quiet)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body, headers = signed_delivery(kind)
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/siren", data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                status, reply = response.status, json.loads(response.read())
            replied.set()

            assert (status, reply) == (200, {})
            data = CONTRACTS["kinds"][kind]["request"]["data"]
            if kind in TELLS:
                if kind == "postConversionAction":
                    assert stubbed == [("add_to_crm", CONTRACTS["requestKey"], data["conversion"], data["reward"])]
                else:
                    assert stubbed == [("note_share", CONTRACTS["requestKey"], data["allocation"])]
                assert posted == []
            else:
                assert answered.wait(5)
                expected = json.loads(snippet(f"answer-{kind}", "json"))
                assert posted == [{"auth": f"Bearer {APP_KEY}", "body": expected}]
                keeps_the_rules(kind, data, expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


@pytest.mark.parametrize("kind", sorted(set(KINDS) - TELLS - {"metricCalculation", "engagementCalculation"}))
def test_the_readme_answer_is_the_one_siren_and_its_screens_show(kind):
    """Siren's feature page shows the fixture's answer. The two calculation
    samples score only the collaborator who triggered, so their answers are
    held by the run above instead."""
    assert json.loads(snippet(f"answer-{kind}", "json")) == CONTRACTS["kinds"][kind]["answer"]
