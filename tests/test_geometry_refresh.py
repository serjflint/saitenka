"""WP4.4: several geometry-input changes in one batch coalesce into one refresh, and a refresh
armed before a source change cannot run against its replacement."""

from __future__ import annotations

from saitenka.app.geometry_refresh import GeometryRefreshDue, RefreshWindow


def test_a_fresh_window_is_not_armed() -> None:
    assert RefreshWindow().armed is None


def test_arming_asks_for_a_deadline() -> None:
    window, due = RefreshWindow().arm(generation=3)

    assert due == GeometryRefreshDue(3)
    assert window.armed == due


def test_further_changes_in_the_same_batch_coalesce() -> None:
    """A resize publishes osd-dimensions, sub-pos and margins together; that is one refresh, not
    three, or libass runs three times for one visual change."""
    window, first = RefreshWindow().arm(generation=3)

    for _ in range(4):
        window, again = window.arm(generation=3)
        assert again is None

    assert window.armed == first


def test_a_new_generation_supersedes_the_armed_refresh() -> None:
    window, first = RefreshWindow().arm(generation=3)
    window, second = window.arm(generation=4)

    assert second == GeometryRefreshDue(4)
    assert second != first
    assert window.armed == second


def test_the_armed_due_fires_on_its_own_generation() -> None:
    window, due = RefreshWindow().arm(generation=3)

    assert window.fires(due, generation=3)


def test_a_due_whose_generation_moved_underneath_is_dropped() -> None:
    """The tick drain refreshed only while the pipeline generation held; the deadline keeps that
    guard, so a source change between arming and due cannot render against the replacement."""
    window, due = RefreshWindow().arm(generation=3)

    assert not window.fires(due, generation=4)


def test_a_superseded_due_is_dropped_even_on_a_matching_generation() -> None:
    _window, first = RefreshWindow().arm(generation=3)
    window, _second = RefreshWindow().arm(generation=3)
    window, _third = window.arm(generation=4)

    assert not window.fires(first, generation=3)


def test_a_due_after_retirement_is_dropped() -> None:
    window, due = RefreshWindow().arm(generation=3)

    assert not window.retire().fires(due, generation=3)


def test_retiring_disarms() -> None:
    window, _due = RefreshWindow().arm(generation=3)

    assert window.retire().armed is None


def test_a_retired_window_arms_again() -> None:
    window, _due = RefreshWindow().arm(generation=3)
    window, due = window.retire().arm(generation=3)

    assert due == GeometryRefreshDue(3)
    assert window.fires(due, generation=3)


def test_a_window_is_immutable() -> None:
    window = RefreshWindow()

    window.arm(generation=3)

    assert window.armed is None
