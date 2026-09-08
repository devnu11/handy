"""Report performance measurements against their expected values, as a PNG.

Raw values don't compare: a 4ms cache read and an 800ms report build can't
share an axis, and neither says whether it is acceptable. So the chart plots
deviation from expected, signed so that worse is always to the right whether
the metric wants to go up (throughput) or down (latency).

Input JSON (a bare list of metrics is also accepted):

    {
      "source": "perf run 4821",
      "tolerance": 0.05,
      "metrics": [
        {"name": "p99 latency", "value": 262, "unit": "ms", "target": 250,
         "tolerance": 0.1, "lower_is_better": true},
        {"name": "throughput", "value": 18400, "unit": "req/s", "baseline": 19000,
         "lower_is_better": false}
      ]
    }

A metric compares against `target` when it has one and `baseline` otherwise, so
fixed goals and last-run comparisons can sit in the same report.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from handy.tools._viz import (
    INK,
    INK_MUTED,
    INK_SECONDARY,
    STATUS_CRITICAL,
    STATUS_GOOD,
    STATUS_WARNING,
    SURFACE,
    bar_thickness,
    figure_and_axes,
    footer,
    format_stamp,
    header,
    panel_title,
    strip_chrome,
    text_table,
    truncate,
    visible_lengths,
)
from handy.util import read_json, write_json

HELP = "compare performance measurements against expected values, as a PNG"

DEFAULT_TOLERANCE = 0.05

STATUS_COLORS = {"good": STATUS_GOOD, "warning": STATUS_WARNING, "critical": STATUS_CRITICAL}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input", nargs="?", type=Path, help="performance results JSON (default: stdin)"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("perf-report.png"),
        help="PNG to write (default: %(default)s)",
    )
    parser.add_argument("--summary", type=Path, help="also write the computed summary as JSON")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help="allowed deviation for metrics that don't declare one (default: %(default)s)",
    )
    parser.add_argument("--title", default="Performance", help="headline on the graphic")
    parser.add_argument("--top", type=int, default=12, help="metrics to chart (default: 12)")
    parser.add_argument("--dpi", type=int, default=130, help="output resolution (default: 130)")


def summarize_metric(metric: dict[str, Any], default_tolerance: float) -> dict[str, Any]:
    """Measure one metric against whichever expectation it declares."""
    value = float(metric.get("value", 0.0))
    target = metric.get("target")
    baseline = metric.get("baseline")
    expected = float(target if target is not None else baseline or 0.0)
    against = "target" if target is not None else "baseline"
    lower_is_better = bool(metric.get("lower_is_better", True))
    tolerance = float(metric.get("tolerance", default_tolerance))

    deviation = (value - expected) / expected if expected else 0.0
    # Signed so positive always means worse, whichever way the metric wants to go.
    badness = deviation if lower_is_better else -deviation

    if badness > tolerance:
        status = "critical"
    elif badness > tolerance / 2:
        status = "warning"
    else:
        status = "good"

    return {
        "name": metric.get("name", "unnamed"),
        "unit": metric.get("unit", ""),
        "value": value,
        "expected": expected,
        "against": against,
        "lower_is_better": lower_is_better,
        "tolerance": tolerance,
        "deviation": round(deviation, 6),
        "badness": round(badness, 6),
        "within_tolerance": badness <= tolerance,
        "status": status,
    }


def summarize(
    document: dict[str, Any] | list[dict[str, Any]],
    default_tolerance: float = DEFAULT_TOLERANCE,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Turn raw measurements into the summary the graphic is drawn from."""
    raw = document if isinstance(document, list) else document.get("metrics", [])
    meta = document if isinstance(document, dict) else {}
    tolerance = float(meta.get("tolerance", default_tolerance))
    metrics = [summarize_metric(metric, tolerance) for metric in raw]
    metrics.sort(key=lambda metric: -metric["badness"])

    regressions = [metric for metric in metrics if not metric["within_tolerance"]]
    return {
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "source": meta.get("source", ""),
        "metrics": metrics,
        "overall": {
            "metrics": len(metrics),
            "within_tolerance": sum(1 for metric in metrics if metric["within_tolerance"]),
            "regressions": len(regressions),
            "worst": regressions[0]["name"] if regressions else "",
            "worst_badness": regressions[0]["badness"] if regressions else 0.0,
        },
    }


def render(
    summary: dict[str, Any],
    output: Path,
    title: str = "Performance",
    top: int = 12,
    dpi: int = 130,
) -> Path:
    """Draw the report and write it to `output`, returning the path."""
    plt, figure = figure_and_axes(dpi=dpi)
    overall = summary.get("overall", {})
    worst = f"{overall.get('worst_badness', 0):+.1%}" if overall.get("worst") else "none"
    header(
        figure,
        title,
        truncate(summary.get("source", "") or "performance results", 120),
        [
            ("Metrics", f"{overall.get('metrics', 0):,}"),
            (
                "Within tolerance",
                f"{overall.get('within_tolerance', 0)} of {overall.get('metrics', 0)}",
            ),
            ("Regressions", f"{overall.get('regressions', 0)}"),
            ("Worst", worst),
        ],
    )

    metrics = summary.get("metrics", [])[:top]
    if not metrics:
        figure.text(
            0.5, 0.42, "No metrics in the input.", ha="center", fontsize=16, color=INK_SECONDARY
        )
    else:
        grid = figure.add_gridspec(
            1, 2, left=0.175, right=0.975, top=0.66, bottom=0.10, wspace=0.42
        )
        _draw_deviations(figure.add_subplot(grid[0, 0]), metrics)
        _draw_table(figure.add_subplot(grid[0, 1]), metrics)

    footer(
        figure,
        "Deviation from each metric's own expectation, signed so worse is always to the right.",
        format_stamp(summary.get("generated_at", "")),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=dpi)
    plt.close(figure)
    return output


