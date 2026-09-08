"""HTTP plumbing shared by the Jira and Confluence tools.

Underscore-prefixed so the dispatcher skips it: this is a helper, not a
subcommand. It lives beside the tools rather than in `handy/util.py` because
nothing outside the Atlassian family has any use for it.

Both products are assumed to be on-prem (Server / Data Center) and to accept a
personal access token as a bearer credential.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


class AtlassianError(Exception):
    """A request to Jira or Confluence could not be completed."""


@dataclass(frozen=True)
class Client:
    """A minimal bearer-token REST client for one on-prem Atlassian host."""

    base_url: str
    token: str
    timeout: float = 30.0

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: Any) -> Any:
        body = json.dumps(payload).encode("utf-8")
        return self._request("POST", path, data=body, content_type="application/json")

    def put(self, path: str, payload: Any) -> Any:
        body = json.dumps(payload).encode("utf-8")
        return self._request("PUT", path, data=body, content_type="application/json")

    def upload(
        self,
        path: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        fields: dict[str, str] | None = None,
    ) -> Any:
        """POST a multipart file upload (how Confluence takes attachments)."""
        body, encoded_type = encode_multipart(fields or {}, filename, content, content_type)
        return self._request(
            "POST",
            path,
            data=body,
            content_type=encoded_type,
            # Confluence rejects uploads without this XSRF opt-out header.
            headers={"X-Atlassian-Token": "nocheck"},
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/json")
        if content_type:
            request.add_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            detail = _snippet(error.read())
            raise AtlassianError(f"{method} {url} failed: HTTP {error.code} {detail}") from error
        except urllib.error.URLError as error:
            raise AtlassianError(f"{method} {url} failed: {error.reason}") from error
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise AtlassianError(f"{method} {url} returned a non-JSON body") from error


def client_from_env(
    service: str,
    url: str | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> Client:
    """Build a client for "jira" or "confluence" from flags, falling back to env.

    Reads `JIRA_URL` / `JIRA_TOKEN` (or `CONFLUENCE_*`) so tokens never have to
    appear in a crontab or a shell history.
    """
    prefix = service.upper()
    base_url = url or os.environ.get(f"{prefix}_URL", "")
    secret = token or os.environ.get(f"{prefix}_TOKEN", "")
    if not base_url:
        raise AtlassianError(f"no {service} base URL: pass --url or set {prefix}_URL")
    if not secret:
        raise AtlassianError(f"no {service} token: pass --token or set {prefix}_TOKEN")
    return Client(base_url=base_url.rstrip("/"), token=secret, timeout=timeout)


def encode_multipart(
    fields: dict[str, str],
    filename: str,
    content: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    """Return the body and Content-Type for a one-file multipart/form-data POST."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _snippet(body: bytes, limit: int = 200) -> str:
    text = body.decode("utf-8", errors="replace").strip().replace("\n", " ")
    return text[:limit] if text else "(no response body)"
