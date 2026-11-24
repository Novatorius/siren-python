"""The SDK ships Siren's kind registry snapshot (docs/api/app-kinds.json on
Siren's pkt/apps-feature-rename) byte for byte, and takes each kind's
screen name, the kinds ``on()`` accepts and each FeatureKind member's docs
from it. Kinds keep their ids. The readable name comes from the snapshot."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

import siren

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = Path(siren.__file__).parent / "app_kinds.json"
FIELDS = ["kind", "term", "name", "description", "mode", "direction"]


def shipped():
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def generator():
    spec = importlib.util.spec_from_file_location("feature_kinds", ROOT / "scripts" / "feature_kinds.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shipped_snapshot_matches_sirens_byte_for_byte():
    path = os.environ.get("SIREN_APP_KINDS")
    if not path:
        pytest.skip(
            "Set SIREN_APP_KINDS to Siren's docs/api/app-kinds.json to hold the shipped copy byte for byte equal to it."
        )
    assert SHIPPED.read_bytes() == Path(path).read_bytes()


def test_the_shipped_snapshot_has_sirens_format():
    raw = SHIPPED.read_bytes()
    kinds = shipped()
    assert len(kinds) == 14
    assert [list(kind) for kind in kinds] == [FIELDS] * 14
    assert all(kind["mode"] in ("tell", "ask", "receive") for kind in kinds)
    assert all(kind["direction"] in ("out", "in") for kind in kinds)
    # PHP json_encode with JSON_PRETTY_PRINT, JSON_UNESCAPED_SLASHES and
    # JSON_UNESCAPED_UNICODE, then one newline.
    assert raw.endswith(b"]\n") and not raw.endswith(b"\n\n")
    assert raw.startswith(b"[\n    {\n        \"kind\": ")
    assert b"\\/" not in raw


def test_the_snapshot_ships_in_the_package():
    assert SHIPPED.is_file()


def test_the_kinds_on_takes_are_the_snapshots_tells_and_asks_in_its_order():
    assert list(siren.APP_REQUEST_KINDS) == [kind["kind"] for kind in shipped() if kind["mode"] in ("tell", "ask")]


def test_feature_kind_holds_every_kind_in_the_snapshot():
    assert [member.value for member in siren.FeatureKind] == [kind["kind"] for kind in shipped()]


def test_each_member_has_docs_that_carry_its_name_and_a_term_that_differs():
    source = (ROOT / "src" / "siren" / "types.py").read_text(encoding="utf-8")
    module = generator()
    assert module.generated_block(shipped()) in source, "Run python scripts/feature_kinds.py to bring FeatureKind's docs up to the snapshot."
    block = module.generated_block(shipped())
    assert 'INCENTIVE_RESOLVER = "incentiveResolver"\n    """Attribution model. Siren\'s code calls it the incentive resolver.' in block
    assert 'PAYOUT_METHOD = "payoutMethod"\n    """Payout method. Siren asks' in block


@pytest.mark.parametrize(
    "kind, words",
    [
        ("attributionModel", "The kind for Attribution model is incentiveResolver."),
        ("Incentive structure", "The kind for Incentive structure is incentiveType."),
        ("customerFilterForPrograms", "The kind for Customer filter for programs is engagementFilter."),
    ],
)
def test_the_screen_name_message_reads_names_from_the_snapshot(kind, words):
    with pytest.raises(siren.AppHandlerError) as refused:
        siren.Siren(api_key="sk_test").apps.request_handler("cd" * 32).on(kind, "my-feature", lambda request: None)
    assert str(refused.value) == words
