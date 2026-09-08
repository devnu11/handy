from datetime import UTC, datetime

import pytest

from handy.tools import conformance_report as report

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def suite(name, total, passed, **extra):
    return {"name": name, "total": total, "passed": passed, **extra}


@pytest.mark.parametrize(
    ("executed", "expected"),
    [(0, 1), (20, 1), (220, 1), (9_600, 2), (126_400, 4), (2_100_000, 5), (50_000_000, 6)],
)
def test_decimals_track_suite_size(executed, expected):
    """A percentage has to be able to show one failure, whatever the size."""
    assert report.decimals_for(executed) == expected


def test_one_failure_is_visible_in_the_percentage():
    big = report.summarize_suite(suite("big", 2_100_000, 2_099_999), 0.999)
    clean = report.summarize_suite(suite("big", 2_100_000, 2_100_000), 0.999)
    assert big["pass_rate_text"] != clean["pass_rate_text"]


def test_failed_is_derived_when_absent():
    computed = report.summarize_suite(suite("core", 100, 97, skipped=1), 0.9)
    assert computed["failed"] == 2
    assert computed["executed"] == 99


def test_passed_is_derived_from_failures():
    computed = report.summarize_suite({"name": "core", "total": 50, "failed": 5}, 0.9)
    assert computed["passed"] == 45


def test_budget_is_the_allowance_at_the_floor():
    computed = report.summarize_suite(suite("core", 10_000, 9_995), 0.999)
    assert computed["budget"] == pytest.approx(10.0)
    assert computed["allowed_failures"] == 10
    assert computed["budget_consumed"] == pytest.approx(0.5)
    assert computed["status"] == "good"


def test_a_small_suite_has_no_tolerance_at_all():
    """20 tests at a 99.9% floor allows zero failures — one breaches it."""
    computed = report.summarize_suite(suite("smoke", 20, 19), 0.999)
    assert computed["zero_tolerance"] is True
    assert computed["allowed_failures"] == 0
    assert computed["breached"] is True
    assert computed["status"] == "critical"
    assert computed["budget_consumed"] is not None  # budget is 0.02, not zero


def test_a_clean_small_suite_is_not_breached():
    computed = report.summarize_suite(suite("smoke", 20, 20), 0.999)
    assert computed["breached"] is False
    assert computed["status"] == "good"


def test_nearly_spent_budget_warns_before_it_breaches():
    computed = report.summarize_suite(suite("core", 10_000, 9_991), 0.999)
    assert computed["breached"] is False
    assert computed["status"] == "warning"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("99.90000%", "99.9%"), ("99.99%", "99.99%"), ("100%", "100%"), ("95.0%", "95%")],
)
def test_floors_lose_their_trailing_zeros(text, expected):
    assert report.trim_rate(text) == expected


def test_per_suite_minimum_beats_the_default():
    computed = report.summarize_suite(suite("tls", 48_200, 48_198, minimum_pass_rate=0.9999), 0.9)
    assert computed["minimum_pass_rate"] == 0.9999
    assert computed["breached"] is False


def test_summarize_puts_the_worst_suites_first():
    summary = report.summarize(
        {
            "suites": [
                suite("clean", 1000, 1000),
                suite("broken", 1000, 900),
                suite("nearly", 10_000, 9_991),
            ]
        },
        0.999,
        now=NOW,
    )
    assert [item["name"] for item in summary["suites"]] == ["broken", "nearly", "clean"]
    assert summary["overall"]["breached"] == 1
    assert summary["overall"]["failed"] == 109


def test_summarize_accepts_a_bare_list():
    summary = report.summarize([suite("core", 100, 100)], 0.9, now=NOW)
    assert summary["overall"]["suites"] == 1


def test_render_writes_a_png(tmp_path):
    summary = report.summarize(
        {"source": "nightly", "suites": [suite("core", 2_100_000, 2_099_878), suite("s", 20, 19)]},
        0.999,
        now=NOW,
    )
    output = report.render(summary, tmp_path / "conformance.png")
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_handles_no_suites(tmp_path):
    summary = report.summarize([], 0.999, now=NOW)
    assert report.render(summary, tmp_path / "empty.png").is_file()


def test_run_reports_failure_when_a_suite_is_below_its_floor(tmp_path, capsys):
    import argparse

    from handy.util import write_json

    source = tmp_path / "results.json"
    write_json({"suites": [suite("smoke", 20, 19)]}, source)
    args = argparse.Namespace(
        input=source,
        output=tmp_path / "out.png",
        summary=tmp_path / "summary.json",
        minimum=0.999,
        title="t",
        top=12,
        dpi=100,
    )
    assert report.run(args) == 1
    assert "1 of 1 suites below their floor" in capsys.readouterr().out
    assert (tmp_path / "summary.json").is_file()
