"""Compute pass rates and queue times from TeamCity builds, official vs personal."""

from __future__ import annotations

import argparse
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from handy.util import read_json, write_json

HELP = "pass rates and queue times for official and personal TeamCity builds"

OFFICIAL = "official"
PERSONAL = "personal"
GROUPS = (OFFICIAL, PERSONAL)

# --group-by choice -> the build field it reads.
GROUP_KEYS = {
    "build-type": "build_type",
    "project": "project",
    "pool": "pool",
    "user": "user",
}
UNKNOWN = "(none)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="builds JSON from `handy teamcity-query` (default: stdin)",
    )
    parser.add_argument("-o", "--output", type=Path, help="write the stats here (default: stdout)")
    parser.add_argument(
        "--group-by",
        choices=sorted(GROUP_KEYS),
        default="build-type",
        help="what the per-row breakdown is keyed on (default: %(default)s)",
    )


def summarize(
    builds: list[dict[str, Any]],
    group_by: str = "build-type",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize builds separately for official and personal runs.

    Pass rate is passed / (passed + failed): canceled builds say nothing about
    the code under test, so they are counted but left out of the rate. Queue
    time is start minus queued, which includes any wait on snapshot
    dependencies, not only the wait for an agent.
    """
    field = GROUP_KEYS[group_by]
    return {
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "group_by": group_by,
        "total": len(builds),
        "groups": {
            name: summarize_group([b for b in builds if group_of(b) == name], field)
            for name in GROUPS
        },
    }


def group_of(build: dict[str, Any]) -> str:
    return PERSONAL if build.get("personal") else OFFICIAL


def summarize_group(builds: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Overall tally plus a per-key breakdown and a per-day series."""
    return {
        **tally(builds),
        "breakdown": _partition(builds, lambda b: b.get(field) or UNKNOWN, by_count=True),
        "daily": _partition(builds, finish_day, by_count=False),
    }


def _partition(builds: list[dict[str, Any]], key: Any, by_count: bool) -> list[dict[str, Any]]:
    """Tally builds per key; keyless builds (key returns None) are dropped."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for build in builds:
        name = key(build)
        if name is not None:
            buckets.setdefault(name, []).append(build)
    rows = [{"name": name, **tally(members)} for name, members in buckets.items()]
    order = (lambda row: (-row["builds"], row["name"])) if by_count else (lambda row: row["name"])
    return sorted(rows, key=order)


def finish_day(build: dict[str, Any]) -> str | None:
    """The UTC date a build finished on, as "YYYY-MM-DD"."""
    finished = _parse(build.get("finished"))
    return finished.astimezone(UTC).date().isoformat() if finished else None


def tally(builds: list[dict[str, Any]]) -> dict[str, Any]:
    """Outcome counts, pass rate and queue-time statistics for a set of builds."""
    outcomes = [build.get("outcome") for build in builds]
    passed, failed = outcomes.count("passed"), outcomes.count("failed")
    waits = [wait for wait in map(queue_seconds, builds) if wait is not None]
    return {
        "builds": len(builds),
        "passed": passed,
        "failed": failed,
        "canceled": outcomes.count("canceled"),
        "pass_rate": pass_rate(passed, failed),
        "queue": queue_stats(waits),
    }


def pass_rate(passed: int, failed: int) -> float | None:
    """Fraction passed among decided builds; None when nothing was decided."""
    decided = passed + failed
    return round(passed / decided, 4) if decided else None


def queue_seconds(build: dict[str, Any]) -> float | None:
    """Seconds between queueing and starting; None if either stamp is unusable."""
    queued, started = _parse(build.get("queued")), _parse(build.get("started"))
    if queued is None or started is None or started < queued:
        return None
    return (started - queued).total_seconds()


def queue_stats(waits: list[float]) -> dict[str, Any]:
    """Count, mean, median, p90 and max of queue waits, in seconds."""
    if not waits:
        return {"count": 0, "mean_s": None, "median_s": None, "p90_s": None, "max_s": None}
    return {
        "count": len(waits),
        "mean_s": round(statistics.fmean(waits), 1),
        "median_s": round(statistics.median(waits), 1),
        "p90_s": round(percentile(waits, 0.9), 1),
        "max_s": round(max(waits), 1),
    }


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile: the smallest value with `fraction` at or below it."""
    if not values:
        raise ValueError("percentile of an empty list")
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _parse(value: str | None) -> datetime | None:
    """Parse an ISO timestamp; None for a missing or malformed one."""
    try:
        parsed = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed and not parsed.tzinfo else parsed


def stats_for_document(document: Any, group_by: str = "build-type") -> dict[str, Any]:
    """Summarize a `teamcity-query` document (or a bare list of builds)."""
    builds = document.get("builds") if isinstance(document, dict) else document
    if not isinstance(builds, list):
        raise ValueError("expected a list of builds or a teamcity-query document")
    stats = summarize(builds, group_by)
    if isinstance(document, dict):
        stats.update({key: document[key] for key in ("locator", "since") if document.get(key)})
    return stats


def run(args: argparse.Namespace) -> int:
    try:
        stats = stats_for_document(read_json(args.input), args.group_by)
    except (OSError, ValueError) as error:
        print(f"handy teamcity-stats: cannot read builds: {error}")
        return 1
    write_json(stats, args.output)
    if args.output is not None:
        print(f"wrote stats for {stats['total']} builds to {args.output}")
    return 0
