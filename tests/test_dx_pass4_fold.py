"""The pass 4 re-check, folded in with the dead-method removal
(review/sdk-dx/pass-4-recheck-fold-spec.md and section 3 of
pass-4-followups-spec.md in the run folder).

- Every README sample that writes, pays or calls another system returns
  early on a test request before it does.
- "Develop on your machine" gives the steps that work on the first run:
  apply writes the key, listen writes the secret, the app starts with the
  file loaded, and trigger sends a sample. Nothing reads ``.env.sandbox`` by
  itself.
- Replay reads a request saved with ``siren apps requests <id> --json``.
- ``api_keys`` and ``webhooks.subscriptions.create`` are gone, since Siren
  serves neither route on d1661413a3. Keys and scopes live on the app:
  ``apps.scopes()``, ``create_key()``, ``rotate_key()`` and ``revoke_key()``.
"""

from __future__ import annotations

import builtins
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

import siren
from siren import testing
from siren.apps import Apps


ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
CHANGELOG = (ROOT / "CHANGELOG.md").read_text()
BLOCK = re.compile(r"(?:<!-- readme-test: ([\w-]+) -->\n)?```python\n(.*?)```", re.S)


def section(heading: str) -> str:
    start = README.index(heading)
    rest = README[start + len(heading):]
    end = re.search(r"^#{2,4} ", rest, re.M)
    return rest[: end.start()] if end else rest


def words(text: str) -> str:
    return " ".join(text.split())


# Must 1: a sample that writes checks request.test first ----------------------------

# Each README sample whose function writes a record, pays, or calls another
# system, with the call that does it. A new writing sample goes here.
WRITING_SAMPLES: Dict[str, str] = {
    "handler-flask": "add_to_crm",
    "fn-postConversionAction": "add_to_crm",
    "fn-postAllocationAction": "note_share",
    "fn-payoutMethod": "pay_once",
}

# Calls a handler sample makes that write nothing: the SDK's own, Flask's,
# a test's list, and the two lookups the filter samples leave to the app.
READ_ONLY: Set[str] = {
    "siren.Siren", "client.apps.request_handler", "client.apps.verify_request", "client.apps.split_pool",
    "client.apps.requests.answer", "answers.on", "answers.on_event", "answers.later", "answers.simulate",
    "webhook_event", "sample_event_data", "Flask", "app.add_url_rule", "approved.append",
    "is_refunded", "is_staff",
}

HANDLER_SAMPLE = re.compile(r"answers\.on(?:_event)?\(|\.verify_request\(")
CALL = re.compile(r"(?<![\w.])([A-Za-z_][\w.]*)\(")


def handler_samples() -> List[Tuple[str, str]]:
    found = []
    for match in BLOCK.finditer(README):
        source = re.sub(r"#[^\n]*", "", match.group(2))  # comments name calls they don't make
        if HANDLER_SAMPLE.search(source):
            found.append((match.group(1), source))
    return found


def calls(source: str) -> List[Tuple[int, str]]:
    defined = set(re.findall(r"def (\w+)\(", source))
    return [
        (m.start(), m.group(1))
        for m in CALL.finditer(source)
        if m.group(1) not in defined and not hasattr(builtins, m.group(1))
    ]


def test_every_handler_sample_calls_only_what_the_lists_know():
    unknown = {
        name: sorted({call for _, call in calls(source)} - READ_ONLY - set(WRITING_SAMPLES.values()))
        for name, source in handler_samples()
    }
    assert {name: found for name, found in unknown.items() if found} == {}, (
        "A README handler sample calls a function the docs test doesn't know. "
        "Add the sample to WRITING_SAMPLES if it writes, or the call to READ_ONLY."
    )


def test_a_sample_that_writes_is_listed():
    writers = set(WRITING_SAMPLES.values())
    unlisted = [name for name, source in handler_samples() if any(call in writers for _, call in calls(source)) and name not in WRITING_SAMPLES]
    assert unlisted == []
    assert set(WRITING_SAMPLES) <= {name for name, _ in handler_samples()}


@pytest.mark.parametrize("name", sorted(WRITING_SAMPLES))
def test_a_writing_sample_reads_test_before_it_writes(name):
    source = dict(handler_samples())[name]
    first_write = min(at for at, call in calls(source) if call == WRITING_SAMPLES[name])
    check = re.search(r"if request\.test:", source)
    assert check is not None and check.start() < first_write, f"README {name} writes before it checks request.test"


def test_the_field_list_names_test_beside_is_ask():
    fields = words(section("## Answer Siren in a few lines"))
    assert "`request.is_ask`" in fields and "`request.test`" in fields


# Must 2 and should-fix 5: Develop on your machine ----------------------------------

LOCAL = words(section("## Develop on your machine"))
START = "set -a; . ./.env.sandbox; set +a; flask --app app run --port 3000"


