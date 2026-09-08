"""Run the whole weekly pipeline: query Jira, bucketize, render, publish.

This is the one tool that leans on the others. Each stage is still usable on its
own — `jira-query | jira-stats | jira-infographic | confluence-publish` does the
same thing by hand — but a cron entry shouldn't have to spell out a pipeline.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from handy.tools import confluence_publish, jira_infographic, jira_query, jira_stats
from handy.tools._atlassian import AtlassianError, client_from_env
from handy.util import write_json

HELP = "query Jira, bucketize, render a PNG, and publish it to Confluence"


@dataclass(frozen=True)
class Artifacts:
    issues: Path
    stats: Path
    image: Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("jql", help="JQL to run")
    parser.add_argument(
        "-d",
        "--out-dir",
        type=Path,
        default=Path("."),
        help="where to write the intermediate JSON and the PNG (default: .)",
    )
    parser.add_argument(
        "--name",
        default="jira-report",
        help="basename for the generated files (default: %(default)s)",
    )
    parser.add_argument("--title", default="Jira issue report", help="headline on the graphic")
    parser.add_argument("--buckets", default=jira_stats.DEFAULT_BUCKETS, help="age bands in days")
    parser.add_argument("--top", type=int, default=10, help="components to chart (default: 10)")
    parser.add_argument("--limit", type=int, default=1000, help="cap on issues fetched")
    parser.add_argument("--jira-url", help="default: $JIRA_URL")
    parser.add_argument("--jira-token", help="default: $JIRA_TOKEN")
    parser.add_argument(
        "--page-id",
        help="Confluence page to publish to; without it the pipeline stops at the PNG",
    )
    parser.add_argument("--confluence-url", help="default: $CONFLUENCE_URL")
    parser.add_argument("--confluence-token", help="default: $CONFLUENCE_TOKEN")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds per request")


def build_artifacts(
    document: dict[str, Any],
    out_dir: Path,
    name: str = "jira-report",
    buckets: str = jira_stats.DEFAULT_BUCKETS,
    title: str = "Jira issue report",
    top: int = 10,
    now: datetime | None = None,
) -> Artifacts:
    """Turn a `jira-query` document into the three files on disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = Artifacts(
        issues=out_dir / f"{name}-issues.json",
        stats=out_dir / f"{name}-stats.json",
        image=out_dir / f"{name}.png",
    )
    stats = jira_stats.summarize(
        document.get("issues", []), jira_stats.parse_buckets(buckets), now or datetime.now(UTC)
    )
    if document.get("jql"):
        stats["jql"] = document["jql"]
    write_json(document, paths.issues)
    write_json(stats, paths.stats)
    jira_infographic.render(stats, paths.image, title, top)
    return paths


def run(args: argparse.Namespace) -> int:
    try:
        jira = client_from_env("jira", args.jira_url, args.jira_token, args.timeout)
        issues = jira_query.search_issues(jira, args.jql, args.limit)
    except AtlassianError as error:
        print(f"handy jira-report: {error}")
        return 1
    try:
        paths = build_artifacts(
            jira_query.build_document(args.jql, issues),
            args.out_dir,
            args.name,
            args.buckets,
            args.title,
            args.top,
        )
    except (ValueError, OSError) as error:
        print(f"handy jira-report: {error}")
        return 1
    print(f"fetched {len(issues)} issues")
    print(f"wrote {paths.issues}, {paths.stats} and {paths.image}")

    if not args.page_id:
        return 0
    try:
        confluence = client_from_env(
            "confluence", args.confluence_url, args.confluence_token, args.timeout
        )
        attachment_id, replaced = confluence_publish.upload_attachment(
            confluence,
            args.page_id,
            paths.image.name,
            paths.image.read_bytes(),
            comment=f"Updated by handy jira-report on {datetime.now(UTC):%Y-%m-%d}",
        )
        edited = confluence_publish.ensure_embedded(confluence, args.page_id, paths.image.name)
    except (AtlassianError, KeyError) as error:
        print(f"handy jira-report: {error}")
        return 1
    verb = "replaced" if replaced else "attached"
    print(f"{verb} {paths.image.name} on page {args.page_id} (attachment {attachment_id})")
    if edited:
        print("added an image macro to the page body")
    return 0
