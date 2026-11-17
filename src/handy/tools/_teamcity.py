"""HTTP plumbing and timestamp handling shared by the TeamCity tools.

Underscore-prefixed so the dispatcher skips it: this is a helper, not a
subcommand. It is deliberately separate from `_atlassian.py` — the two families
are independent, and TeamCity only ever needs a read-only GET.

The server is assumed to accept an access token as a bearer credential
(Profile -> Access Tokens in the TeamCity UI).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# TeamCity's own wire format, e.g. "20130305T170030+0400". The REST API both
# returns dates like this and expects them in locators.
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%z"


class TeamCityError(Exception):
    """A request to TeamCity could not be completed."""


@dataclass(frozen=True)
class Client:
    """A minimal read-only bearer-token REST client for one TeamCity server."""

    base_url: str
    token: str
    timeout: float = 30.0

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET `path` under the server root and return the decoded JSON body."""
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/json")
        return _decode(url, _fetch(request, url, self.timeout))


def _fetch(request: urllib.request.Request, url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = _snippet(error.read())
        raise TeamCityError(f"GET {url} failed: HTTP {error.code} {detail}") from error
    except urllib.error.URLError as error:
        raise TeamCityError(f"GET {url} failed: {error.reason}") from error


def _decode(url: str, body: bytes) -> Any:
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise TeamCityError(f"GET {url} returned a non-JSON body") from error


def _snippet(body: bytes, limit: int = 200) -> str:
    text = body.decode("utf-8", errors="replace").strip().replace("\n", " ")
    return text[:limit] if text else "(no response body)"


def client_from_env(
    url: str | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> Client:
    """Build a client from flags, falling back to `TEAMCITY_URL` / `TEAMCITY_TOKEN`.

    Reading the environment keeps tokens out of crontabs and shell history.
    """
    base_url = url or os.environ.get("TEAMCITY_URL", "")
    secret = token or os.environ.get("TEAMCITY_TOKEN", "")
    if not base_url:
        raise TeamCityError("no TeamCity base URL: pass --url or set TEAMCITY_URL")
    if not secret:
        raise TeamCityError("no TeamCity token: pass --token or set TEAMCITY_TOKEN")
    return Client(base_url=base_url.rstrip("/"), token=secret, timeout=timeout)


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a TeamCity timestamp; None for a missing or malformed one."""
    if not value:
        return None
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError:
        return None


def format_timestamp(moment: datetime) -> str:
    """Render a datetime the way TeamCity locators expect it (naive means UTC)."""
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.strftime(TIMESTAMP_FORMAT)