def test_the_steps_run_in_the_order_that_works():
    steps = [
        "siren apps apply",
        "siren listen --forward-to localhost:3000/siren",
        START,
        "siren trigger ask <app> <feature>",
    ]
    places = [LOCAL.find(step) for step in steps]
    assert -1 not in places, dict(zip(steps, places))
    assert places == sorted(places)
    assert "dotenv -f .env.sandbox run" in LOCAL  # python-dotenv reads .env unless told
    assert "restart" in LOCAL.lower() and "new secret" in LOCAL


def test_apply_writes_the_key_and_listen_writes_the_secret():
    apply_step = LOCAL[LOCAL.index("siren apps apply"): LOCAL.index("siren listen --forward-to")]
    assert "SIREN_API_KEY" in apply_step and "SIREN_SIGNING_SECRET" not in apply_step
    listen_step = LOCAL[LOCAL.index("siren listen --forward-to"): LOCAL.index(START)]
    assert "SIREN_SIGNING_SECRET" in listen_step


def test_nothing_claims_to_read_the_env_file_by_itself():
    sentences = re.split(r"(?<=[.!?])\s+", words(README))
    claims = [s for s in sentences if ".env.sandbox" in s and re.search(r"which the handler reads|handler reads|reads `?\.env\.sandbox", s)]
    assert claims == []
    assert "process environment" in LOCAL


def test_the_next_release_note_covers_apply_on_a_sandbox():
    note = next(s for s in re.split(r"(?<=[.!?])\s+", LOCAL) if "next release" in s)
    assert "listen" in note and "trigger" in note and "apply" in note


def test_trigger_on_live_needs_the_flag_and_refuses_a_payout_method():
    for text in (LOCAL, words(section("#### Test requests"))):
        assert "--live" in text and "payout method" in text


# Should-fix 1: replay a saved request --------------------------------------------


def test_replay_reads_a_saved_request_not_the_requests_route():
    replay = section("#### Replay a stored request")
    assert "siren apps requests <id> --json > request.json" in replay
    assert "next release" in words(replay)
    block = re.search(r"<!-- readme-test: replay -->\n```python\n(.*?)```", README, re.S)
    assert block is not None and "requests.retrieve" not in block.group(1) and "request.json" in block.group(1)


# Should-fix 7: a missing replay key is the handler's error -------------------------


def test_a_missing_replay_key_raises_app_handler_error():
    record = {"id": "apr_" + "c" * 32, "kind": "distributionResolver", "mode": "ask", "feature": "app:12", "data": {}}
    with pytest.raises(siren.AppHandlerError, match="needs the feature's key"):
        testing.app_request_from_record(record, secret="ab" * 32)


# Section 3: the dead methods are gone, and keys live on the app -------------------


def test_the_removed_resources_are_gone(client):
    assert not hasattr(client, "api_keys")
    assert not hasattr(client.webhooks.subscriptions, "create")
    assert not hasattr(siren, "ApiKey") and "ApiKey" not in siren.__all__
    with pytest.raises(ImportError):
        __import__("siren.api_keys")
    assert hasattr(client.webhooks.subscriptions, "list") and hasattr(client.webhooks.subscriptions, "delete")


def test_the_retired_webhook_scopes_are_gone():
    names = {scope.name for scope in siren.ApiKeyScope}
    assert "WEBHOOK_SUBSCRIPTIONS_READ" not in names and "WEBHOOK_SUBSCRIPTIONS_MANAGE" not in names
    assert "webhook-subscriptions:read" not in {scope.value for scope in siren.ApiKeyScope}


APP = {
    "id": 7, "key": "acme-payouts", "name": "Acme payouts", "status": "active", "url": "https://acme.test/siren",
    "events": [], "scopes": ["ledger:payouts:read"], "apiKeyPrefix": "sk_live_ab12", "apiKey": None, "address": None,
    "features": [], "needsAttention": 0,
}
SCOPES = {
    "events": [{"scope": "event:*", "resource": "events", "action": "*", "label": "Send every event"}],
    "ledger": [{"scope": "ledger:payouts:read", "resource": "ledger", "action": "payouts:read", "label": "Read payouts"}],
}


def test_scopes_reads_the_app_scopes_route(client, mocked_api):
    route = mocked_api.get("/apps/scopes").respond(200, json=SCOPES)
    assert client.apps.scopes() == SCOPES
    assert route.called


def test_scopes_refuses_a_body_that_is_not_groups_of_scopes(client, mocked_api):
    mocked_api.get("/apps/scopes").respond(200, json=["event:*"])
    with pytest.raises(siren.ApiError, match="GET /apps/scopes"):
        client.apps.scopes()


def test_create_key_posts_to_the_app_key_route_once(client, mocked_api):
    route = mocked_api.post("/apps/acme-payouts/key").respond(201, json=dict(APP, rawKey="sk_live_" + "f" * 64))
    made = client.apps.create_key("acme-payouts", scopes=["ledger:payouts:read"])
    assert made["rawKey"] == "sk_live_" + "f" * 64 and made["key"] == "acme-payouts"
    assert json.loads(route.calls.last.request.content) == {"scopes": ["ledger:payouts:read"]}
    assert route.call_count == 1


