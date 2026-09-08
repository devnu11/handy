from datetime import UTC, datetime, timedelta

from handy.tools import jira_infographic as graphic
from handy.tools import jira_stats

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def sample_stats(count=12):
    components = ["api", "web", "docs", "infra"]
    issues = [
        {
            "key": f"FOO-{index}",
            "components": [components[index % len(components)]],
            "created": (NOW - timedelta(days=index * 11)).isoformat(),
        }
        for index in range(count)
    ]
    stats = jira_stats.summarize(issues, now=NOW)
    stats["jql"] = "project = FOO AND resolution = Unresolved"
    return stats


def test_ordinal_colors_span_the_ramp():
    assert graphic.ordinal_colors(0) == []
    assert graphic.ordinal_colors(1) == [graphic.BLUE_ORDINAL[len(graphic.BLUE_ORDINAL) // 2]]
    four = graphic.ordinal_colors(4)
    assert four[0] == graphic.BLUE_ORDINAL[0]
    assert four[-1] == graphic.BLUE_ORDINAL[-1]
    assert len(set(four)) == 4  # every band gets its own step


def test_ordinal_colors_never_runs_off_the_ramp():
    colors = graphic.ordinal_colors(len(graphic.BLUE_ORDINAL) + 5)
    assert set(colors) <= set(graphic.BLUE_ORDINAL)


def test_render_writes_a_png(tmp_path):
    output = graphic.render(sample_stats(), tmp_path / "report.png")
    assert output.read_bytes()[:8] == PNG_MAGIC


def test_render_creates_missing_directories(tmp_path):
    output = graphic.render(sample_stats(), tmp_path / "nested" / "deep" / "report.png")
    assert output.is_file()


def test_render_handles_an_empty_result(tmp_path):
    stats = jira_stats.summarize([], now=NOW)
    output = graphic.render(stats, tmp_path / "empty.png")
    assert output.read_bytes()[:8] == PNG_MAGIC


def test_render_caps_the_component_list(tmp_path):
    # Nothing to assert on the pixels; this just has to not raise with top < len.
    output = graphic.render(sample_stats(40), tmp_path / "capped.png", top=2)
    assert output.is_file()


def test_render_stacks_each_component_by_age_band(tmp_path):
    stats = sample_stats()
    bands = {bucket["label"] for bucket in stats["age_buckets"]}
    # The per-component breakdown is what the stacked bars are drawn from.
    assert all(bands == set(item["age_buckets"]) for item in stats["components"])
    assert all(sum(item["age_buckets"].values()) == item["count"] for item in stats["components"])
    assert graphic.render(stats, tmp_path / "stacked.png").is_file()


def test_render_survives_a_component_missing_a_band(tmp_path):
    stats = sample_stats()
    stats["components"][0]["age_buckets"].pop("90+")
    assert graphic.render(stats, tmp_path / "sparse.png").is_file()


def test_render_survives_stats_with_no_bands(tmp_path):
    stats = sample_stats()
    stats["age_buckets"] = []
    assert graphic.render(stats, tmp_path / "bandless.png").is_file()


def test_truncate_keeps_long_component_names_short():
    assert graphic._truncate("short", 10) == "short"
    assert len(graphic._truncate("a-very-long-component-name", 10)) == 10


def test_run_rejects_a_document_that_isnt_stats(tmp_path, capsys):
    import argparse

    from handy.util import write_json

    path = tmp_path / "issues.json"
    write_json({"issues": []}, path)
    args = argparse.Namespace(input=path, output=tmp_path / "out.png", title="t", top=5, dpi=100)
    assert graphic.run(args) == 1
    assert "jira-stats" in capsys.readouterr().out
