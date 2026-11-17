"""Run the whole TeamCity pipeline: fetch builds, compute stats, render the PNG.

This leans on the other three TeamCity tools. Each stage is still usable on its
own — `teamcity-query | teamcity-stats | teamcity-dashboard` does the same thing
by hand — but a cron entry shouldn't have to spell out a pipeline. Publish the
PNG with `handy confluence-publish` or `handy confluence-block`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handy.tools import teamcity_dashboard, teamcity_query, teamcity_stats
from handy.tools._teamcity import TeamCityError, client_from_env
from handy.util import write_json

HELP = "fetch TeamCity builds, compute stats, and render the dashboard PNG"


@dataclass(frozen=True)
class Artifacts:
    """The three files one run leaves behind."""

    builds: Path
    stats: Path
    image: Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    teamcity_query.add_filter_arguments(parser)
    parser.add_argument(
        "-d",
        "--out-dir",
        type=Path,
        default=Path("."),
        help="where to write the intermediate JSON and the PNG (default: .)",
    )
    parser.add_argument(
        "--name",
        default="teamcity-report",
        help="basename for the generated files (default: %(default)s)",
    )
    parser.add_argument("--title", default="TeamCity dashboard", help="headline on the graphic")
    parser.add_argument(
        "--group-by",
        choices=sorted(teamcity_stats.GROUP_KEYS),
        default="build-type",
        help="what the per-row breakdown is keyed on (default: %(default)s)",
    )
    parser.add_argument("--top", type=int, default=6, help="rows to chart per panel (default: 6)")
    parser.add_argument("--url", help="TeamCity base URL (default: $TEAMCITY_URL)")
    parser.add_argument("--token", help="access token (default: $TEAMCITY_TOKEN)")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds per request")


def build_artifacts(
    document: dict[str, Any],
    out_dir: Path,
    name: str = "teamcity-report",
    group_by: str = "build-type",
    title: str = "TeamCity dashboard",
    top: int = 6,
) -> Artifacts:
    """Turn a `teamcity-query` document into the three files on disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = Artifacts(
        builds=out_dir / f"{name}-builds.json",
        stats=out_dir / f"{name}-stats.json",
        image=out_dir / f"{name}.png",
    )
    stats = teamcity_stats.stats_for_document(document, group_by)
    write_json(document, paths.builds)
    write_json(stats, paths.stats)
    teamcity_dashboard.render(stats, paths.image, title, top)
    return paths


def run(args: argparse.Namespace) -> int:
    try:
        client = client_from_env(args.url, args.token, args.timeout)
        document = teamcity_query.query(client, args)
        paths = build_artifacts(
            document, args.out_dir, args.name, args.group_by, args.title, args.top
        )
    except (TeamCityError, ValueError, OSError) as error:
        print(f"handy teamcity-report: {error}")
        return 1
    print(f"fetched {document['count']} builds")
    print(f"wrote {paths.builds}, {paths.stats} and {paths.image}")
    return 0