def test_create_key_with_no_scopes_sends_an_empty_body(client, mocked_api):
    route = mocked_api.post("/apps/acme-payouts/key").respond(201, json=dict(APP, rawKey="sk_live_" + "f" * 64))
    client.apps.create_key("acme-payouts")
    assert json.loads(route.calls.last.request.content) == {}


def test_create_key_is_never_retried(client, mocked_api):
    route = mocked_api.post("/apps/acme-payouts/key").respond(503)
    with pytest.raises(siren.SirenError):
        client.apps.create_key("acme-payouts")
    assert route.call_count == 1


def test_a_key_answer_with_no_raw_key_says_to_rotate(client, mocked_api):
    mocked_api.post("/apps/acme-payouts/key").respond(201, json=APP)
    with pytest.raises(siren.ApiError, match="rotate_key"):
        client.apps.create_key("acme-payouts")


def test_rotate_key_posts_to_the_rotate_route(client, mocked_api):
    route = mocked_api.post("/apps/acme-payouts/key/rotate").respond(200, json=dict(APP, rawKey="sk_live_" + "e" * 64))
    assert client.apps.rotate_key("acme-payouts")["rawKey"] == "sk_live_" + "e" * 64
    assert route.call_count == 1 and route.calls.last.request.content in (b"", b"{}")


def test_revoke_key_deletes_the_app_key(client, mocked_api):
    route = mocked_api.delete("/apps/acme-payouts/key").respond(200, json=dict(APP, apiKeyPrefix=None, scopes=[]))
    assert client.apps.revoke_key("acme-payouts")["apiKeyPrefix"] is None
    assert route.called


@pytest.mark.parametrize("method", ["create_key", "rotate_key", "revoke_key"])
def test_a_bad_app_key_is_refused_before_sending(client, mocked_api, method):
    with pytest.raises(ValueError, match=f"apps.{method}"):
        getattr(client.apps, method)("Not An App")
    assert not mocked_api.calls


def test_create_key_takes_only_scopes(client, mocked_api):
    with pytest.raises(ValueError):
        client.apps.create_key("acme-payouts", scopes="event:*")  # type: ignore[arg-type]
    assert not mocked_api.calls


@pytest.mark.parametrize("method", ["scopes", "create_key", "rotate_key", "revoke_key"])
def test_each_key_method_says_it_takes_a_signed_in_session(method):
    assert "signed-in" in words(getattr(Apps, method).__doc__ or "")


def test_the_changelog_names_what_replaces_the_removed_methods():
    unreleased = words(CHANGELOG[CHANGELOG.index("## [Unreleased]"): CHANGELOG.index("## [", CHANGELOG.index("## [Unreleased]") + 5)])
    for name in ("api_keys", "webhooks.subscriptions.create", "siren apps apply", "create_key", "rotate_key", "revoke_key",
                 "siren apps rotate-key", "apps.scopes()"):
        assert name in unreleased, name
    assert "The methods stay" not in unreleased


def test_the_openapi_file_promises_no_removed_route():
    spec = (ROOT / "openapi.yaml").read_text()
    assert "/api-keys" not in spec and "createWebhookSubscription" not in spec and "createApiKey" not in spec


def test_the_client_has_no_api_key_used_by_the_api_key_error_docs():
    assert "api_keys" not in (siren.ApiKeyNotAcceptedError.__doc__ or "")



# The file `siren apps requests <id> --json` saves (siren-node
# pkt/apps-cli-dx 9645ffb, src/cli/commands/requests.ts printRequestJson):
# Siren's request detail as it came, plus appKey, featureKey and sends.

SAVED = (Path(__file__).parent / "fixtures" / "apps-cli" / "request.json").read_text()


def test_a_saved_request_replays_with_no_key_passed():
    request = testing.app_request_from_record(SAVED, secret="ab" * 32, timestamp=1_700_000_000)
    body = request.body
    assert body["feature"] == {"kind": "distributionResolver", "key": "split-by-score", "id": "app:12"}
    assert body["app"] == "acme-payouts"
    assert body["id"] == json.loads(SAVED)["id"] and body["data"] == json.loads(SAVED)["data"]


def test_a_saved_request_parsed_replays_the_same():
    from_text = testing.app_request_from_record(SAVED, secret="ab" * 32, timestamp=1_700_000_000).body
    from_dict = testing.app_request_from_record(json.loads(SAVED), secret="ab" * 32, timestamp=1_700_000_000).body
    assert from_text == from_dict


def test_key_is_the_fallback_when_the_app_is_gone():
    gone = dict(json.loads(SAVED), appKey=None, featureKey=None, sends=[])
    body = testing.app_request_from_record(gone, key="split-by-score", secret="ab" * 32).body
    assert body["feature"]["key"] == "split-by-score"
    with pytest.raises(siren.AppHandlerError, match="featureKey is null"):
        testing.app_request_from_record(gone, secret="ab" * 32)
