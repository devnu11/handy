"""Run a JQL query against an on-prem Jira and dump the matching issues as JSON."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from handy.tools._atlassian import AtlassianError, client_from_env
from handy.util import write_json

HELP = "run a JQL query and write the matching issues as JSON"

# Only what the downstream tools actually read, so the response stays small.
FIELDS = (
    "summary",
    "status",
    "issuetype",
    "priority",
    "assignee",
    "components",
    "created",
    "resolutiondate",
)


class Searcher(Protocol):
    """The slice of `_atlassian.Client` this tool needs (kept narrow for tests)."""

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("jql", help="JQL to run, e.g. 'project = FOO AND resolution = Unresolved'")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write the JSON here (default: stdout)",
    )
    parser.add_argument("--url", help="Jira base URL (default: $JIRA_URL)")
    parser.add_argument("--token", help="personal access token (default: $JIRA_TOKEN)")
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="stop after this many issues (default: 1000)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="issues to request per call (default: 100)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")


def search_issues(
    client: Searcher,
    jql: str,
    limit: int = 1000,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Page through Jira's search endpoint and return normalized issue dicts."""
    issues: list[dict[str, Any]] = []
    start_at = 0
    while len(issues) < limit:
        page = client.get(
            "/rest/api/2/search",
            {
                "jql": jql,
                "startAt": start_at,
                "maxResults": min(page_size, limit - len(issues)),
                "fields": ",".join(FIELDS),
            },
        )
        raw = page.get("issues") or []
        if not raw:
            break
        issues.extend(normalize_issue(issue) for issue in raw)
        start_at += len(raw)
        if start_at >= page.get("total", start_at):
            break
    return issues[:limit]


def normalize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Flatten one Jira issue so downstream tools never touch Jira's nesting."""
    fields = issue.get("fields") or {}
    return {
        "key": issue.get("key", ""),
        "summary": fields.get("summary") or "",
        "status": _name(fields.get("status"), "Unknown"),
        "type": _name(fields.get("issuetype"), "Unknown"),
        "priority": _name(fields.get("priority")),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "components": [c.get("name", "") for c in fields.get("components") or []],
        "created": fields.get("created"),
        "resolved": fields.get("resolutiondate"),
    }


def build_document(
    jql: str, issues: list[dict[str, Any]], fetched_at: datetime | None = None
) -> dict[str, Any]:
    """Wrap issues in the envelope `handy jira-stats` expects."""
    stamp = fetched_at or datetime.now(UTC)
    return {
        "jql": jql,
        "fetched_at": stamp.isoformat(),
        "count": len(issues),
        "issues": issues,
    }


def _name(value: dict[str, Any] | None, default: str | None = None) -> str | None:
    return (value or {}).get("name") or default


def run(args: argparse.Namespace) -> int:
    try:
        client = client_from_env("jira", args.url, args.token, args.timeout)
        issues = search_issues(client, args.jql, args.limit, args.page_size)
    except AtlassianError as error:
        print(f"handy jira-query: {error}")
        return 1
    write_json(build_document(args.jql, issues), args.output)
    if args.output is not None:
        print(f"wrote {len(issues)} issues to {args.output}")
    return 0
