"""Every status Siren gives a request an app was sent, from one constant.

Siren keeps them as ``STATUS_*`` constants in lib/Apps/Core/Models/AppRequest.php
(feat/apps 0383186437) and has no fixture that lists them, so one test reads
that file from a Siren checkout and an always-run test holds the eight.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import get_args, get_type_hints

import pytest

import siren
from siren.types import AppRequestRecord

EIGHT = ("pending", "sent", "delivered", "answered", "applying", "applied", "needs_attention", "dropped")


def test_the_constant_holds_the_eight_statuses():
    assert siren.APP_REQUEST_STATUSES == EIGHT


def test_the_status_type_and_the_record_come_from_the_constant():
    assert get_args(siren.AppRequestStatus) == siren.APP_REQUEST_STATUSES
    assert get_type_hints(AppRequestRecord)["status"] == siren.AppRequestStatus


@pytest.mark.parametrize("status", EIGHT)
def test_the_list_filter_takes_each_status(client, mocked_api, status):
    route = mocked_api.get("/apps/requests").respond(200, json={"requests": []})
    assert client.apps.requests.list(status=status) == []
    assert route.calls.last.request.url.params["status"] == status


@pytest.mark.skipif(not os.environ.get("SIREN_CHECKOUT"), reason="set SIREN_CHECKOUT to a Siren checkout to compare")
def test_the_statuses_match_sirens_constants():
    source = (Path(os.environ["SIREN_CHECKOUT"]) / "lib/Apps/Core/Models/AppRequest.php").read_text()
    sirens = re.findall(r"public const STATUS_\w+ = '([a-z_]+)';", source)
    assert sirens, "AppRequest.php holds no STATUS_* constants"
    missing = [status for status in sirens if status not in siren.APP_REQUEST_STATUSES]
    extra = [status for status in siren.APP_REQUEST_STATUSES if status not in sirens]
    assert (missing, extra) == ([], []), f"missing {missing}, extra {extra}"


def test_the_readme_lists_the_eight():
    readme = " ".join((Path(__file__).resolve().parent.parent / "README.md").read_text().split())
    listed = readme[readme.index("`status` is one of `siren.APP_REQUEST_STATUSES`"):][:300]
    assert all(f"`{status}`" in listed for status in EIGHT)
