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


def test_pushing_keeps_what_the_navigation_replaces() -> None:
    assert pushed(EMPTY, "base") == TipNavTurn(TipNavState(("base",)))


def test_popping_hands_back_the_step_it_removes() -> None:
    """The view comes back as a decision, not as a return value: whoever restores it is not the
    code that decided there was one to restore."""
    assert popped(TipNavState(("base", "first"))) == TipNavTurn(
        TipNavState(("base",)), (TipViewRestored("first"),)
    )


def test_popping_an_empty_stack_decides_nothing() -> None:
    """The refusal is the whole reason this is a machine — Esc falls through to closing the
    tooltip, and inline the check and the mutation were one statement."""
    assert popped(EMPTY) == TipNavTurn(EMPTY)


def test_the_stack_unwinds_in_the_order_it_was_built() -> None:
    state = pushed(pushed(EMPTY, "base").state, "first").state

    first = popped(state)
    assert first.decisions == (TipViewRestored("first"),)
    assert popped(first.state).decisions == (TipViewRestored("base"),)


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
