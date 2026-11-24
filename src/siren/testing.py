"""Try an app's request handler without Siren.

:func:`app_request` builds a request signed the way Siren signs one, for any
kind of feature, and :func:`sample_data` gives each kind's example question,
the one Siren's own request fixture holds. Hand the request to the handler's
``simulate()`` in a test, which runs the function and returns its answer
without posting it, or post it to a running app with any HTTP client::

    from siren.testing import app_request

    def test_split_by_score():
        result = answers.simulate(app_request("distributionResolver", "split-by-score"))
        assert result.answer["shares"] == {"41": 6000, "43": 4000}

Nothing here reaches the network.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from ._client import DEFAULT_BASE_URL
from .apps import REQUEST_EVENT, _REQUEST_ID

__all__ = ["KINDS", "AppTestRequest", "app_request", "sample_data"]

_SAMPLES: Dict[str, Dict[str, Any]] = json.loads((Path(__file__).parent / "_samples.json").read_text(encoding="utf-8"))["kinds"]

#: The kinds Siren sends an app a request for, each with sample data.
KINDS: Tuple[str, ...] = tuple(sorted(_SAMPLES))

_TELLS = frozenset({"postConversionAction", "postAllocationAction"})


@dataclass(frozen=True)
class AppTestRequest:
    """A signed request, as Siren would send it: ``method``, ``headers`` and
    the ``raw_body`` bytes the signature covers. Pass it to the handler's
    ``simulate()``, or post ``raw_body`` with ``headers`` to a running app."""

    method: str
    headers: Dict[str, str] = field(default_factory=dict)
    raw_body: bytes = b""

    @property
    def body(self) -> Dict[str, Any]:
        """The request's body, parsed."""
        parsed: Dict[str, Any] = json.loads(self.raw_body)
        return parsed

    @classmethod
    def signed(cls, raw_body: bytes, secret: str, *, timestamp: Optional[int] = None) -> "AppTestRequest":
        """Any body, signed as Siren signs a request. For a test that sends
        the handler something Siren would not, such as a broken body."""
        t = int(time.time()) if timestamp is None else int(timestamp)
        v1 = hmac.new(secret.encode("utf-8"), f"{t}.".encode("utf-8") + raw_body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Siren-Event": REQUEST_EVENT,
            "X-Siren-Timestamp": str(t),
            "X-Siren-Signature-V2": f"t={t},v1={v1}",
        }
        return cls("POST", headers, raw_body)


def sample_data(kind: str) -> Dict[str, Any]:
    """The kind's example question, the ``data`` Siren's own request fixture
    holds for it. Each call returns a fresh copy, so change it freely.

    Raises :class:`ValueError` for a kind Siren sends no request for.
    """
    if kind not in _SAMPLES:
        raise ValueError(f"siren.testing has no sample for {kind!r}. Siren sends requests for these kinds: {', '.join(KINDS)}.")
    return copy.deepcopy(_SAMPLES[kind])


def app_request(
    kind: str,
    key: str,
    *,
    data: Optional[Mapping[str, Any]] = None,
    secret: Optional[str] = None,
    id: Optional[str] = None,  # noqa: A002 - the request's own field name
    app: str = "my-app",
    base_url: str = DEFAULT_BASE_URL,
    timestamp: Optional[int] = None,
) -> AppTestRequest:
    """A request for the feature ``key`` of ``kind``, signed as Siren signs
    one.

    - ``data`` is the question, :func:`sample_data` for the kind by default.
    - ``secret`` is the app's signing secret, ``SIREN_SIGNING_SECRET`` by
      default.
    - ``id`` is the request key, a new ``apr_`` id by default.
    - ``base_url`` is the Siren the request comes from. An ask's
      ``answerUrl`` is built on it, and Siren Cloud's address is the default,
      as it is the client's.
    - ``timestamp`` is the signed time in Unix seconds, now by default.

    The two actions are tells and every other kind is an ask, with
    ``answerBy`` an hour after the request was made.

    Raises :class:`ValueError` for a kind Siren sends no request for, an
    empty key, a malformed id, and no secret.
    """
    method = "siren.testing.app_request()"
    question = sample_data(kind) if data is None else dict(data)
    if not isinstance(key, str) or key == "":
        raise ValueError(f"{method} needs the feature's key, such as 'split-by-score'.")
    signing_secret = secret if secret is not None else os.environ.get("SIREN_SIGNING_SECRET")
    if not signing_secret:
        raise ValueError(f"{method} needs the app's signing secret. Pass secret, or set SIREN_SIGNING_SECRET.")
    request_id = f"apr_{uuid.uuid4().hex}" if id is None else id
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        raise ValueError(f"{method} takes id as apr_ followed by 32 lowercase hex characters, and got {request_id!r}.")

    t = int(time.time()) if timestamp is None else int(timestamp)
    made = datetime.fromtimestamp(t, tz=timezone.utc)
    body: Dict[str, Any] = {
        "version": 1,
        "id": request_id,
        "type": REQUEST_EVENT,
        "mode": "tell" if kind in _TELLS else "ask",
        "app": app,
        "item": {"kind": kind, "key": key, "id": "app:1"},
        "createdAt": made.isoformat(),
    }
    if body["mode"] == "ask":
        body["answerBy"] = (made + timedelta(hours=1)).isoformat()
        body["answerUrl"] = f"{base_url.rstrip('/')}/apps/requests/{request_id}/answer"
    body["data"] = question

    request = AppTestRequest.signed(json.dumps(body).encode("utf-8"), signing_secret, timestamp=t)
    request.headers["X-Siren-Delivery"] = f"{request_id}.1"
    request.headers["Idempotency-Key"] = request_id
    return request
