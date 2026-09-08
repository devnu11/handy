"""Report conformance pass rates against a minimum, as JSON and a PNG.

Pass rates are a bad primary scale here: suites range from twenty tests to
millions, so 99.9% means "one failure allowed" in one suite and "two thousand"
in another, and a 0.1% failure is 0.8px on a 100%-wide bar. The report is built
on the error budget instead — allowed failures = (1 - minimum) x tests — which
is comparable across every suite size, with exact counts in the table beside it.

Input JSON (a bare list of suites is also accepted):

    {
      "source": "nightly conformance",
      "minimum_pass_rate": 0.999,
      "suites": [
        {"name": "core", "total": 2100000, "passed": 2099878, "skipped": 0,
         "minimum_pass_rate": 0.9999}
      ]
    }

`failed` is derived from the others when absent, and vice versa.
"""

from __future__ import annotations

import argparse
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from handy.tools._viz import (
    HAIRLINE,
    INK,
    INK_MUTED,
    INK_SECONDARY,
    STATUS_CRITICAL,
    STATUS_GOOD,
    STATUS_WARNING,
    bar_thickness,
    compact,
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

HELP = "report conformance pass rates against a minimum, as a PNG"

DEFAULT_MINIMUM = 0.999

# Bars are clipped here so one catastrophic suite can't squash the rest; the
# true figure always rides beside the bar as text.
CONSUMED_CAP = 1.5

STATUS_COLORS = {"good": STATUS_GOOD, "warning": STATUS_WARNING, "critical": STATUS_CRITICAL}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="conformance results JSON (default: stdin)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("conformance-report.png"),
        help="PNG to write (default: %(default)s)",
    )
    parser.add_argument("--summary", type=Path, help="also write the computed summary as JSON")
    parser.add_argument(
        "--minimum",
        type=float,
        default=DEFAULT_MINIMUM,
        help="minimum pass rate for suites that don't declare one (default: %(default)s)",
    )
    parser.add_argument("--title", default="Conformance", help="headline on the graphic")
    parser.add_argument("--top", type=int, default=12, help="suites to chart (default: 12)")
    parser.add_argument("--dpi", type=int, default=130, help="output resolution (default: 130)")


def decimals_for(executed: int) -> int:
    """Decimal places that keep a single failure visible in the percentage.

    Twenty tests need one place; two million need four, or every suite reads
    "100.0%" whether it is clean or has a thousand failures.
    """
    if executed <= 0:
        return 1
    # One failure in n tests moves the rate by 1/n, so the percentage needs
    # about log10(n) - 2 places to show it. Capping this too low is how a
    # report ends up printing "100.0000%" over a suite with failures.
    return max(1, min(8, math.ceil(math.log10(executed)) - 2))


def format_rate(rate: float, executed: int) -> str:
    return f"{rate * 100:.{decimals_for(executed)}f}%"


def trim_rate(text: str) -> str:
    """Drop trailing zeros from a floor: 99.90000% reads as noise, 99.9% doesn't."""
    number, _, _ = text.partition("%")
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    return f"{number}%"


def summarize_suite(suite: dict[str, Any], default_minimum: float) -> dict[str, Any]:
    """Work out one suite's pass rate, error budget, and status."""
    total = int(suite.get("total", 0))
    skipped = int(suite.get("skipped", 0) or 0)
    passed = suite.get("passed")
    failed = suite.get("failed")
    if failed is None:
        failed = max(total - int(passed or 0) - skipped, 0)
    if passed is None:
        passed = max(total - int(failed) - skipped, 0)
    passed, failed = int(passed), int(failed)
    executed = passed + failed

    minimum = float(suite.get("minimum_pass_rate", default_minimum))
    pass_rate = passed / executed if executed else 1.0
    # Allowed failures before the floor is breached. Below one, the suite has
    # no tolerance at all: a single failure breaches it.
    budget = executed * (1 - minimum)
    consumed = failed / budget if budget > 0 else None
    breached = pass_rate < minimum

    if breached:
        status = "critical"
    elif consumed is not None and consumed >= 0.75:
        status = "warning"
    else:
        status = "good"

    return {
        "name": suite.get("name", "unnamed"),
        "total": total,
        "executed": executed,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": pass_rate,
        "pass_rate_text": format_rate(pass_rate, executed),
        "minimum_pass_rate": minimum,
        "minimum_text": trim_rate(format_rate(minimum, executed)),
        "budget": round(budget, 3),
        "allowed_failures": int(budget),
        "budget_consumed": round(consumed, 4) if consumed is not None else None,
        "zero_tolerance": budget < 1,
        "breached": breached,
        "status": status,
    }


