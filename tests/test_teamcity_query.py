import argparse
from datetime import UTC, datetime, timedelta

import pytest

from handy.tools import teamcity_query

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


class FakeTeamCity:
    """Serves canned build pages and records the params it was asked for."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return self.pages.pop(0) if self.pages else {"build": []}


def raw_build(build_id=1, **overrides):
    build = {
        "id": build_id,
        "number": "42",
        "status": "SUCCESS",
        "state": "finished",
        "buildTypeId": "Gpu_Cts",
        "branchName": "main",
        "queuedDate": "20261117T100000+0000",
        "startDate": "20261117T100500+0000",
        "finishDate": "20261117T103000+0000",
        "webUrl": "https://tc/build/1",
        "buildType": {"name": "CTS", "projectName": "GPU"},
        "agent": {"name": "agent-4", "pool": {"name": "boards"}},
        "triggered": {"user": {"username": "ci-bot"}},
    }
    build.update(overrides)
    return build


def filter_args(**overrides):
    values = {
        "since": "7d",
        "project": None,
        "build_type": None,
        "user": None,
        "personal": "any",
        "branch": "default:any",
        "locator": None,
        "limit": 5000,
        "page_size": 500,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("36h", timedelta(hours=36)), ("7d", timedelta(days=7)), (" 2W ", timedelta(weeks=2))],
)
def test_parse_since_accepts_hours_days_weeks(spec, expected):
    assert teamcity_query.parse_since(spec) == expected


@pytest.mark.parametrize("spec", ["", "7", "d", "0d", "-1d", "7m", "1.5d"])
def test_parse_since_rejects_nonsense(spec):
    with pytest.raises(ValueError, match="--since"):
        teamcity_query.parse_since(spec)


def test_locator_spells_out_what_the_default_filter_would_hide():
    locator = teamcity_query.build_locator(NOW)
    assert locator == (
        "state:finished,personal:any,canceled:any,failedToStart:any,branch:default:any,"
        "finishDate:(date:20261117T120000+0000,condition:after)"
    )


def test_locator_adds_optional_dimensions():
    locator = teamcity_query.build_locator(
        NOW,
        personal="true",
        project="Gpu",
        build_type="Gpu_Cts",
        user="devans",
        branch="default:true",
        extra=",tag:nightly,",
    )
    assert "personal:true" in locator
    assert "branch:default:true" in locator
    assert "affectedProject:(id:Gpu)" in locator
    assert "buildType:(id:Gpu_Cts)" in locator
    assert "user:(username:devans)" in locator
    assert locator.endswith(",tag:nightly")


def test_normalize_flattens_teamcitys_nesting():
    assert teamcity_query.normalize_build(raw_build()) == {
        "id": 1,
        "number": "42",
        "build_type_id": "Gpu_Cts",
        "build_type": "CTS",
        "project": "GPU",
        "outcome": "passed",
        "personal": False,
        "user": "ci-bot",
        "branch": "main",
        "agent": "agent-4",
        "pool": "boards",
        "queued": "2026-11-17T10:00:00+00:00",
        "started": "2026-11-17T10:05:00+00:00",
        "finished": "2026-11-17T10:30:00+00:00",
        "web_url": "https://tc/build/1",
    }


def test_normalize_tolerates_missing_fields():
    normalized = teamcity_query.normalize_build({"id": 9, "buildTypeId": "X"})
    assert normalized["build_type"] == "X"  # falls back to the id
    assert normalized["personal"] is False
    assert normalized["user"] is None
    assert normalized["pool"] is None
    assert normalized["queued"] is None


def test_personal_build_reports_its_owner_not_the_trigger():
    build = raw_build(personal=True, user={"username": "devans"})
    normalized = teamcity_query.normalize_build(build)
    assert normalized["personal"] is True
    assert normalized["user"] == "devans"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"status": "SUCCESS"}, "passed"),
        ({"status": "FAILURE"}, "failed"),
        ({"status": "UNKNOWN"}, "failed"),
        ({"status": "UNKNOWN", "canceledInfo": {"timestamp": "x"}}, "canceled"),
        ({"status": "SUCCESS", "canceledInfo": {"timestamp": "x"}}, "canceled"),
    ],
)
def test_outcome_classification(overrides, expected):
    assert teamcity_query.outcome(raw_build(**overrides)) == expected


def test_fetch_pages_until_a_short_page():
    client = FakeTeamCity([{"build": [raw_build(1), raw_build(2)]}, {"build": [raw_build(3)]}])
    builds = teamcity_query.fetch_builds(client, "state:finished", page_size=2)
    assert [build["id"] for build in builds] == [1, 2, 3]
    locators = [call[1]["locator"] for call in client.calls]
    assert locators == ["state:finished,start:0,count:2", "state:finished,start:2,count:2"]
    assert client.calls[0][0] == "/app/rest/builds"
    assert client.calls[0][1]["fields"] == teamcity_query.FIELDS


def test_fetch_respects_the_limit():
    client = FakeTeamCity([{"build": [raw_build(1), raw_build(2)]}])
    builds = teamcity_query.fetch_builds(client, "x", limit=2, page_size=50)
    assert len(builds) == 2
    assert client.calls[0][1]["locator"].endswith("count:2")
    assert len(client.calls) == 1


def test_fetch_stops_on_an_empty_page():
    assert teamcity_query.fetch_builds(FakeTeamCity([{}]), "x") == []


def test_document_wraps_the_builds():
    document = teamcity_query.build_document("loc", NOW, [{"id": 1}], fetched_at=NOW)
    assert document == {
        "locator": "loc",
        "since": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
        "count": 1,
        "builds": [{"id": 1}],
    }


def test_query_ties_the_flags_to_the_fetch():
    client = FakeTeamCity([{"build": [raw_build(1, personal=True)]}])
    document = teamcity_query.query(client, filter_args(since="1d", personal="true"), now=NOW)
    assert document["count"] == 1
    assert document["since"] == (NOW - timedelta(days=1)).isoformat()
    assert "personal:true" in document["locator"]
    assert "date:20261116T120000+0000" in client.calls[0][1]["locator"]


def test_run_reports_a_bad_since(capsys):
    args = filter_args(since="soon", url="https://tc", token="t", timeout=1.0, output=None)
    assert teamcity_query.run(args) == 1
    assert "handy teamcity-query: bad --since" in capsys.readouterr().out