def _draw_deviations(axes: Any, metrics: list[dict[str, Any]]) -> None:
    """Diverging bars around the expected value, worst to the right."""
    labels = [truncate(metric["name"], 18) for metric in metrics]
    values = [metric["badness"] for metric in metrics]
    widest_tolerance = max((metric["tolerance"] for metric in metrics), default=0.05)
    positions = range(len(metrics))
    reach = max(max((abs(value) for value in values), default=0.0), widest_tolerance) * 1.35

    axes.set_xlim(-reach, reach)
    axes.axvline(0, color=INK_MUTED, linewidth=1)

    # A metric barely off its expectation still has to be visible; the label
    # beside the bar always carries the real figure.
    drawn = _signed_lengths(axes, values, reach)
    colors = [STATUS_COLORS[metric["status"]] for metric in metrics]
    axes.barh(positions, drawn, height=bar_thickness(axes, len(metrics)), color=colors)

    for position, metric, value in zip(positions, metrics, values, strict=True):
        # Each bar is read against its own metric's tolerance. One shared band
        # would put a 7% bar inside "acceptable" for a metric that allows 5%.
        for edge in (-metric["tolerance"], metric["tolerance"]):
            axes.plot(
                [edge, edge],
                [position - 0.34, position + 0.34],
                color=INK_MUTED,
                linewidth=1,
                alpha=0.45,
                solid_capstyle="butt",
            )
        offset = reach * 0.03
        axes.text(
            value + (offset if value >= 0 else -offset),
            position,
            f"{value:+.1%}",
            va="center",
            ha="left" if value >= 0 else "right",
            fontsize=9,
            color=INK_SECONDARY,
            # Sits on the surface so a tolerance tick can't strike through it.
            bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1},
        )

    axes.set_yticks(list(positions), labels, fontsize=9.5, color=INK)
    axes.set_ylim(len(metrics) - 0.5, -0.5)
    axes.set_xticks([])
    axes.set_xlabel(
        "better ←   deviation from expected   → worse   (ticks: each metric's tolerance)",
        fontsize=9,
        color=INK_MUTED,
        labelpad=8,
    )
    strip_chrome(axes, baseline="none")
    panel_title(axes, "Deviation from expected")


def _draw_table(axes: Any, metrics: list[dict[str, Any]]) -> None:
    rows = [
        [
            truncate(metric["name"], 16),
            _measure(metric["value"], metric["unit"]),
            _measure(metric["expected"], metric["unit"]),
            metric["against"],
            f"±{metric['tolerance']:.0%}",
        ]
        for metric in metrics
    ]
    text_table(
        axes,
        headers=["metric", "measured", "expected", "vs", "tol"],
        rows=rows,
        columns=[(0.0, "left"), (0.44, "right"), (0.72, "right"), (0.89, "right"), (1.0, "right")],
        statuses=[STATUS_COLORS[metric["status"]] for metric in metrics],
    )
    panel_title(axes, "Measured against expected")


def _signed_lengths(axes: Any, values: list[float], reach: float) -> list[float]:
    """Floor every nonzero deviation at a drawable length, keeping its sign."""
    magnitudes = visible_lengths(axes, [abs(value) for value in values], reach)
    return [
        magnitude if value >= 0 else -magnitude
        for value, magnitude in zip(values, magnitudes, strict=True)
    ]


def _measure(value: float, unit: str) -> str:
    """Enough precision that measured and expected never look identical."""
    if abs(value) >= 1000:
        text = f"{value:,.0f}"
    elif abs(value) >= 10:
        text = f"{value:.1f}"
    elif abs(value) >= 1:
        text = f"{value:.2f}"
    else:
        text = f"{value:.3f}"
    return f"{text}{unit}" if unit else text


def run(args: argparse.Namespace) -> int:
    try:
        document = read_json(args.input)
    except (OSError, ValueError) as error:
        print(f"handy perf-report: cannot read results: {error}")
        return 1
    if not isinstance(document, list | dict):
        print("handy perf-report: expected a metric list or a results document")
        return 1

    summary = summarize(document, args.tolerance)
    if args.summary is not None:
        write_json(summary, args.summary)
    output = render(summary, args.output, args.title, args.top, args.dpi)

    overall = summary["overall"]
    print(
        f"{overall['within_tolerance']} of {overall['metrics']} metrics within tolerance, "
        f"{overall['regressions']} regression(s)"
    )
    print(f"wrote {output}")
    return 1 if overall["regressions"] else 0
