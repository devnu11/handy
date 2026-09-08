"""Chart plumbing shared by the report generators.

Underscore-prefixed so the dispatcher skips it. Every generator draws on the
same light surface with the same palette and mark specs, so a Confluence page
carrying several of these reads as one set rather than three unrelated charts.

Palette values are the validated defaults from the data-viz reference palette
(light surface). Status colors are reserved for state — good/warning/critical —
and are never used to tell one series from another.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#7a7873"
HAIRLINE = "#e3e2de"

# Categorical slots 1 and 2, for when a chart needs series identity.
SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"

# Reserved for state, never for identity.
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"

# Blue ordinal ramp, steps 250-700, for ordered categories (age bands, tiers).
# It starts at 250 because anything lighter fails contrast on a light surface.
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

# Ceiling on bar thickness. Without it a bar is a fixed fraction of its row, so
# a chart with few rows draws fat, heavy bars. Longer lists fall back to the
# fraction and thin out as usual.
BAR_INCHES = 0.36

# No nonzero value may render as nothing: a single failure among two million
# tests still has to be visible, or "clean" and "broken" look identical.
MIN_MARK_INCHES = 0.03


def figure_and_axes(figsize: tuple[float, float] = (12, 8.2), dpi: int = 130) -> Any:
    """Open a figure on the shared surface. Returns (matplotlib.pyplot, figure)."""
    # Imported here, not at module scope: the CLI imports every tool module to
    # build its parser, and matplotlib is far too slow to pay for on every run.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt, plt.figure(figsize=figsize, dpi=dpi, facecolor=SURFACE)


def ordinal_colors(count: int) -> list[str]:
    """Pick `count` evenly spaced steps from the blue ordinal ramp."""
    if count <= 0:
        return []
    if count == 1:
        return [BLUE_ORDINAL[len(BLUE_ORDINAL) // 2]]
    last = len(BLUE_ORDINAL) - 1
    return [BLUE_ORDINAL[round(index * last / (count - 1))] for index in range(count)]


def bar_thickness(axes: Any, count: int, along: str = "height") -> float:
    """Thickness of one bar as a fraction of its band, capped at BAR_INCHES."""
    figure = axes.get_figure()
    position = axes.get_position()
    if along == "height":
        span = position.height * figure.get_figheight()
    else:
        span = position.width * figure.get_figwidth()
    band = span / max(count, 1)
    return min(0.5, BAR_INCHES / band) if band else 0.5


def visible_lengths(axes: Any, values: list[float], limit: float) -> list[float]:
    """Floor every nonzero value at a drawable length, in data units.

    A count of 1 against a maximum of 2,000,000 is a hundredth of a pixel wide.
    Rather than let it vanish, give it the smallest mark the eye can catch; the
    exact number always rides beside the bar as text.
    """
    position = axes.get_position()
    inches = position.width * axes.get_figure().get_figwidth()
    floor = (MIN_MARK_INCHES / inches) * limit if inches else 0.0
    return [max(value, floor) if value > 0 else 0.0 for value in values]


def header(figure: Any, title: str, subtitle: str, tiles: list[tuple[str, str]]) -> None:
    """Title, subtitle, and a row of stat tiles above a hairline divider."""
    from matplotlib.lines import Line2D

    figure.text(0.035, 0.945, title, fontsize=21, fontweight="bold", color=INK, va="top")
    figure.text(0.035, 0.895, subtitle, fontsize=10, color=INK_SECONDARY, va="top")
    span = 0.93 / max(len(tiles), 1)
    for index, (label, value) in enumerate(tiles):
        x = 0.035 + index * span
        figure.text(x, 0.815, label, fontsize=10, color=INK_MUTED, va="top")
        figure.text(
            x,
            0.785,
            value,
            fontsize=30 if index == 0 else 19,
            fontweight="bold" if index == 0 else "normal",
            color=INK,
            va="top",
        )
    figure.add_artist(Line2D([0.035, 0.965], [0.705, 0.705], color=HAIRLINE, linewidth=1))


def footer(figure: Any, note: str, stamp: str = "") -> None:
    figure.text(0.035, 0.022, note, fontsize=8.5, color=INK_MUTED)
    if stamp:
        figure.text(0.965, 0.022, stamp, fontsize=8.5, color=INK_MUTED, ha="right")


def panel_title(axes: Any, text: str) -> None:
    axes.set_title(text, fontsize=11.5, fontweight="bold", color=INK, loc="left", pad=12)


def strip_chrome(axes: Any, baseline: str = "bottom") -> None:
    """Recessive axes: one hairline baseline, no ticks, no gridlines."""
    axes.set_facecolor(SURFACE)
    for side, spine in axes.spines.items():
        spine.set_visible(side == baseline)
        spine.set_color(HAIRLINE)
        spine.set_linewidth(1)
    axes.tick_params(length=0, colors=INK_SECONDARY)


def format_stamp(value: str, prefix: str = "generated") -> str:
    try:
        return f"{prefix} {datetime.fromisoformat(value):%Y-%m-%d %H:%M %Z}".strip()
    except ValueError:
        return ""


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def compact(number: float) -> str:
    """Short form for a big count: 2,100,000 -> 2.1M."""
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(number) >= limit:
            scaled = number / limit
            return f"{scaled:.1f}{suffix}".replace(".0", "")
    return f"{number:,.0f}"


def text_table(
    axes: Any,
    headers: list[str],
    rows: list[list[str]],
    columns: list[tuple[float, str]],
    statuses: list[str] | None = None,
) -> None:
    """Draw a compact table in a blank panel.

    `columns` gives each column's x position (0-1) and alignment. A status color
    per row is drawn as a dot in the left margin, so state reads without relying
    on the reader parsing the numbers.
    """
    axes.set_xlim(0, 1)
    axes.set_ylim(0, 1)
    axes.set_facecolor(SURFACE)
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(False)

    step = 1 / (len(rows) + 1.8)
    top = 1.0
    for (x, align), title in zip(columns, headers, strict=True):
        axes.text(x, top, title, ha=align, va="center", fontsize=8.5, color=INK_MUTED)
    axes.axhline(top - step * 0.5, color=HAIRLINE, linewidth=1)

    for index, row in enumerate(rows):
        y = top - step * (index + 1.15)
        if statuses:
            axes.plot(-0.055, y, marker="o", markersize=5, color=statuses[index], clip_on=False)
        for column, ((x, align), cell) in enumerate(zip(columns, row, strict=True)):
            axes.text(
                x,
                y,
                cell,
                ha=align,
                va="center",
                fontsize=9,
                color=INK if column == 0 else INK_SECONDARY,
                family="sans-serif" if column == 0 else "monospace",
            )
