"""Short-lived app tokens, traded for the app's key and kept fresh.

Siren takes only an app token at a custom event's send address (slice C13,
apps-event-tokens). :class:`AppTokens` asks the token route
(``POST /oauth/token``, grant ``client_credentials``) for a server token the
first time a custom event is sent, keeps it, and asks again when fewer than
:data:`SERVER_REFRESH_MARGIN` seconds of its life are left. It keeps one
browser token per event in the same way, with :data:`BROWSER_REFRESH_MARGIN`.
Threads that need a token at the same time share one request.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from ._client import HttpClient, parse_json
from .errors import ApiError, BadRequestError
from .types import BrowserToken

#: A server token is replaced when fewer than this many seconds are left.
SERVER_REFRESH_MARGIN = 120

#: A browser token is replaced when fewer than this many seconds are left.
BROWSER_REFRESH_MARGIN = 60

#: The scope that asks for a browser token, followed by the event key.
BROWSER_SCOPE_PREFIX = "browser:"


def _clock() -> float:
    """Seconds on a clock that never goes back. Tests replace it."""
    return time.monotonic()


class AppTokens:
    """One client's app tokens: a server token and a browser token per event."""

    def __init__(self, client: HttpClient) -> None:
        self._client = client
        self._held: Dict[str, Tuple[str, float]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._key_has_no_app = False

    def server_token(self) -> Optional[str]:
        """The server token to send a custom event with.

        ``None`` when the key belongs to no app, so the send carries the key
        as before. Any other refusal from the token route is raised.
        """
        if self._key_has_no_app:
            return None
        try:
            return self._token("", SERVER_REFRESH_MARGIN)[0]
        except BadRequestError as exc:
            if exc.code != "unauthorized_client":
                raise
            self._key_has_no_app = True
            return None

    def drop_server_token(self, rejected: str) -> None:
        """Forget a server token Siren answered 401 to."""
        with self._lock_for(""):
            held = self._held.get("")
            if held is not None and held[0] == rejected:
                del self._held[""]

    def browser_token(self, event_key: str) -> BrowserToken:
        """A browser token for one custom event, reused until under 60 seconds are left."""
        token, expires_at = self._token(BROWSER_SCOPE_PREFIX + event_key, BROWSER_REFRESH_MARGIN)
        return BrowserToken(
            token=token,
            expires_in=max(0, int(round(expires_at - _clock()))),
            url=f"{self._client.base_url}/event/{quote(event_key, safe='')}",
        )

    def _lock_for(self, scope: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(scope)
            if lock is None:
                lock = self._locks[scope] = threading.Lock()
            return lock

    def _token(self, scope: str, margin: int) -> Tuple[str, float]:
        held = self._held.get(scope)
        if held is not None and held[1] - _clock() >= margin:
            return held

        # One request per scope at a time. A thread that waited here finds
        # the token the first one got and goes on without asking again.
        with self._lock_for(scope):
            held = self._held.get(scope)
            if held is not None and held[1] - _clock() >= margin:
                return held

            body = {"grant_type": "client_credentials", "client_secret": self._client.api_key}
            if scope:
                body["scope"] = scope
            response = self._client.request(
                "POST", "/oauth/token", json=body, retryable=True, bearer=False
            )
            answer = parse_json(response)
            token = answer.get("access_token") if isinstance(answer, dict) else None
            expires_in = answer.get("expires_in") if isinstance(answer, dict) else None
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(expires_in, int)
                or isinstance(expires_in, bool)
                or expires_in <= 0
            ):
                raise ApiError(
                    "Siren answered the token request without an access token.",
                    status_code=response.status_code,
                )

            held = (token, _clock() + expires_in)
            self._held[scope] = held
            return held
