"""The shared chart plumbing: palette steps and mark specs."""

import pytest

from handy.tools import _viz as viz


def test_ordinal_colors_span_the_ramp():
    assert viz.ordinal_colors(0) == []
    assert viz.ordinal_colors(1) == [viz.BLUE_ORDINAL[len(viz.BLUE_ORDINAL) // 2]]
    four = viz.ordinal_colors(4)
    assert four[0] == viz.BLUE_ORDINAL[0]
    assert four[-1] == viz.BLUE_ORDINAL[-1]
    assert len(set(four)) == 4  # every band gets its own step


def test_ordinal_colors_never_runs_off_the_ramp():
    colors = viz.ordinal_colors(len(viz.BLUE_ORDINAL) + 5)
    assert set(colors) <= set(viz.BLUE_ORDINAL)


def test_bars_keep_a_fixed_thickness_as_the_row_count_changes():
    """The point of the cap: a short component list must not draw fat bars."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(12, 8.2))
    axes = figure.add_subplot(111)
    span = axes.get_position().height * figure.get_figheight()
    inches = {count: viz.bar_thickness(axes, count) * span / count for count in (3, 6, 12, 40)}
    plt.close(figure)

    # Short lists are held at the cap instead of fattening to fill their rows.
    assert inches[3] == pytest.approx(viz.BAR_INCHES)
    assert inches[6] == pytest.approx(viz.BAR_INCHES)
    # Once rows are narrower than the cap, bars thin out rather than touching.
    assert inches[12] < viz.BAR_INCHES
    assert inches[40] < inches[12]


def test_truncate_keeps_long_component_names_short():
    assert viz.truncate("short", 10) == "short"
    assert len(viz.truncate("a-very-long-component-name", 10)) == 10
