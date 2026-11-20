"""The feature page's Python samples (section 5 of the run's design note,
design/sdk-answer-handler.md), copied here as written and run as one Flask
app each on 127.0.0.1. A fake Siren signs Siren's own request for each kind
(tests/fixtures/apps-feature-contracts/requests.json) with a test secret and
delivers it over HTTP. A tell gets its 200 once the function returns. An ask
gets its 200 before anything is posted, then the fake takes the posted
answer, which must equal the fixture's. The README carries each sample word
for word."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = json.loads((ROOT / "tests" / "fixtures" / "apps-feature-contracts" / "requests.json").read_text())
SECRET = "cd" * 32
APP_KEY = "sk_test_fake_app_key"

# Section 5.1, the whole sample. 5.2 to 5.4 change only the function.
SAMPLE_5_1 = '''import os
import siren
from flask import Flask

client = siren.Siren(api_key=os.environ["SIREN_API_KEY"], base_url="https://cloud.example.com/siren/v1")
answers = client.apps.request_handler(os.environ["SIREN_SIGNING_SECRET"])

@answers.on("postConversionAction", "tell-the-crm")
def tell_the_crm(request):
    # Siren may send it twice, so add it once, keyed by request.id.
    add_to_crm(request.id, request.data["conversion"], request.data["reward"])

app = Flask(__name__)
app.add_url_rule("/siren", view_func=answers.flask, methods=["POST"])
'''

SAMPLE_5_2 = '''@answers.on("postAllocationAction", "note-the-share")
def note_the_share(request):
    # Siren may send it twice, so note it once, keyed by request.id.
    note_share(request.id, request.data["allocation"])
'''

SAMPLE_5_3 = '''@answers.on("distributionResolver", "split-by-score")
def split_by_score(request):
    pool, metrics = request.data["pool"], request.data["metrics"]
    # Whole currency steps, never more than the pool, split evenly when every score is 0.
    shares = client.apps.split_pool(pool, {m["metricId"]: m["score"] for m in metrics})
    return {"outcome": "allocated", "units": pool["units"], "shares": shares}
'''

SAMPLE_5_4 = '''@answers.on("incentiveResolver", "split-the-reward")
def split_the_reward(request):
    pool, conversions = request.data["pool"], request.data["conversions"]
    rewards = client.apps.split_pool(pool, {c["conversionId"]: c["score"] for c in conversions})
    return {"outcome": "rewards", "units": pool["units"], "rewards": rewards}
'''

SAMPLE_AWARD = '''@answers.on("incentiveType", "pay-on-closed-deals")
def pay_on_closed_deals(request):
    sale, ids = request.data["transaction"], [e["engagementId"] for e in request.data["engagements"]]
    if not sale or sale["units"] != request.data["program"]["units"]:
        return {"outcome": "none"}  # A lead earns nothing here.
    return {"outcome": "award", "units": sale["units"], "pool": sale["total"] // 10, "engagementIds": ids}
'''

FUNCTION_5_1 = SAMPLE_5_1[SAMPLE_5_1.index('@answers.on("postConversionAction"') : SAMPLE_5_1.index("app = Flask")]

SAMPLES = {
    "postConversionAction": SAMPLE_5_1,
    "postAllocationAction": SAMPLE_5_1.replace(FUNCTION_5_1, SAMPLE_5_2 + "\n"),
    "distributionResolver": SAMPLE_5_1.replace(FUNCTION_5_1, SAMPLE_5_3 + "\n"),
    "incentiveResolver": SAMPLE_5_1.replace(FUNCTION_5_1, SAMPLE_5_4 + "\n"),
    "incentiveType": SAMPLE_5_1.replace(FUNCTION_5_1, SAMPLE_AWARD + "\n"),
}


@pytest.fixture(autouse=True)
def flask_installed():
    pytest.importorskip("flask")


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass


def signed_delivery(kind: str):
    """What Siren sends for the kind, signed now with the test secret, as
    WebhookSignature::headers() signs it."""
    body = json.dumps(CONTRACTS["kinds"][kind]["request"]).encode()
    t = int(time.time())
    v1 = hmac.new(SECRET.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Siren-Signature": "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "X-Siren-Event": "app.request",
        "X-Siren-Delivery": CONTRACTS["requestKey"] + ".1",
        "User-Agent": "Siren-Webhooks/1.0",
        "X-Siren-Timestamp": str(t),
        "X-Siren-Signature-V2": f"t={t},v1={v1}",
        "Idempotency-Key": CONTRACTS["requestKey"],
    }
    return body, headers


@pytest.mark.parametrize("kind", list(SAMPLES))
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

    with respx.mock(base_url=CONTRACTS["apiBase"], assert_all_called=False) as siren_api:
        siren_api.post(f"/apps/requests/{CONTRACTS['requestKey']}/answer").mock(side_effect=answer_route)
        scope: Dict[str, Any] = {
            "__name__": "sample",
            "add_to_crm": lambda *args: stubbed.append(args),
            "note_share": lambda *args: stubbed.append(args),
        }
        exec(compile(SAMPLES[kind], f"sample-{kind}", "exec"), scope)
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
            expected = CONTRACTS["kinds"][kind]["answer"]
            if expected is None:
                data = CONTRACTS["kinds"][kind]["request"]["data"]
                if kind == "postConversionAction":
                    assert stubbed == [(CONTRACTS["requestKey"], data["conversion"], data["reward"])]
                else:
                    assert stubbed == [(CONTRACTS["requestKey"], data["allocation"])]
                assert posted == []
            else:
                assert answered.wait(5)
                assert posted == [{"auth": f"Bearer {APP_KEY}", "body": expected}]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


def test_each_sample_is_ten_to_sixteen_lines():
    for kind, sample in SAMPLES.items():
        assert 10 <= len(sample.splitlines()) <= 16, kind


@pytest.mark.parametrize(
    "sample", [SAMPLE_5_1, SAMPLE_5_2, SAMPLE_5_3, SAMPLE_5_4, SAMPLE_AWARD], ids=["5.1", "5.2", "5.3", "5.4", "award"]
)
def test_the_readme_carries_each_sample_word_for_word(sample):
    assert sample in (ROOT / "README.md").read_text()
