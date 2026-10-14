"""The tooltip's link-navigation back-stack, as a pure function of the stack it is given."""

from __future__ import annotations

from saitenka.runtime.tipnav import (
    TipNavState,
    TipNavTurn,
    TipViewRestored,
    cleared,
    popped,
    pushed,
)

EMPTY = TipNavState()


def test_popping_an_empty_stack_decides_nothing() -> None:
    """The refusal is the whole reason this is a machine: Esc falls through to closing the
    tooltip, so the check has to be answerable without being the code that unwinds."""
    assert popped(EMPTY) == TipNavTurn(EMPTY)


def test_the_stack_unwinds_in_the_order_it_was_built() -> None:
    base = pushed(EMPTY, "base")
    first_push = pushed(base.state, "first")
    assert base.decisions == ()
    assert first_push.decisions == ()

    first = popped(first_push.state)
    assert first.decisions == (TipViewRestored("first"),)
    second = popped(first.state)
    assert second.decisions == (TipViewRestored("base"),)
    assert second.state == EMPTY


def test_clearing_drops_every_step_without_restoring_one() -> None:
    """Going "back" while clearing would reopen a view describing a word that is no longer
    hovered, which is the one thing a teardown must not do."""
    assert cleared(TipNavState(("base", "first"))) == TipNavTurn(EMPTY)
    assert cleared(EMPTY) == TipNavTurn(EMPTY)


def test_the_machine_never_looks_inside_a_captured_view() -> None:
    """Opaque by contract: `runtime` cannot name a panel or a pixmap, so anything at all round-trips
    — which is also what stops the stack becoming a second copy of the view it snapshots."""

    class Unreadable:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"the slice read {name} off a captured view")

    view = Unreadable()
    assert popped(pushed(EMPTY, view).state).decisions == (TipViewRestored(view),)
