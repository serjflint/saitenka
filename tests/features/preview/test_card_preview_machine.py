"""The mined-card preview's slice: what is composed, its clip, and the enlarge toggle."""

from __future__ import annotations

from saitenka.runtime.card_preview import CardPreview, dismissed, shown, zoom_toggled

ZOOMED = CardPreview(content="card", audio="clip.opus", zoom=True)


def test_a_fresh_preview_starts_un_zoomed() -> None:
    """A new card at the last card's magnification is a surprise, and as an assignment at the show
    site it is a rule every second way to show one has to remember."""
    assert shown("next", "next.opus").state == CardPreview("next", "next.opus")


def test_the_enlarge_toggle_flips_both_ways() -> None:
    unzoomed = zoom_toggled(ZOOMED).state
    assert unzoomed.zoom is False
    assert zoom_toggled(unzoomed).state.zoom is True
    assert unzoomed.content == "card", "toggling magnification is not a new preview"


def test_a_dismiss_forgets_the_clip_with_the_panel() -> None:
    """A ▶ on a preview that is gone has nothing to press, and keeping the path would let a replay
    resurrect a dismissed card."""
    assert dismissed().state == CardPreview()


def test_shown_ness_is_having_something_composed() -> None:
    """The registry's uniform predicate. Deliberately not "a rect is placed", which is the same
    answer one step later — a composed preview is always drawn before anything can look."""
    assert CardPreview().open is False
    assert CardPreview(content="card").open is True
