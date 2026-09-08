"""Render bucketized Jira stats as a single PNG infographic for Confluence."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from handy.util import read_json

HELP = "render bucketized Jira stats as a PNG infographic"

# Palette: the validated defaults from the data-viz reference palette, light
# surface. Age bands are ordered categories, so they take the blue ordinal ramp
# wherever they appear; the average-age panel is a different measure, so it
# takes categorical slot 2 (orange).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#7a7873"
HAIRLINE = "#e3e2de"
SERIES_AGE = "#eb6834"

# Longest component name drawn before it is elided, shared by both bar panels.
LABEL_LIMIT = 20

# Gap between stacked segments, as a fraction of the widest bar (~2px here).
SEGMENT_GAP = 0.004

# Ceiling on bar thickness. Without it a bar is a fixed fraction of its row, so
# a query returning a handful of components draws fat, heavy bars. Longer lists
# fall back to the fraction and thin out as usual.
BAR_INCHES = 0.36

# Rows the component panel always reserves, so a short list keeps a sane row
# pitch instead of stretching a handful of bars over the whole panel.
MIN_COMPONENT_ROWS = 6

# Blue ordinal ramp, steps 250-700. Age bands are ordered categories, so they
# get the ramp; it starts at 250 because anything lighter fails contrast on a
# light surface.
BLUE_ORDINAL = (
    "#86b6ef",
    "#6da7ec",
    "#5598e7",
    "#3987e5",
    "#2a78d6",
    "#256abf",
    "#1c5cab",
    "#184f95",
    "#104281",
    "#0d366b",
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="stats JSON from `handy jira-stats` (default: stdin)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("jira-report.png"),
        help="PNG to write (default: %(default)s)",
    )
    parser.add_argument("--title", default="Jira issue report", help="headline on the graphic")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="how many components to chart (default: 10)",
    )
    parser.add_argument("--dpi", type=int, default=130, help="output resolution (default: 130)")


def ordinal_colors(count: int) -> list[str]:
    """Pick `count` evenly spaced steps from the blue ordinal ramp."""
    if count <= 0:
        return []
    if count == 1:
        return [BLUE_ORDINAL[len(BLUE_ORDINAL) // 2]]
    last = len(BLUE_ORDINAL) - 1
    return [BLUE_ORDINAL[round(index * last / (count - 1))] for index in range(count)]


def render(
    stats: dict[str, Any],
    output: Path,
    title: str = "Jira issue report",
    top: int = 10,
    dpi: int = 130,
) -> Path:
    """Draw the infographic and write it to `output`, returning the path."""
    # Imported here, not at module scope: the CLI imports every tool module to
    # build its parser, and matplotlib is far too slow to pay for on every run.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(12, 8.2), dpi=dpi, facecolor=SURFACE)
    _draw_header(figure, stats, title)

    if not stats.get("counted"):
        figure.text(
            0.5,
            0.42,
            "No issues matched this query.",
            ha="center",
            fontsize=16,
            color=INK_SECONDARY,
        )
    else:
        grid = figure.add_gridspec(
            2, 2, left=0.185, right=0.965, top=0.66, bottom=0.09, hspace=0.58, wspace=0.34
        )
        components = stats.get("components", [])[:top]
        bands = [bucket["label"] for bucket in stats.get("age_buckets", [])]
        _draw_components(figure.add_subplot(grid[:, 0]), components, bands)
        _draw_age_buckets(figure.add_subplot(grid[0, 1]), stats.get("age_buckets", []))
        _draw_average_age(figure.add_subplot(grid[1, 1]), components)

    _draw_footer(figure, stats)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=dpi, facecolor=SURFACE)
    plt.close(figure)
    return output


def _draw_header(figure: Any, stats: dict[str, Any], title: str) -> None:
    figure.text(0.035, 0.945, title, fontsize=21, fontweight="bold", color=INK, va="top")
    subtitle = _truncate(stats.get("jql", "") or "all issues in the input", 120)
    figure.text(0.035, 0.895, subtitle, fontsize=10, color=INK_SECONDARY, va="top")

    overall = stats.get("overall", {})
    tiles = [
        ("Issues", f"{stats.get('counted', 0):,}", True),
        ("Average age", f"{overall.get('average_age_days', 0):.1f} days", False),
        ("Median age", f"{overall.get('median_age_days', 0):.1f} days", False),
        ("Components", f"{len(stats.get('components', [])):,}", False),
    ]
    for index, (label, value, hero) in enumerate(tiles):
        x = 0.035 + index * 0.235
        figure.text(x, 0.815, label, fontsize=10, color=INK_MUTED, va="top")
        figure.text(
            x,
            0.785,
            value,
            fontsize=30 if hero else 19,
            fontweight="bold" if hero else "normal",
            color=INK,
            va="top",
        )
    from matplotlib.lines import Line2D

    figure.add_artist(Line2D([0.035, 0.965], [0.705, 0.705], color=HAIRLINE, linewidth=1))


def _draw_components(axes: Any, components: list[dict[str, Any]], bands: list[str]) -> None:
    """Stacked bars: one row per component, segmented by age band."""
    labels = [_truncate(item["name"], LABEL_LIMIT) for item in components]
    totals = [item["count"] for item in components]
    positions = range(len(components))
    rows = max(len(components), MIN_COMPONENT_ROWS)
    thickness = _bar_thickness(axes, rows)
    widest = max(totals) if totals else 1
    # White doing the separating: a small gap in the surface color between
    # segments, rather than a stroke drawn around each one.
    gap = widest * SEGMENT_GAP

    cursors = [0.0] * len(components)
    for band, color in zip(bands, ordinal_colors(len(bands)), strict=True):
        widths = [item["age_buckets"].get(band, 0) for item in components]
        drawn = [width - gap if width > gap * 2 else width for width in widths]
        axes.barh(positions, drawn, left=cursors, height=thickness, color=color, label=band)
        cursors = [start + width for start, width in zip(cursors, widths, strict=True)]

    for position, total in zip(positions, totals, strict=True):
        axes.text(
            total + widest * 0.02,
            position,
            f"{total:,}",
            va="center",
            fontsize=9,
            color=INK_SECONDARY,
        )
    axes.set_yticks(list(positions), labels, fontsize=9.5, color=INK)
    axes.set_ylim(rows - 0.5, -0.5)
    axes.set_xlim(0, widest * 1.14 or 1)
    axes.set_xticks([])
    _strip_chrome(axes, baseline="left")
    _band_legend(axes, len(bands))
    _panel_title(axes, "Issues by component, split by age in days")


def _bar_thickness(axes: Any, count: int, along: str = "height") -> float:
    """Thickness of one bar as a fraction of its band, capped at BAR_INCHES."""
    figure = axes.get_figure()
    position = axes.get_position()
    if along == "height":
        span = position.height * figure.get_figheight()
    else:
        span = position.width * figure.get_figwidth()
    band = span / max(count, 1)
    return min(0.5, BAR_INCHES / band) if band else 0.5


def _band_legend(axes: Any, band_count: int) -> None:
    """Age bands ride a legend, so identity is never carried by color alone."""
    if not band_count:
        return
    # Below the plot, not above it: the panel title owns the top edge.
    legend = axes.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.015),
        ncols=band_count,
        frameon=False,
        fontsize=9,
        handlelength=0.9,
        handleheight=0.9,
        columnspacing=1.4,
        handletextpad=0.5,
    )
    for text in legend.get_texts():
        text.set_color(INK_SECONDARY)


def _draw_average_age(axes: Any, components: list[dict[str, Any]]) -> None:
    oldest = sorted(components, key=lambda item: -item["average_age_days"])
    labels = [_truncate(item["name"], LABEL_LIMIT) for item in oldest]
    ages = [item["average_age_days"] for item in oldest]
    _horizontal_bars(axes, labels, ages, SERIES_AGE, [f"{value:.0f}d" for value in ages])
    _panel_title(axes, "Average age by component (days)")


def _draw_age_buckets(axes: Any, buckets: list[dict[str, Any]]) -> None:
    counts = [bucket["count"] for bucket in buckets]
    labels = [bucket["label"] for bucket in buckets]
    if not counts:
        axes.set_axis_off()
        return
    positions = range(len(counts))
    width = _bar_thickness(axes, len(counts), along="width")
    axes.bar(positions, counts, width=width, color=ordinal_colors(len(counts)))
    for position, bucket in zip(positions, buckets, strict=True):
        axes.text(
            position,
            bucket["count"] + (max(counts) or 1) * 0.04,
            f"{bucket['count']:,}  ({bucket['share']:.0%})",
            ha="center",
            fontsize=9,
            color=INK_SECONDARY,
        )
    axes.set_xticks(list(positions), labels, fontsize=9.5, color=INK)
    axes.set_xlim(-0.65, len(counts) - 0.35)
    axes.set_ylim(0, (max(counts) or 1) * 1.28)
    axes.set_yticks([])
    axes.set_xlabel("days since creation", fontsize=9, color=INK_MUTED, labelpad=6)
    _strip_chrome(axes)
    _panel_title(axes, "Issues by age")


def _horizontal_bars(
    axes: Any, labels: list[str], values: list[float], color: str, value_labels: list[str]
) -> None:
    positions = range(len(labels))
    axes.barh(positions, values, height=_bar_thickness(axes, len(labels)), color=color)
    widest = max(values) if values else 1
    for position, value, text in zip(positions, values, value_labels, strict=True):
        # Labels ride outside the bar end, so a short bar never clips its value.
        axes.text(
            value + widest * 0.02,
            position,
            text,
            va="center",
            fontsize=9,
            color=INK_SECONDARY,
        )
    axes.set_yticks(list(positions), labels, fontsize=9.5, color=INK)
    axes.invert_yaxis()
    axes.set_xlim(0, widest * 1.22 or 1)
    axes.set_xticks([])
    _strip_chrome(axes, baseline="left")


def _panel_title(axes: Any, text: str) -> None:
    axes.set_title(text, fontsize=11.5, fontweight="bold", color=INK, loc="left", pad=12)


def _strip_chrome(axes: Any, baseline: str = "bottom") -> None:
    axes.set_facecolor(SURFACE)
    for side, spine in axes.spines.items():
        spine.set_visible(side == baseline)
        spine.set_color(HAIRLINE)
        spine.set_linewidth(1)
    axes.tick_params(length=0, colors=INK_SECONDARY)


def _draw_footer(figure: Any, stats: dict[str, Any]) -> None:
    stamp = _format_stamp(stats.get("generated_at", ""))
    note = "An issue with several components is counted under each of them."
    skipped = stats.get("skipped") or []
    if skipped:
        note += f" {len(skipped)} issue(s) had no usable creation date."
    figure.text(0.035, 0.022, note, fontsize=8.5, color=INK_MUTED)
    figure.text(0.965, 0.022, stamp, fontsize=8.5, color=INK_MUTED, ha="right")


def _format_stamp(value: str) -> str:
    try:
        return f"generated {datetime.fromisoformat(value):%Y-%m-%d %H:%M %Z}".strip()
    except ValueError:
        return ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def run(args: argparse.Namespace) -> int:
    try:
        stats = read_json(args.input)
    except (OSError, ValueError) as error:
        print(f"handy jira-infographic: cannot read stats: {error}")
        return 1
    if not isinstance(stats, dict) or "age_buckets" not in stats:
        print("handy jira-infographic: expected a document from `handy jira-stats`")
        return 1
    output = render(stats, args.output, args.title, args.top, args.dpi)
    print(f"wrote {output}")
    return 0
