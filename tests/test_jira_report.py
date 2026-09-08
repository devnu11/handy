from datetime import UTC, datetime, timedelta

from handy.tools import jira_report

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def document(count=5):
    return {
        "jql": "project = FOO",
        "fetched_at": NOW.isoformat(),
        "count": count,
        "issues": [
            {
                "key": f"FOO-{index}",
                "components": ["api" if index % 2 else "web"],
                "created": (NOW - timedelta(days=index * 9)).isoformat(),
            }
            for index in range(count)
        ],
    }


def test_build_artifacts_writes_all_three_files(tmp_path):
    paths = jira_report.build_artifacts(document(), tmp_path, name="weekly", now=NOW)
    assert paths.issues.is_file()
    assert paths.stats.is_file()
    assert paths.image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert paths.image.name == "weekly.png"


def test_build_artifacts_carries_the_jql_into_the_stats(tmp_path):
    import json

    paths = jira_report.build_artifacts(document(), tmp_path, now=NOW)
    stats = json.loads(paths.stats.read_text())
    assert stats["jql"] == "project = FOO"
    assert stats["counted"] == 5


def test_build_artifacts_creates_the_output_directory(tmp_path):
    paths = jira_report.build_artifacts(document(), tmp_path / "new" / "dir", now=NOW)
    assert paths.stats.is_file()


def test_build_artifacts_honours_custom_buckets(tmp_path):
    import json

    paths = jira_report.build_artifacts(document(), tmp_path, buckets="0-1,2+", now=NOW)
    stats = json.loads(paths.stats.read_text())
    assert [bucket["label"] for bucket in stats["age_buckets"]] == ["0-1", "2+"]
