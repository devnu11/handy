from datetime import UTC, datetime, timedelta

import pytest

from handy.tools import jira_stats

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def issue(key, components, days_old, now=NOW):
    created = now - timedelta(days=days_old)
    return {
        "key": key,
        "components": components,
        "created": created.isoformat(),
    }


def test_parse_buckets_handles_ranges_and_open_ends():
    buckets = jira_stats.parse_buckets("0-7,8-30,90+")
    assert [(b.label, b.low, b.high) for b in buckets] == [
        ("0-7", 0, 7),
        ("8-30", 8, 30),
        ("90+", 90, None),
    ]


@pytest.mark.parametrize("spec", ["", "abc", "5", "1-", "-3"])
def test_parse_buckets_rejects_nonsense(spec):
    with pytest.raises(ValueError):
        jira_stats.parse_buckets(spec)


@pytest.mark.parametrize(
    "value",
    ["2024-01-15T10:23:45.000+0000", "2024-01-15T10:23:45+00:00", "2024-01-15T10:23:45"],
)
def test_parse_timestamp_accepts_jira_and_plain_iso(value):
    assert jira_stats.parse_timestamp(value).year == 2024


def test_parse_timestamp_rejects_junk():
    with pytest.raises(ValueError):
        jira_stats.parse_timestamp("last tuesday")


def test_age_is_floored_at_zero_for_future_dates():
    created = NOW.replace(year=NOW.year + 1)
    assert jira_stats.age_in_days(created, NOW) == 0


def test_summarize_buckets_by_component_and_age():
    stats = jira_stats.summarize(
        [
            issue("A-1", ["api"], 3),
            issue("A-2", ["api"], 40),
            issue("A-3", ["web"], 200),
        ],
        now=NOW,
    )
    assert stats["counted"] == 3
    assert stats["overall"]["average_age_days"] == 81.0
    assert stats["overall"]["median_age_days"] == 40.0
    assert stats["overall"]["oldest_days"] == 200

    counts = {bucket["label"]: bucket["count"] for bucket in stats["age_buckets"]}
    assert counts == {"0-7": 1, "8-30": 0, "31-90": 1, "90+": 1}

    by_name = {component["name"]: component for component in stats["components"]}
    assert by_name["api"]["count"] == 2
    assert by_name["api"]["average_age_days"] == 21.5
    assert by_name["web"]["age_buckets"]["90+"] == 1


def test_an_issue_counts_under_each_of_its_components():
    stats = jira_stats.summarize([issue("A-1", ["api", "web"], 10)], now=NOW)
    assert stats["counted"] == 1
    assert {c["name"]: c["count"] for c in stats["components"]} == {"api": 1, "web": 1}


def test_issues_without_components_land_in_unassigned():
    stats = jira_stats.summarize([issue("A-1", [], 10)], now=NOW)
    assert stats["components"][0]["name"] == jira_stats.UNASSIGNED


def test_components_are_sorted_by_count_then_name():
    stats = jira_stats.summarize(
        [issue("A-1", ["z"], 1), issue("A-2", ["z"], 1), issue("A-3", ["a"], 1)],
        now=NOW,
    )
    assert [c["name"] for c in stats["components"]] == ["z", "a"]


def test_unusable_creation_dates_are_skipped_not_fatal():
    stats = jira_stats.summarize(
        [{"key": "A-1", "components": ["api"], "created": "nope"}, {"key": "A-2"}],
        now=NOW,
    )
    assert stats["total"] == 2
    assert stats["counted"] == 0
    assert stats["skipped"] == ["A-1", "A-2"]


def test_overlapping_bands_do_not_double_count():
    bands = jira_stats.parse_buckets("0-30,10-40")
    stats = jira_stats.summarize([issue("A-1", ["api"], 20)], bands, now=NOW)
    assert [bucket["count"] for bucket in stats["age_buckets"]] == [1, 0]


def test_empty_input_produces_zeroed_stats():
    stats = jira_stats.summarize([], now=NOW)
    assert stats["counted"] == 0
    assert stats["components"] == []
    assert all(bucket["count"] == 0 for bucket in stats["age_buckets"])
