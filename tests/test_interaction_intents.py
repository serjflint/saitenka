"""WP5.3: what the pointer and tooltip-navigation commands actually decide."""

from __future__ import annotations

import pytest

from saitenka.app.intents import DismissHover
from saitenka.app.session.interaction_intents import (
    CopyUnderCursor,
    InteractionCommand,
    InteractionInputs,
    NavigateBack,
    RouteClick,
    RouteWheel,
    ScrollTooltip,
    reduce,
)


@pytest.mark.parametrize(
    ("command", "steps"),
    [(InteractionCommand.WHEEL_UP, -1), (InteractionCommand.WHEEL_DOWN, 1)],
)
def test_a_wheel_step_is_routed_rather_than_aimed(command: InteractionCommand, steps: int) -> None:
    """Which surface claims the step is `surfaces.SURFACES`' topmost-first table, not a decision to
    re-make here — so the effect carries the step and nothing else."""
    assert reduce(command, InteractionInputs()) == (RouteWheel(steps),)


@pytest.mark.parametrize(
    ("command", "sign"),
    [(InteractionCommand.TOOLTIP_UP, -1), (InteractionCommand.TOOLTIP_DOWN, 1)],
)
def test_a_keyboard_step_scrolls_a_fraction_of_the_viewport(
    command: InteractionCommand, sign: int
) -> None:
    """A fraction rather than a pixel constant, so a taller panel pages proportionally instead of
    crawling."""
    assert reduce(command, InteractionInputs(tooltip_view_height=500)) == (
        ScrollTooltip(60 * sign),
    )


def test_a_zero_height_tooltip_scrolls_nowhere() -> None:
    """No viewport measured yet — a fraction of nothing is nothing, not a default step size."""
    assert reduce(InteractionCommand.TOOLTIP_DOWN, InteractionInputs()) == (ScrollTooltip(0),)


def test_escape_walks_back_through_link_history_first() -> None:
    """Browser-back-then-close, the feel Yomitan's history gives."""
    assert reduce(
        InteractionCommand.TOOLTIP_BACK_OR_CLOSE, InteractionInputs(can_go_back=True)
    ) == (NavigateBack(),)


def test_escape_at_the_root_closes_the_tooltip() -> None:
    assert reduce(InteractionCommand.TOOLTIP_BACK_OR_CLOSE, InteractionInputs()) == (
        DismissHover(),
    )


def test_a_click_is_routed_and_a_right_click_copies() -> None:
    assert reduce(InteractionCommand.CLICK, InteractionInputs()) == (RouteClick(),)
    assert reduce(InteractionCommand.COPY_UNDER_CURSOR, InteractionInputs()) == (CopyUnderCursor(),)


def test_the_reducer_reads_its_inputs_without_mutating_them() -> None:
    given = InteractionInputs(can_go_back=True, tooltip_view_height=500)

    for command in InteractionCommand:
        reduce(command, given)

    assert given == InteractionInputs(can_go_back=True, tooltip_view_height=500)
