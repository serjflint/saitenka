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


def test_cycling_moves_to_the_next_configured_profile() -> None:
    from saitenka.app.session_intents import SwitchProfile

    assert reduce(
        SessionCommand.CYCLE_PROFILE, SessionInputs(profile_count=3, profile_index=1)
    ) == (SwitchProfile(2),)


def test_cycling_wraps_at_the_end() -> None:
    from saitenka.app.session_intents import SwitchProfile

    assert reduce(
        SessionCommand.CYCLE_PROFILE, SessionInputs(profile_count=3, profile_index=2)
    ) == (SwitchProfile(0),)


def test_a_single_profile_session_is_inert() -> None:
    """Almost every session. There is nothing to cycle to, and saying so on each keypress would be
    noise rather than information."""
    assert reduce(SessionCommand.CYCLE_PROFILE, SessionInputs(profile_count=1)) == ()


def test_the_decision_is_which_profile_not_whether_it_resolves() -> None:
    """Resolving a tokenizer and re-scoping dictionaries is I/O that can fail and revert; the
    reducer says which one to try, which is the part that is a decision."""
    from saitenka.app.session_intents import SwitchProfile

    (effect,) = reduce(
        SessionCommand.CYCLE_PROFILE, SessionInputs(profile_count=2, profile_index=0)
    )

    assert effect == SwitchProfile(1)
