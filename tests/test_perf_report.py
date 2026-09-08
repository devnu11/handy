from datetime import UTC, datetime

import pytest

from handy.tools import perf_report as report

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def test_target_wins_over_baseline():
    metric = report.summarize_metric(
        {"name": "p99", "value": 110, "target": 100, "baseline": 200}, 0.05
    )
    assert metric["against"] == "target"
    assert metric["expected"] == 100


def test_baseline_is_used_when_there_is_no_target():
    metric = report.summarize_metric({"name": "cold start", "value": 812, "baseline": 780}, 0.05)
    assert metric["against"] == "baseline"
    assert metric["deviation"] == pytest.approx(0.041, abs=0.001)


def test_higher_is_better_flips_the_sign_of_badness():
    """Throughput falling is a regression even though the number went down."""
    metric = report.summarize_metric(
        {"name": "throughput", "value": 18_400, "baseline": 19_000, "lower_is_better": False},
        0.05,
    )
    assert metric["deviation"] < 0
    assert metric["badness"] > 0
    assert metric["status"] == "warning"


def test_beating_the_target_is_good_not_a_regression():
    metric = report.summarize_metric({"name": "rss", "value": 1240, "target": 1500}, 0.05)
    assert metric["badness"] < 0
    assert metric["within_tolerance"] is True
    assert metric["status"] == "good"


@pytest.mark.parametrize(
    ("value", "status"),
    [(101, "good"), (104, "warning"), (106, "critical")],
)
def test_status_thresholds(value, status):
    metric = report.summarize_metric({"name": "m", "value": value, "target": 100}, 0.05)
    assert metric["status"] == status


def test_a_metric_can_carry_its_own_tolerance():
    metric = report.summarize_metric(
        {"name": "gc", "value": 113, "target": 100, "tolerance": 0.15}, 0.05
    )
    # Inside its own 15% tolerance, so not a regression — but past halfway
    # through it, which is worth flagging before it breaches.
    assert metric["within_tolerance"] is True
    assert metric["status"] == "warning"


def test_summarize_orders_by_badness_and_counts_regressions():
    summary = report.summarize(
        {
            "metrics": [
                {"name": "fine", "value": 100, "target": 100},
                {"name": "bad", "value": 160, "target": 100},
                {"name": "slight", "value": 103, "target": 100},
            ]
        },
        0.05,
        now=NOW,
    )
    assert [metric["name"] for metric in summary["metrics"]] == ["bad", "slight", "fine"]
    assert summary["overall"]["regressions"] == 1
    assert summary["overall"]["worst"] == "bad"
    assert summary["overall"]["within_tolerance"] == 2


def test_a_metric_with_no_expectation_does_not_explode():
    metric = report.summarize_metric({"name": "orphan", "value": 42}, 0.05)
    assert metric["deviation"] == 0.0
    assert metric["status"] == "good"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.947, "0.947"), (4.2, "4.20"), (41.25, "41.2"), (18_400, "18,400")],
)
def test_measurements_keep_enough_precision_to_differ(value, expected):
    assert report._measure(value, "") == expected


def test_render_writes_a_png(tmp_path):
    summary = report.summarize(
        {
            "source": "run 1",
            "metrics": [
                {"name": "p99", "value": 268, "unit": "ms", "target": 250},
                {"name": "rss", "value": 1240, "unit": "MB", "target": 1500},
            ],
        },
        0.05,
        now=NOW,
    )
    output = report.render(summary, tmp_path / "perf.png")
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_handles_no_metrics(tmp_path):
    assert report.render(report.summarize([], now=NOW), tmp_path / "empty.png").is_file()
