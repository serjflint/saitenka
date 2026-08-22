"""WP4.5: the navigation settle window is a revision-fenced deadline, not a polled timestamp."""

from __future__ import annotations

from saitenka.app.subnav_settle import (
    NavigationSettleDue,
    SettleWindow,
    swallows,
)


def test_a_closed_window_swallows_nothing() -> None:
    closed = SettleWindow()

    assert not swallows(closed, text="", nav_prev_text="に", identity_reinstall=False)
    assert not swallows(closed, text="に", nav_prev_text="に", identity_reinstall=False)


def test_an_open_window_swallows_the_mid_seek_blank() -> None:
    window = SettleWindow().begin()

    assert swallows(window, text="", nav_prev_text="いち", identity_reinstall=False)
    assert swallows(window, text="   ", nav_prev_text="いち", identity_reinstall=False)


def test_an_open_window_swallows_mpv_re_reporting_the_pre_nav_cue() -> None:
    window = SettleWindow().begin()

    assert swallows(window, text="いち", nav_prev_text="いち", identity_reinstall=False)


def test_an_identity_reinstall_of_the_pre_nav_text_is_not_swallowed() -> None:
    """The same text arriving for a retired identity is a real reinstall, not a seek transient."""
    window = SettleWindow().begin()

    assert not swallows(window, text="いち", nav_prev_text="いち", identity_reinstall=True)


def test_the_target_cue_is_never_swallowed() -> None:
    window = SettleWindow().begin()

    assert not swallows(window, text="に", nav_prev_text="いち", identity_reinstall=False)


# --- revision fencing ---------------------------------------------------------------------------


def test_each_navigation_supersedes_the_previous_window() -> None:
    first = SettleWindow().begin()
    second = first.begin()

    assert second.revision > first.revision
    assert second.open


def test_a_due_for_a_superseded_navigation_leaves_the_current_window_open() -> None:
    """The classic late-due race: the first nav's deadline fires after a second nav opened its
    own window. Closing it there would un-guard the seek still in flight."""
    first = SettleWindow().begin()
    second = first.begin()

    assert second.due(first.identity) == second
    assert second.due(first.identity).open


def test_the_matching_due_closes_the_window() -> None:
    window = SettleWindow().begin()

    assert not window.due(window.identity).open


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


def test_the_window_is_immutable() -> None:
    window = SettleWindow()

    window.begin()

    assert not window.open
