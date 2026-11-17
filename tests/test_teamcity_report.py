import json
from datetime import UTC, datetime, timedelta

from handy.tools import teamcity_report

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def document(count=6):
    builds = []
    for index in range(count):
        queued = NOW - timedelta(days=index % 3, hours=1)
        builds.append(
            {
                "outcome": "passed" if index % 2 else "failed",
                "personal": index % 3 == 0,
                "build_type": "CTS",
                "pool": f"pool-{index % 2}",
                "queued": queued.isoformat(),
                "started": (queued + timedelta(minutes=index)).isoformat(),
                "finished": (queued + timedelta(minutes=30)).isoformat(),
            }
        )
    return {"locator": "state:finished", "since": NOW.isoformat(), "count": count, "builds": builds}


def test_build_artifacts_writes_all_three_files(tmp_path):
    paths = teamcity_report.build_artifacts(document(), tmp_path / "new" / "dir", name="weekly")
    assert json.loads(paths.builds.read_text())["count"] == 6
    assert json.loads(paths.stats.read_text())["total"] == 6
    assert paths.image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert paths.image.name == "weekly.png"


def test_build_artifacts_carries_the_locator_and_grouping_into_the_stats(tmp_path):
    paths = teamcity_report.build_artifacts(document(), tmp_path, group_by="pool")
    stats = json.loads(paths.stats.read_text())
    assert stats["locator"] == "state:finished"
    assert stats["group_by"] == "pool"
    names = {row["name"] for row in stats["groups"]["official"]["breakdown"]}
    assert names == {"pool-0", "pool-1"}
