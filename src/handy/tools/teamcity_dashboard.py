"""Render TeamCity pass rates and queue times as a single PNG dashboard."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from handy.tools._viz import (
    HAIRLINE,
    INK,
    INK_MUTED,
    INK_SECONDARY,
    SERIES_1,
    SERIES_2,
    SURFACE,
    bar_thickness,
    figure_and_axes,
    footer,
    format_stamp,
    header,
    panel_title,
    strip_chrome,
    truncate,
    visible_lengths,
)
from handy.util import read_json

HELP = "render TeamCity pass-rate and queue-time stats as a PNG dashboard"

# (stats group key, legend label, color). Order is fixed so a group keeps its
# color whether or not the other one has any builds.
SERIES = (("official", "Official", SERIES_1), ("personal", "Personal", SERIES_2))

LABEL_LIMIT = 24

# Rows the bar panels always reserve, so two or three rows keep a sane pitch.
MIN_ROWS = 4

# Most date labels drawn under a trend panel before they are thinned out.
MAX_DAY_LABELS = 7

# Axis length as a multiple of the longest bar: the rest is room for end labels.
LABEL_ROOM = 1.6

# Thickest a single bar of a pair may be, in row units; leaves air between rows.
PAIR_BAR_LIMIT = 0.36


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="stats JSON from `handy teamcity-stats` (default: stdin)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("teamcity-dashboard.png"),
        help="PNG to write (default: %(default)s)",
    )
    parser.add_argument("--title", default="TeamCity dashboard", help="headline on the graphic")
    parser.add_argument("--top", type=int, default=6, help="rows to chart per panel (default: 6)")
    parser.add_argument("--dpi", type=int, default=130, help="output resolution (default: 130)")


def format_duration(seconds: float | None) -> str:
    """Short human form of a wait: 45s, 4.2m, 1.3h; an em dash when unknown."""
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def format_rate(rate: float | None) -> str:
    """A pass rate as a percentage; an em dash when no build was decided."""
    return "—" if rate is None else f"{rate:.1%}"


def duration_scale(longest: float) -> tuple[float, str]:
    """Pick the divisor and unit that keep an axis of waits in readable numbers."""
    if longest >= 2 * 3600:
        return 3600.0, "hours"
    if longest >= 120:
        return 60.0, "minutes"
    return 1.0, "seconds"


def top_rows(groups: dict[str, Any], top: int) -> list[str]:
    """Breakdown names ranked by builds across both groups, busiest first."""
    totals: dict[str, int] = {}
    for group in groups.values():
        for row in group.get("breakdown", []):
            totals[row["name"]] = totals.get(row["name"], 0) + row["builds"]
    ranked = sorted(totals, key=lambda name: (-totals[name], name))
    return ranked[: max(top, 0)]


def all_days(groups: dict[str, Any]) -> list[str]:
    """Every day either group has builds on, oldest first."""
    return sorted({row["name"] for group in groups.values() for row in group.get("daily", [])})


def render(
    stats: dict[str, Any],
    output: Path,
    title: str = "TeamCity dashboard",
    top: int = 6,
    dpi: int = 130,
) -> Path:
    """Draw the dashboard and write it to `output`, returning the path."""
    plt, figure = figure_and_axes(dpi=dpi)
    groups = stats.get("groups", {})
    _draw_header(figure, stats, groups, title)
    if stats.get("total"):
        _draw_panels(figure, stats, groups, top)
    else:
        figure.text(0.5, 0.42, "No builds matched.", ha="center", fontsize=16, color=INK_SECONDARY)
    _draw_footer(figure, stats)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=dpi, facecolor=SURFACE)
    plt.close(figure)
    return output


def _draw_header(figure: Any, stats: dict[str, Any], groups: dict[str, Any], title: str) -> None:
    tiles = [
        (f"{label} pass rate", format_rate(groups.get(key, {}).get("pass_rate")))
        for key, label, _ in SERIES
    ]
    tiles += [
        (f"{label} median queue", format_duration(_queue(groups.get(key, {}), "median_s")))
        for key, label, _ in SERIES
    ]
    header(figure, title, _subtitle(stats, groups), tiles)


def _subtitle(stats: dict[str, Any], groups: dict[str, Any]) -> str:
    counts = ", ".join(
        f"{groups.get(key, {}).get('builds', 0):,} {label.lower()}" for key, label, _ in SERIES
    )
    since = format_stamp(stats.get("since", ""), prefix="finished since")
    return truncate(" · ".join(part for part in (f"{counts} builds", since) if part), 120)


def _queue(group: dict[str, Any], key: str) -> float | None:
    return (group.get("queue") or {}).get(key)


def _draw_panels(figure: Any, stats: dict[str, Any], groups: dict[str, Any], top: int) -> None:
    grid = figure.add_gridspec(
        2, 2, left=0.165, right=0.965, top=0.62, bottom=0.1, hspace=0.62, wspace=0.34
    )
    names, days = top_rows(groups, top), all_days(groups)
    noun = stats.get("group_by", "build-type").replace("-", " ")
    _draw_pass_bars(figure.add_subplot(grid[0, 0]), groups, names, noun)
    _draw_queue_bars(figure.add_subplot(grid[0, 1]), groups, names, noun)
    _draw_pass_trend(figure.add_subplot(grid[1, 0]), groups, days)
    _draw_queue_trend(figure.add_subplot(grid[1, 1]), groups, days)
    _draw_legend(figure)


def _draw_legend(figure: Any) -> None:
    """One legend for all four panels: series identity never rides on color alone."""
    from matplotlib.patches import Patch

    handles = [Patch(color=color, label=label) for _, label, color in SERIES]
    legend = figure.legend(
        handles=handles,
        loc="upper right",
        bbox_to_anchor=(0.965, 0.7),
        ncols=len(handles),
        frameon=False,
        fontsize=9,
        handlelength=0.9,
        handleheight=0.9,
        columnspacing=1.4,
        handletextpad=0.5,
    )
    for text in legend.get_texts():
        text.set_color(INK_SECONDARY)


def _lookup(group: dict[str, Any], section: str) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in group.get(section, [])}


def _draw_pass_bars(axes: Any, groups: dict[str, Any], names: list[str], noun: str) -> None:
    """Paired bars of pass rate per row; the label carries the failure count."""

    def value(row: dict[str, Any]) -> float | None:
        return row["pass_rate"]

    def label(row: dict[str, Any]) -> str:
        decided = row["passed"] + row["failed"]
        return f"{row['pass_rate']:.0%} · {row['failed']} of {decided} failed"

    _paired_bars(axes, groups, names, value, label, limit=1.0)
    panel_title(axes, f"Pass rate by {noun}")


def _draw_queue_bars(axes: Any, groups: dict[str, Any], names: list[str], noun: str) -> None:
    """Paired bars of median queue time per row, with p90 in the label."""

    def value(row: dict[str, Any]) -> float | None:
        return _queue(row, "median_s")

    def label(row: dict[str, Any]) -> str:
        median, p90 = _queue(row, "median_s"), _queue(row, "p90_s")
        return f"{format_duration(median)} · p90 {format_duration(p90)}"

    _paired_bars(axes, groups, names, value, label, limit=None)
    panel_title(axes, f"Median queue time by {noun}")


def _paired_bars(
    axes: Any,
    groups: dict[str, Any],
    names: list[str],
    value: Any,
    label: Any,
    limit: float | None,
) -> None:
    """One row per name with an official and a personal bar, labelled at the end.

    `value(row)` gives the bar length (None draws nothing) and `label(row)` its
    text. `limit` fixes the scale's maximum; None scales to the longest bar.
    """
    rows = max(len(names), MIN_ROWS)
    thickness = min(bar_thickness(axes, rows), PAIR_BAR_LIMIT)
    series = [
        (_bar_rows(_lookup(groups.get(key, {}), "breakdown"), names, value), color)
        for key, _, color in SERIES
    ]
    longest = limit or max((v for found, _ in series for _, v, _ in found), default=0) or 1
    for index, (found, color) in enumerate(series):
        offset = (index - 0.5) * (thickness + 0.04)
        _draw_bar_series(axes, found, color, offset, thickness, longest, label)
    axes.set_yticks(range(len(names)), [truncate(name, LABEL_LIMIT) for name in names])
    axes.tick_params(axis="y", labelsize=9, labelcolor=INK)
    axes.set_ylim(rows - 0.5, -0.5)
    axes.set_xlim(0, longest * LABEL_ROOM)
    axes.set_xticks([])
    strip_chrome(axes, baseline="left")


def _bar_rows(
    lookup: dict[str, dict[str, Any]], names: list[str], value: Any
) -> list[tuple[int, float, dict[str, Any]]]:
    """(row position, bar length, stats row) for each name this group can draw."""
    found = []
    for position, name in enumerate(names):
        row = lookup.get(name)
        length = value(row) if row else None
        if length is not None:
            found.append((position, length, row))
    return found


def _draw_bar_series(
    axes: Any,
    found: list[tuple[int, float, dict[str, Any]]],
    color: str,
    offset: float,
    thickness: float,
    longest: float,
    label: Any,
) -> None:
    if not found:
        return
    positions = [position + offset for position, _, _ in found]
    lengths = [length for _, length, _ in found]
    drawn = visible_lengths(axes, lengths, longest * LABEL_ROOM)
    axes.barh(positions, drawn, height=thickness, color=color)
    for position, length, (_, _, row) in zip(positions, lengths, found, strict=True):
        # Outside the bar end, in ink: text never wears the series color.
        axes.text(
            length + longest * 0.03,
            position,
            label(row),
            va="center",
            fontsize=8,
            color=INK_SECONDARY,
            clip_on=False,
        )


def _draw_pass_trend(axes: Any, groups: dict[str, Any], days: list[str]) -> None:
    from matplotlib.ticker import PercentFormatter

    lowest = _trend_lines(axes, groups, days, lambda row: row["pass_rate"], scale=1.0)[0]
    # A line needn't start at zero, but the range is padded so a dip keeps context.
    axes.set_ylim(max(0.0, math.floor(lowest * 10) / 10 - 0.1), 1.02)
    axes.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    panel_title(axes, "Pass rate by day")


def _draw_queue_trend(axes: Any, groups: dict[str, Any], days: list[str]) -> None:
    def median(row: dict[str, Any]) -> float | None:
        return _queue(row, "median_s")

    waits = [
        wait
        for group in groups.values()
        for wait in map(median, group.get("daily", []))
        if wait is not None
    ]
    divisor, unit = duration_scale(max(waits, default=0))
    highest = _trend_lines(axes, groups, days, median, scale=divisor)[1]
    axes.set_ylim(0, highest * 1.15 or 1)
    panel_title(axes, f"Median queue time by day ({unit})")


def _trend_lines(
    axes: Any, groups: dict[str, Any], days: list[str], value: Any, scale: float
) -> tuple[float, float]:
    """Plot one line per group over `days`. Returns the (lowest, highest) point."""
    points: list[float] = []
    for key, _, color in SERIES:
        lookup = _lookup(groups.get(key, {}), "daily")
        raw = [value(lookup[day]) if day in lookup else None for day in days]
        series = [math.nan if item is None else item / scale for item in raw]
        points += [item for item in series if not math.isnan(item)]
        axes.plot(range(len(days)), series, color=color, linewidth=2, marker="o", markersize=4.5)
    _style_trend(axes, days)
    return (min(points, default=1.0), max(points, default=0.0))


def _style_trend(axes: Any, days: list[str]) -> None:
    """Recessive axes with a few date labels and hairline horizontal gridlines."""
    step = max(1, math.ceil(len(days) / MAX_DAY_LABELS))
    ticks = list(range(0, len(days), step))
    axes.set_xticks(ticks, [days[tick][5:] for tick in ticks])
    axes.set_xlim(-0.5, max(len(days) - 0.5, 0.5))
    axes.locator_params(axis="y", nbins=4)
    axes.grid(axis="y", color=HAIRLINE, linewidth=1)
    axes.set_axisbelow(True)
    strip_chrome(axes)
    axes.tick_params(labelsize=8.5, labelcolor=INK_MUTED)


def _draw_footer(figure: Any, stats: dict[str, Any]) -> None:
    canceled = sum(group.get("canceled", 0) for group in stats.get("groups", {}).values())
    note = (
        "Pass rate = passed / (passed + failed); "
        f"{canceled:,} canceled build(s) excluded. "
        "Queue time = queued to started, including waits on dependencies. Days are UTC."
    )
    footer(figure, note, format_stamp(stats.get("generated_at", "")))


def run(args: argparse.Namespace) -> int:
    try:
        stats = read_json(args.input)
    except (OSError, ValueError) as error:
        print(f"handy teamcity-dashboard: cannot read stats: {error}")
        return 1
    if not isinstance(stats, dict) or "groups" not in stats:
        print("handy teamcity-dashboard: expected a document from `handy teamcity-stats`")
        return 1
    output = render(stats, args.output, args.title, args.top, args.dpi)
    print(f"wrote {output}")
    return 0
