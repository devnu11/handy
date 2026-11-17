"""Pull finished builds out of TeamCity and dump them as JSON.

Both official and personal builds are fetched by default; each build carries a
`personal` flag so `handy teamcity-stats` can report the two side by side.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from handy.tools._teamcity import (
    TeamCityError,
    client_from_env,
    format_timestamp,
    parse_timestamp,
)
from handy.util import write_json

HELP = "fetch finished TeamCity builds (official and personal) as JSON"

# Only what the downstream tools read, so the response stays small.
FIELDS = (
    "count,build(id,number,status,state,personal,branchName,buildTypeId,"
    "queuedDate,startDate,finishDate,webUrl,canceledInfo(timestamp),"
    "user(username),triggered(user(username)),"
    "buildType(name,projectName),agent(name,pool(name)))"
)

PERSONAL_CHOICES = ("any", "true", "false")
_SINCE_UNITS = {"h": "hours", "d": "days", "w": "weeks"}
_SINCE_PATTERN = re.compile(r"(\d+)([hdw])")


class Searcher(Protocol):
    """The slice of `_teamcity.Client` this tool needs (kept narrow for tests)."""

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_filter_arguments(parser)
    parser.add_argument("-o", "--output", type=Path, help="write the JSON here (default: stdout)")
    parser.add_argument("--url", help="TeamCity base URL (default: $TEAMCITY_URL)")
    parser.add_argument("--token", help="access token (default: $TEAMCITY_TOKEN)")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")


def add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    """Flags that choose which builds to fetch (shared with `teamcity-report`)."""
    parser.add_argument(
        "--since",
        default="7d",
        help="how far back to look, by finish time: Nh, Nd or Nw (default: %(default)s)",
    )
    parser.add_argument("--project", help="project ID; its subprojects are included")
    parser.add_argument("--build-type", help="build configuration ID")
    parser.add_argument("--user", help="username: owner of personal builds, else who triggered")
    parser.add_argument(
        "--personal",
        choices=PERSONAL_CHOICES,
        default="any",
        help="any = official and personal, true = personal only, false = official only",
    )
    parser.add_argument(
        "--branch",
        default="default:any",
        help="TeamCity branch locator (default: %(default)s, i.e. every branch)",
    )
    parser.add_argument("--locator", help="extra raw locator dimensions to append")
    parser.add_argument(
        "--limit", type=int, default=5000, help="stop after this many builds (default: 5000)"
    )
    parser.add_argument(
        "--page-size", type=int, default=500, help="builds to request per call (default: 500)"
    )


def parse_since(spec: str) -> timedelta:
    """Turn "36h", "7d" or "2w" into a timedelta."""
    match = _SINCE_PATTERN.fullmatch(spec.strip().lower())
    if not match or int(match.group(1)) <= 0:
        raise ValueError(f"bad --since {spec!r}: use a positive Nh, Nd or Nw, e.g. 7d")
    return timedelta(**{_SINCE_UNITS[match.group(2)]: int(match.group(1))})


def build_locator(
    since: datetime,
    personal: str = "any",
    project: str | None = None,
    build_type: str | None = None,
    user: str | None = None,
    branch: str = "default:any",
    extra: str | None = None,
) -> str:
    """Compose the build locator.

    Every dimension TeamCity's default filter would silently narrow — personal,
    canceled, failed-to-start, branch — is spelled out, so the result never
    depends on the server's defaults.
    """
    parts = [
        "state:finished",
        f"personal:{personal}",
        "canceled:any",
        "failedToStart:any",
        f"branch:{branch}",
        f"finishDate:(date:{format_timestamp(since)},condition:after)",
    ]
    if project:
        parts.append(f"affectedProject:(id:{project})")
    if build_type:
        parts.append(f"buildType:(id:{build_type})")
    if user:
        parts.append(f"user:(username:{user})")
    if extra:
        parts.append(extra.strip(","))
    return ",".join(parts)


def fetch_builds(
    client: Searcher,
    locator: str,
    limit: int = 5000,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Page through `/app/rest/builds` and return normalized build dicts."""
    builds: list[dict[str, Any]] = []
    while len(builds) < limit:
        wanted = min(page_size, limit - len(builds))
        page = client.get(
            "/app/rest/builds",
            {"locator": f"{locator},start:{len(builds)},count:{wanted}", "fields": FIELDS},
        )
        raw = page.get("build") or []
        builds.extend(normalize_build(build) for build in raw)
        if len(raw) < wanted:
            break
    return builds[:limit]


def normalize_build(build: dict[str, Any]) -> dict[str, Any]:
    """Flatten one TeamCity build so downstream tools never touch its nesting."""
    build_type = build.get("buildType") or {}
    agent = build.get("agent") or {}
    return {
        "id": build.get("id"),
        "number": build.get("number") or "",
        "build_type_id": build.get("buildTypeId") or "",
        "build_type": build_type.get("name") or build.get("buildTypeId") or "",
        "project": build_type.get("projectName") or "",
        "outcome": outcome(build),
        "personal": bool(build.get("personal")),
        "user": _username(build),
        "branch": build.get("branchName"),
        "agent": agent.get("name"),
        "pool": (agent.get("pool") or {}).get("name"),
        "queued": _iso(build.get("queuedDate")),
        "started": _iso(build.get("startDate")),
        "finished": _iso(build.get("finishDate")),
        "web_url": build.get("webUrl"),
    }


def outcome(build: dict[str, Any]) -> str:
    """Classify a finished build as "canceled", "passed" or "failed"."""
    if build.get("canceledInfo"):
        return "canceled"
    return "passed" if build.get("status") == "SUCCESS" else "failed"


def _username(build: dict[str, Any]) -> str | None:
    """Owner of a personal build, else whoever triggered it (None for a trigger)."""
    owner = build.get("user") or (build.get("triggered") or {}).get("user") or {}
    return owner.get("username")


def _iso(value: str | None) -> str | None:
    """Convert a TeamCity timestamp to ISO 8601 so later stages stay format-agnostic."""
    parsed = parse_timestamp(value)
    return parsed.isoformat() if parsed else None


def build_document(
    locator: str,
    since: datetime,
    builds: list[dict[str, Any]],
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Wrap builds in the envelope `handy teamcity-stats` expects."""
    stamp = fetched_at or datetime.now(UTC)
    return {
        "locator": locator,
        "since": since.isoformat(),
        "fetched_at": stamp.isoformat(),
        "count": len(builds),
        "builds": builds,
    }


def query(
    client: Searcher, args: argparse.Namespace, now: datetime | None = None
) -> dict[str, Any]:
    """Run the fetch described by the filter flags and return the document."""
    since = (now or datetime.now(UTC)) - parse_since(args.since)
    locator = build_locator(
        since, args.personal, args.project, args.build_type, args.user, args.branch, args.locator
    )
    builds = fetch_builds(client, locator, args.limit, args.page_size)
    return build_document(locator, since, builds, now)


def run(args: argparse.Namespace) -> int:
    try:
        client = client_from_env(args.url, args.token, args.timeout)
        document = query(client, args)
    except (TeamCityError, ValueError) as error:
        print(f"handy teamcity-query: {error}")
        return 1
    write_json(document, args.output)
    if args.output is not None:
        print(f"wrote {document['count']} builds to {args.output}")
    return 0