def summarize(
    document: dict[str, Any] | list[dict[str, Any]],
    default_minimum: float = DEFAULT_MINIMUM,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Turn raw suite results into the summary the graphic is drawn from."""
    raw = document if isinstance(document, list) else document.get("suites", [])
    meta = document if isinstance(document, dict) else {}
    suites = [
        summarize_suite(suite, float(meta.get("minimum_pass_rate", default_minimum)))
        for suite in raw
    ]
    # Worst first: breaches, then whoever has eaten most of their budget.
    suites.sort(key=lambda s: (not s["breached"], -(s["budget_consumed"] or 0), -s["failed"]))

    executed = sum(suite["executed"] for suite in suites)
    failed = sum(suite["failed"] for suite in suites)
    budget = sum(suite["budget"] for suite in suites)
    pass_rate = (executed - failed) / executed if executed else 1.0
    return {
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "source": meta.get("source", ""),
        "suites": suites,
        "overall": {
            "suites": len(suites),
            "breached": sum(1 for suite in suites if suite["breached"]),
            "executed": executed,
            "failed": failed,
            "skipped": sum(suite["skipped"] for suite in suites),
            "pass_rate": pass_rate,
            "pass_rate_text": format_rate(pass_rate, executed),
            "budget": round(budget, 3),
            "budget_consumed": round(failed / budget, 4) if budget > 0 else None,
        },
    }


def render(
    summary: dict[str, Any],
    output: Path,
    title: str = "Conformance",
    top: int = 12,
    dpi: int = 130,
) -> Path:
    """Draw the report and write it to `output`, returning the path."""
    plt, figure = figure_and_axes(dpi=dpi)
    overall = summary.get("overall", {})
    breached = overall.get("breached", 0)
    header(
        figure,
        title,
        truncate(summary.get("source", "") or "conformance results", 120),
        [
            ("Tests run", compact(overall.get("executed", 0))),
            ("Pass rate", overall.get("pass_rate_text", "—")),
            ("Failing", f"{overall.get('failed', 0):,}"),
            ("Below floor", f"{breached} of {overall.get('suites', 0)}"),
        ],
    )

    suites = summary.get("suites", [])[:top]
    if not suites:
        figure.text(
            0.5, 0.42, "No suites in the input.", ha="center", fontsize=16, color=INK_SECONDARY
        )
    else:
        grid = figure.add_gridspec(
            1, 2, left=0.175, right=0.975, top=0.66, bottom=0.10, wspace=0.42
        )
        _draw_budgets(figure.add_subplot(grid[0, 0]), suites)
        _draw_table(figure.add_subplot(grid[0, 1]), suites)

    footer(
        figure,
        "Budget = allowed failures at the minimum pass rate. 100% means the floor is exactly met.",
        format_stamp(summary.get("generated_at", "")),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=dpi)
    plt.close(figure)
    return output


def _draw_budgets(axes: Any, suites: list[dict[str, Any]]) -> None:
    """Error budget consumed, the one scale every suite size shares."""
    labels = [truncate(suite["name"], 18) for suite in suites]
    positions = range(len(suites))
    # A suite with no tolerance and a failure has consumed an unbounded share;
    # it pins to the cap and the table carries the real numbers.
    values = [
        min(suite["budget_consumed"], CONSUMED_CAP)
        if suite["budget_consumed"] is not None
        else (CONSUMED_CAP if suite["failed"] else 0.0)
        for suite in suites
    ]
    axes.set_xlim(0, CONSUMED_CAP)
    drawn = visible_lengths(axes, values, CONSUMED_CAP)
    colors = [STATUS_COLORS[suite["status"]] for suite in suites]
    axes.barh(positions, drawn, height=bar_thickness(axes, len(suites)), color=colors)

    for position, suite, value in zip(positions, suites, values, strict=True):
        consumed = suite["budget_consumed"]
        text = "over" if consumed is None and suite["failed"] else f"{(consumed or 0):.0%}"
        axes.text(
            min(value, CONSUMED_CAP) + 0.03,
            position,
            text,
            va="center",
            fontsize=9,
            color=INK_SECONDARY,
        )

    axes.axvline(1.0, color=HAIRLINE, linewidth=1)
    axes.text(
        1.0, len(suites) - 0.35, "floor", ha="center", va="top", fontsize=8.5, color=INK_MUTED
    )
    axes.set_yticks(list(positions), labels, fontsize=9.5, color=INK)
    axes.set_ylim(len(suites) - 0.5, -0.5)
    axes.set_xticks([])
    strip_chrome(axes, baseline="left")
    panel_title(axes, "Error budget consumed")


def _draw_table(axes: Any, suites: list[dict[str, Any]]) -> None:
    """Exact counts, because a mark can only ever be an approximation."""
    rows = [
        [
            truncate(suite["name"], 16),
            compact(suite["executed"]),
            f"{suite['failed']:,}",
            suite["pass_rate_text"],
            suite["minimum_text"],
        ]
        for suite in suites
    ]
    text_table(
        axes,
        headers=["suite", "tests", "failed", "pass rate", "floor"],
        rows=rows,
        columns=[(0.0, "left"), (0.40, "right"), (0.56, "right"), (0.78, "right"), (1.0, "right")],
        statuses=[STATUS_COLORS[suite["status"]] for suite in suites],
    )
    panel_title(axes, "Every suite, exactly")


def run(args: argparse.Namespace) -> int:
    try:
        document = read_json(args.input)
    except (OSError, ValueError) as error:
        print(f"handy conformance-report: cannot read results: {error}")
        return 1
    if not isinstance(document, list | dict):
        print("handy conformance-report: expected a suite list or a results document")
        return 1

    summary = summarize(document, args.minimum)
    if args.summary is not None:
        write_json(summary, args.summary)
    output = render(summary, args.output, args.title, args.top, args.dpi)

    overall = summary["overall"]
    print(
        f"{overall['pass_rate_text']} pass rate, {overall['failed']:,} failing, "
        f"{overall['breached']} of {overall['suites']} suites below their floor"
    )
    print(f"wrote {output}")
    return 1 if overall["breached"] else 0
