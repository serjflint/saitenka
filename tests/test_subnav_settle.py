"""WP4.5: the navigation settle window is a revision-fenced deadline, not a polled timestamp."""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st

from saitenka.app.subnav_settle import (
    NavigationSettleDue,
    SettleWindow,
)

# --- revision fencing ---------------------------------------------------------------------------


@given(st.integers(min_value=2, max_value=20))
@example(4)
def test_each_navigation_supersedes_the_previous_window(navigations) -> None:
    window = SettleWindow()
    identities = set()

    for _ in range(navigations):
        window = window.begin()
        assert window.open
        assert window.identity not in identities
        identities.add(window.identity)


def test_a_due_for_an_unknown_revision_is_inert() -> None:
    window = SettleWindow().begin()

    assert window.due(NavigationSettleDue(999)) == window


def test_retire_is_idempotent_and_keeps_the_revision() -> None:
    window = SettleWindow().begin()

    once = window.retire()
    twice = once.retire()

    assert (once.open, twice.open) == (False, False)
    assert twice.revision == window.revision


def test_a_retired_window_ignores_its_own_late_due() -> None:
    """Reconcile closes the window before the deadline arrives; the due must change nothing."""
    window = SettleWindow().begin()
    identity = window.identity

    retired = window.retire()

    assert retired.due(identity) == retired
