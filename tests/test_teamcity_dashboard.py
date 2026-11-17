import argparse
from datetime import UTC, datetime, timedelta

import pytest

from handy.tools import teamcity_dashboard as dashboard
from handy.tools import teamcity_stats
from handy.util import write_json

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
BUILD_TYPES = ["CTS full", "CTS smoke", "Perf", "Image compare", "Build debug", "Build release"]


def sample_builds(count=120, personal_every=3):
    builds = []
    for index in range(count):
        personal = index % personal_every == 0
        queued = NOW - timedelta(days=index % 7, minutes=index)
        started = queued + timedelta(seconds=30 + index * (40 if personal else 7))
        builds.append(
            {
                "outcome": "failed" if index % 9 == 0 else "passed",
                "personal": personal,
                "build_type": BUILD_TYPES[index % len(BUILD_TYPES)],
                "queued": queued.isoformat(),
                "started": started.isoformat(),
                "finished": (started + timedelta(minutes=20)).isoformat(),
            }
        )
    return builds


def sample_stats(**kwargs):
    stats = teamcity_stats.summarize(sample_builds(**kwargs), now=NOW)
    stats["since"] = (NOW - timedelta(days=7)).isoformat()
    return stats


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "—"), (0, "0s"), (89, "89s"), (90, "1.5m"), (5399, "90.0m"), (5400, "1.5h")],
)
def test_format_duration(seconds, expected):
    assert dashboard.format_duration(seconds) == expected


def test_format_rate():
    assert dashboard.format_rate(None) == "—"
    assert dashboard.format_rate(0.9876) == "98.8%"


@pytest.mark.parametrize(
    ("longest", "expected"),
    [
        (0, (1.0, "seconds")),
        (119, (1.0, "seconds")),
        (120, (60.0, "minutes")),
        (7199, (60.0, "minutes")),
        (7200, (3600.0, "hours")),
    ],
)
def test_duration_scale(longest, expected):
    assert dashboard.duration_scale(longest) == expected


def test_top_rows_rank_by_builds_across_both_groups():
    groups = {
        "official": {"breakdown": [{"name": "a", "builds": 5}, {"name": "b", "builds": 4}]},
        "personal": {"breakdown": [{"name": "b", "builds": 3}, {"name": "c", "builds": 9}]},
    }
    assert dashboard.top_rows(groups, 2) == ["c", "b"]
    assert dashboard.top_rows(groups, 10) == ["c", "b", "a"]
    assert dashboard.top_rows(groups, 0) == []


def test_all_days_merges_and_sorts_both_groups():
    groups = {
        "official": {"daily": [{"name": "2026-11-16"}, {"name": "2026-11-17"}]},
        "personal": {"daily": [{"name": "2026-11-15"}, {"name": "2026-11-16"}]},
    }
    assert dashboard.all_days(groups) == ["2026-11-15", "2026-11-16", "2026-11-17"]


def test_render_writes_a_png(tmp_path):
    output = dashboard.render(sample_stats(), tmp_path / "nested" / "dash.png")
    assert output.read_bytes()[:8] == PNG_MAGIC


def test_render_handles_an_empty_result(tmp_path):
    stats = teamcity_stats.summarize([], now=NOW)
    assert dashboard.render(stats, tmp_path / "empty.png").read_bytes()[:8] == PNG_MAGIC


def test_render_handles_only_official_builds(tmp_path):
    stats = teamcity_stats.summarize([b for b in sample_builds() if not b["personal"]], now=NOW)
    assert dashboard.render(stats, tmp_path / "official.png").is_file()


def test_render_handles_a_single_build_and_day(tmp_path):
    stats = teamcity_stats.summarize(sample_builds(count=1), now=NOW)
    assert dashboard.render(stats, tmp_path / "one.png").is_file()


def test_render_handles_builds_without_usable_timestamps(tmp_path):
    builds = [{"outcome": "canceled", "personal": False, "build_type": "CTS"}]
    stats = teamcity_stats.summarize(builds, now=NOW)
    assert dashboard.render(stats, tmp_path / "bare.png").is_file()


def test_render_caps_the_rows(tmp_path):
    assert dashboard.render(sample_stats(), tmp_path / "capped.png", top=2).is_file()


def test_run_rejects_a_document_that_isnt_stats(tmp_path, capsys):
    path = tmp_path / "builds.json"
    write_json({"builds": []}, path)
    args = argparse.Namespace(input=path, output=tmp_path / "out.png", title="t", top=5, dpi=100)
    assert dashboard.run(args) == 1
    assert "teamcity-stats" in capsys.readouterr().out
