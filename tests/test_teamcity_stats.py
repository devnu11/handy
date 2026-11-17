import argparse
from datetime import UTC, datetime, timedelta

import pytest

from handy.tools import teamcity_stats as stats_tool
from handy.util import write_json

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def build(outcome="passed", personal=False, wait=60, day=0, **extra):
    """A normalized build that queued for `wait` seconds, `day` days before NOW."""
    queued = NOW - timedelta(days=day, hours=1)
    started = queued + timedelta(seconds=wait)
    record = {
        "outcome": outcome,
        "personal": personal,
        "build_type": "CTS",
        "project": "GPU",
        "pool": "boards",
        "user": "ci-bot",
        "queued": queued.isoformat(),
        "started": started.isoformat(),
        "finished": (started + timedelta(minutes=10)).isoformat(),
    }
    record.update(extra)
    return record


def test_pass_rate_ignores_nothing_decided():
    assert stats_tool.pass_rate(0, 0) is None
    assert stats_tool.pass_rate(3, 1) == 0.75


def test_canceled_builds_are_counted_but_excluded_from_the_rate():
    result = stats_tool.tally([build(), build("failed"), build("canceled"), build("canceled")])
    assert (result["builds"], result["passed"], result["failed"], result["canceled"]) == (
        4,
        1,
        1,
        2,
    )
    assert result["pass_rate"] == 0.5


def test_queue_seconds_is_start_minus_queued():
    assert stats_tool.queue_seconds(build(wait=90)) == 90.0


@pytest.mark.parametrize(
    "overrides",
    [{"queued": None}, {"started": None}, {"queued": "junk"}, {"started": "2000-01-01T00:00:00"}],
)
def test_queue_seconds_is_none_for_unusable_stamps(overrides):
    assert stats_tool.queue_seconds(build(**overrides)) is None


def test_percentile_is_nearest_rank():
    values = [float(v) for v in range(1, 11)]
    assert stats_tool.percentile(values, 0.9) == 9.0
    assert stats_tool.percentile(values, 0.5) == 5.0
    assert stats_tool.percentile([7.0], 0.9) == 7.0
    with pytest.raises(ValueError):
        stats_tool.percentile([], 0.9)


def test_queue_stats_summarize_the_waits():
    result = stats_tool.queue_stats([10.0, 20.0, 30.0, 100.0])
    assert result == {"count": 4, "mean_s": 40.0, "median_s": 25.0, "p90_s": 100.0, "max_s": 100.0}


def test_queue_stats_of_nothing_are_nulls_not_zeros():
    result = stats_tool.queue_stats([])
    assert result["count"] == 0
    assert result["median_s"] is None


def test_summarize_splits_official_from_personal():
    result = stats_tool.summarize(
        [
            build(wait=60),
            build("failed", wait=120),
            build(personal=True, wait=600),
        ],
        now=NOW,
    )
    official, personal = result["groups"]["official"], result["groups"]["personal"]
    assert result["total"] == 3
    assert (official["builds"], official["pass_rate"]) == (2, 0.5)
    assert official["queue"]["median_s"] == 90.0
    assert (personal["builds"], personal["pass_rate"]) == (1, 1.0)
    assert personal["queue"]["median_s"] == 600.0


def test_breakdown_is_keyed_by_the_chosen_field_and_sorted_by_count():
    builds = [build(pool="b"), build(pool="b"), build(pool="a"), build(pool=None)]
    result = stats_tool.summarize(builds, group_by="pool", now=NOW)
    rows = result["groups"]["official"]["breakdown"]
    assert [(row["name"], row["builds"]) for row in rows] == [
        ("b", 2),
        (stats_tool.UNKNOWN, 1),
        ("a", 1),
    ]
    assert result["group_by"] == "pool"


def test_daily_series_is_chronological_and_skips_undated_builds():
    builds = [build(day=0), build("failed", day=2), build(day=2), build(finished=None)]
    daily = stats_tool.summarize(builds, now=NOW)["groups"]["official"]["daily"]
    assert [(row["name"], row["builds"], row["pass_rate"]) for row in daily] == [
        ("2026-11-15", 2, 0.5),
        ("2026-11-17", 1, 1.0),
    ]


def test_finish_day_is_reported_in_utc():
    assert stats_tool.finish_day({"finished": "2026-11-16T22:30:00-06:00"}) == "2026-11-17"


def test_empty_input_produces_empty_groups():
    result = stats_tool.summarize([], now=NOW)
    for name in stats_tool.GROUPS:
        group = result["groups"][name]
        assert group["builds"] == 0
        assert group["pass_rate"] is None
        assert group["breakdown"] == []
        assert group["daily"] == []


def test_stats_for_document_carries_the_query_context():
    document = {"locator": "loc", "since": "2026-11-10T12:00:00+00:00", "builds": [build()]}
    result = stats_tool.stats_for_document(document)
    assert result["locator"] == "loc"
    assert result["since"] == "2026-11-10T12:00:00+00:00"
    assert result["total"] == 1


def test_stats_for_document_accepts_a_bare_list():
    assert stats_tool.stats_for_document([build()])["total"] == 1


def test_stats_for_document_rejects_other_shapes():
    with pytest.raises(ValueError, match="teamcity-query"):
        stats_tool.stats_for_document({"issues": []})


def test_run_writes_stats(tmp_path, capsys):
    source, target = tmp_path / "builds.json", tmp_path / "stats.json"
    write_json({"builds": [build(), build(personal=True)]}, source)
    args = argparse.Namespace(input=source, output=target, group_by="build-type")
    assert stats_tool.run(args) == 0
    assert "2 builds" in capsys.readouterr().out
    assert target.is_file()


def test_run_reports_an_unreadable_input(tmp_path, capsys):
    args = argparse.Namespace(input=tmp_path / "missing.json", output=None, group_by="build-type")
    assert stats_tool.run(args) == 1
    assert "handy teamcity-stats:" in capsys.readouterr().out
