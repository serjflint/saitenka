"""WP5.3: hiding and restoring every saitenka surface is an ordered decision."""

from __future__ import annotations

from saitenka.app.intents import DismissHover
from saitenka.app.session_intents import (
    ReleaseSecondarySubtitles,
    ResumeSubtitles,
    SessionCommand,
    SessionInputs,
    SetSurfacesVisible,
    ShowTranslation,
    SuspendSubtitles,
    reduce,
)

TOGGLE = SessionCommand.TOGGLE_OVERLAY


def test_hiding_tears_the_tooltip_down_before_the_pixels_go() -> None:
    """The tooltip is one of the surfaces about to vanish, and tearing it down afterwards resumes
    playback (hover auto-pause) against a session that has already gone dark."""
    assert reduce(TOGGLE, SessionInputs(overlay_visible=True)) == (
        DismissHover(),
        SetSurfacesVisible(visible=False),
        ReleaseSecondarySubtitles(),
        SuspendSubtitles(),
    )


def test_the_secondary_track_is_released_only_after_the_hide() -> None:
    """Releasing first would let mpv paint the secondary line for the frames before the hide lands."""
    effects = reduce(TOGGLE, SessionInputs(overlay_visible=True))

    assert effects.index(SetSurfacesVisible(visible=False)) < effects.index(
        ReleaseSecondarySubtitles()
    )


def test_showing_resumes_the_pipeline_before_drawing_a_translation() -> None:
    assert reduce(TOGGLE, SessionInputs(overlay_visible=False, translation_wanted=True)) == (
        SetSurfacesVisible(visible=True),
        ResumeSubtitles(),
        ShowTranslation(),
    )


def test_showing_without_a_wanted_translation_draws_none() -> None:
    assert reduce(TOGGLE, SessionInputs(overlay_visible=False)) == (
        SetSurfacesVisible(visible=True),
        ResumeSubtitles(),
    )


def test_the_translation_input_is_want_not_visibility() -> None:
    """The trap this input name exists to avoid. "Is the translation visible" includes "is the
    overlay visible", which is False at exactly the moment this decision is made — asking it that
    way restores the surfaces and never brings the secondary line back with them.
    """
    restoring = SessionInputs(overlay_visible=False, translation_wanted=True)

    assert ShowTranslation() in reduce(TOGGLE, restoring)


def test_the_reducer_reads_its_inputs_without_mutating_them() -> None:
    given = SessionInputs(overlay_visible=True, translation_wanted=True)

    reduce(TOGGLE, given)

    assert given == SessionInputs(overlay_visible=True, translation_wanted=True)
