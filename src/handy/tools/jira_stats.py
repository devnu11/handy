"""Bucketize Jira issues by component and by age, and average the ages."""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from handy.util import read_json, write_json

HELP = "bucketize issues by component and age, with average ages"

DEFAULT_BUCKETS = "0-7,8-30,31-90,90+"
UNASSIGNED = "Unassigned"


@dataclass(frozen=True)
class Bucket:
    """One age band, in whole days. `high` of None means open-ended."""

    label: str
    low: int
    high: int | None

    def contains(self, days: int) -> bool:
        return days >= self.low and (self.high is None or days <= self.high)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="issues JSON from `handy jira-query` (default: stdin)",
    )
    parser.add_argument("-o", "--output", type=Path, help="write the stats here (default: stdout)")
    parser.add_argument(
        "--buckets",
        default=DEFAULT_BUCKETS,
        help=f"age bands in days, e.g. {DEFAULT_BUCKETS!r} (default: %(default)s)",
    )


def parse_buckets(spec: str) -> list[Bucket]:
    """Turn "0-7,8-30,90+" into Bucket objects, left to right."""
    buckets: list[Bucket] = []
    for chunk in (part.strip() for part in spec.split(",")):
        if not chunk:
            continue
        try:
            if chunk.endswith("+"):
                buckets.append(Bucket(chunk, int(chunk[:-1]), None))
            else:
                low, _, high = chunk.partition("-")
                if not high:
                    raise ValueError
                buckets.append(Bucket(chunk, int(low), int(high)))
        except ValueError:
            raise ValueError(f"bad age band {chunk!r}: use 'low-high' or 'low+'") from None
    if not buckets:
        raise ValueError("no age bands given")
    return buckets


def parse_timestamp(value: str) -> datetime:
    """Parse a Jira timestamp ("2024-01-15T10:23:45.000+0000")."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"unparseable timestamp {value!r}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def age_in_days(created: datetime, now: datetime) -> int:
    """Whole days between creation and `now`, floored at zero."""
    return max(0, (now - created).days)


def summarize(
    issues: list[dict[str, Any]],
    buckets: list[Bucket] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bucketize issues by component and age band.

    Age is measured from creation to `now` for every issue, resolved or not. An
    issue with several components is counted once under each of them, so the
    component counts can sum to more than the issue total; an issue with none
    lands under "Unassigned".
    """
    bands = buckets or parse_buckets(DEFAULT_BUCKETS)
    moment = now or datetime.now(UTC)

    ages: list[int] = []
    per_component: dict[str, list[int]] = {}
    skipped: list[str] = []
    for issue in issues:
        created = issue.get("created")
        try:
            days = age_in_days(parse_timestamp(created), moment) if created else None
        except ValueError:
            days = None
        if days is None:
            skipped.append(issue.get("key", "?"))
            continue
        ages.append(days)
        for component in issue.get("components") or [UNASSIGNED]:
            per_component.setdefault(component or UNASSIGNED, []).append(days)

    components = [
        {"name": name, **_stats(component_ages), "age_buckets": _tally(component_ages, bands)}
        for name, component_ages in per_component.items()
    ]
    components.sort(key=lambda item: (-item["count"], item["name"]))

    tally = _tally(ages, bands)
    return {
        "generated_at": moment.isoformat(),
        "total": len(issues),
        "counted": len(ages),
        "skipped": skipped,
        "overall": _stats(ages),
        "age_buckets": [
            {
                "label": band.label,
                "low": band.low,
                "high": band.high,
                "count": tally[band.label],
                "share": round(tally[band.label] / len(ages), 4) if ages else 0.0,
            }
            for band in bands
        ],
        "components": components,
    }


def _stats(ages: list[int]) -> dict[str, Any]:
    if not ages:
        return {"count": 0, "average_age_days": 0.0, "median_age_days": 0.0, "oldest_days": 0}
    return {
        "count": len(ages),
        "average_age_days": round(statistics.fmean(ages), 1),
        "median_age_days": round(statistics.median(ages), 1),
        "oldest_days": max(ages),
    }


def _tally(ages: list[int], bands: list[Bucket]) -> dict[str, int]:
    """Count ages per band, first match wins so overlapping bands can't double count."""
    counts = dict.fromkeys((band.label for band in bands), 0)
    for days in ages:
        for band in bands:
            if band.contains(days):
                counts[band.label] += 1
                break
    return counts


def run(args: argparse.Namespace) -> int:
    try:
        bands = parse_buckets(args.buckets)
    except ValueError as error:
        print(f"handy jira-stats: {error}")
        return 1
    try:
        document = read_json(args.input)
    except (OSError, ValueError) as error:
        print(f"handy jira-stats: cannot read issues: {error}")
        return 1

    issues = document["issues"] if isinstance(document, dict) else document
    if not isinstance(issues, list):
        print("handy jira-stats: expected a list of issues or a jira-query document")
        return 1

    stats = summarize(issues, bands)
    if isinstance(document, dict) and document.get("jql"):
        stats["jql"] = document["jql"]
    write_json(stats, args.output)
    if args.output is not None:
        print(
            f"wrote stats for {stats['counted']} issues "
            f"across {len(stats['components'])} components to {args.output}"
        )
    return 0
