"""WP4.2: the subtitle-owned commands decide purely and return typed effects."""

from __future__ import annotations

import pytest
from saitenka_tokenize.languages import MAIN_LANG, SECOND_LANG

from saitenka.app.subtitle_intents import (
    AcquireSubtitles,
    AcquisitionSource,
    AdoptCurrentAsTarget,
    Announce,
    CopyCueText,
    SeekCue,
    SelectTrack,
    SetAnnotationMode,
    SetSubtitleDelay,
    SubtitleCommand,
    SubtitleInputs,
    ToggleTranslation,
    reduce,
)
from saitenka.app.subtitle_selection import SubtitleTracks


def inputs(**overrides: object) -> SubtitleInputs:
    base = {
        "tracks": SubtitleTracks(jp_sid=2, en_sid=1),
        "active_sid": 2,
        "language": MAIN_LANG,
        "annotation_mode": "full",
        "has_cue": True,
        "retry_in_flight": False,
        "media_path": "/media/ep1.mkv",
        "has_external_sub": False,
        "has_cue_lines": True,
        "cue_starts": (1.0, 5.0, 9.0),
        "playhead": 5.2,
        "sub_delay": 0.0,
    }
    return SubtitleInputs(**{**base, **overrides})  # type: ignore[arg-type]


def test_every_command_produces_at_least_one_effect() -> None:
    """An accepted command never silently no-ops."""
    for command in SubtitleCommand:
        assert reduce(command, inputs())


# --- language toggle ---------------------------------------------------------------------------


def test_toggling_from_the_target_language_selects_the_secondary_track() -> None:
    effects = reduce(SubtitleCommand.TOGGLE_LANGUAGE, inputs())

    assert effects == (SelectTrack(1, SECOND_LANG),)


def test_toggling_without_a_usable_track_announces_instead_of_switching() -> None:
    effects = reduce(
        SubtitleCommand.TOGGLE_LANGUAGE,
        inputs(tracks=SubtitleTracks(None, None), active_sid=None),
    )

    # With no tracks at all the active sid matches the (absent) target, so the toggle aims at
    # the secondary and reports that side as unavailable.
    assert effects == (Announce("EN subtitles unavailable", "warn"),)


def test_unavailable_translation_names_the_configured_language() -> None:
    effects = reduce(
        SubtitleCommand.TOGGLE_LANGUAGE,
        inputs(
            tracks=SubtitleTracks(2, None),
            active_sid=2,
            second_language="de",
        ),
    )

    assert effects == (Announce("DE subtitles unavailable", "warn"),)


def test_unavailable_primary_names_the_configured_language() -> None:
    effects = reduce(
        SubtitleCommand.TOGGLE_LANGUAGE,
        inputs(
            tracks=SubtitleTracks(None, 8),
            active_sid=8,
            language=SECOND_LANG,
            main_language="fr",
            second_language="de",
        ),
    )

    assert effects == (Announce("FR subtitles unavailable", "warn"),)


# --- mark current as target --------------------------------------------------------------------


def test_marking_adopts_the_current_track_and_says_so() -> None:
    effects = reduce(SubtitleCommand.MARK_CURRENT_JAPANESE, inputs(active_sid=7))

    assert effects == (
        AdoptCurrentAsTarget(7),
        Announce("Marked current subtitles as Japanese"),
    )


def test_marking_nothing_is_a_warning_not_an_adoption() -> None:
    effects = reduce(SubtitleCommand.MARK_CURRENT_JAPANESE, inputs(active_sid=None))

    assert effects == (Announce("No subtitle track to mark", "warn"),)
    assert not any(isinstance(effect, AdoptCurrentAsTarget) for effect in effects)


# --- acquisition -------------------------------------------------------------------------------


def test_subtitles_already_on_screen_are_retimed_not_refetched() -> None:
    effects = reduce(SubtitleCommand.RETRY_ACQUISITION, inputs(has_external_sub=True))

    assert effects == (AcquireSubtitles("/media/ep1.mkv", AcquisitionSource.RESYNC_CURRENT),)


def test_retiming_carries_the_active_translation_role() -> None:
    effects = reduce(
        SubtitleCommand.RETRY_ACQUISITION,
        inputs(
            has_external_sub=True,
            language=SECOND_LANG,
            main_language="fr",
            second_language="de",
        ),
    )

    assert effects == (
        AcquireSubtitles(
            "/media/ep1.mkv",
            AcquisitionSource.RESYNC_CURRENT,
            SECOND_LANG,
            "de",
        ),
    )


def test_without_external_subtitles_the_providers_are_queried() -> None:
    effects = reduce(SubtitleCommand.RETRY_ACQUISITION, inputs(has_external_sub=False))

    assert effects == (AcquireSubtitles("/media/ep1.mkv", AcquisitionSource.PROVIDERS),)


def test_acquisition_without_media_is_rejected() -> None:
    effects = reduce(SubtitleCommand.RETRY_ACQUISITION, inputs(media_path=None))

    assert effects == (Announce("No media loaded for subtitle search", "warn"),)


def test_a_second_acquisition_while_one_runs_is_rejected() -> None:
    effects = reduce(SubtitleCommand.RETRY_ACQUISITION, inputs(retry_in_flight=True))

    assert effects == (Announce("Subtitle sync already running", "warn"),)
    assert not any(isinstance(effect, AcquireSubtitles) for effect in effects)


# --- annotation mode ---------------------------------------------------------------------------


def test_annotation_mode_flips_and_redraws_only_when_a_cue_is_up() -> None:
    with_cue = reduce(SubtitleCommand.TOGGLE_ANNOTATION_MODE, inputs(has_cue=True))
    without_cue = reduce(SubtitleCommand.TOGGLE_ANNOTATION_MODE, inputs(has_cue=False))

    assert with_cue == (
        SetAnnotationMode("hover", redraw=True),
        Announce("annotations: hover-only"),
    )
    assert without_cue[0] == SetAnnotationMode("hover", redraw=False)


def test_annotation_mode_toggles_back() -> None:
    effects = reduce(SubtitleCommand.TOGGLE_ANNOTATION_MODE, inputs(annotation_mode="hover"))

    assert effects == (SetAnnotationMode("full", redraw=True), Announce("annotations: full"))


def test_the_reducer_never_mutates_its_inputs() -> None:
    given = inputs()

    for command in SubtitleCommand:
        reduce(command, given)

    assert given == inputs()


# --- navigation, anchoring, copy, translation ---------------------------------------------


@pytest.mark.parametrize(
    ("command", "delta"),
    [
        (SubtitleCommand.NAVIGATE_PREVIOUS, -1),
        (SubtitleCommand.REPLAY_CUE, 0),
        (SubtitleCommand.NAVIGATE_NEXT, 1),
    ],
)
def test_navigation_asks_for_the_matching_step(command: SubtitleCommand, delta: int) -> None:
    """The step carries the cue it is relative to. A bare delta cannot be checked for staleness by
    anyone downstream, because "previous" does not say previous to what."""
    assert reduce(command, inputs(cue_revision=7)) == (SeekCue(delta, 7),)


def test_anchoring_snaps_the_nearest_cue_to_the_playhead() -> None:
    effects = reduce(SubtitleCommand.ANCHOR_TIMING, inputs(playhead=5.2, sub_delay=0.0))

    assert effects[0] == SetSubtitleDelay(pytest.approx(0.2))
    assert isinstance(effects[1], Announce)


def test_anchoring_is_cumulative_from_the_current_delay() -> None:
    """The nearest cue is chosen on the DELAYED timeline — what is actually on screen — so a
    second anchor refines the first instead of fighting it."""
    effects = reduce(SubtitleCommand.ANCHOR_TIMING, inputs(playhead=5.2, sub_delay=4.0))

    # With a +4s delay the cue starting at 1.0 shows at 5.0, so it is the one being heard.
    assert effects[0] == SetSubtitleDelay(pytest.approx(4.2))


def test_anchoring_without_a_track_says_so() -> None:
    effects = reduce(SubtitleCommand.ANCHOR_TIMING, inputs(cue_starts=()))

    assert effects == (Announce("No subtitle track to anchor", "warn"),)


def test_anchoring_without_a_playhead_does_nothing() -> None:
    assert reduce(SubtitleCommand.ANCHOR_TIMING, inputs(playhead=None)) == ()


def test_copying_a_cue_copies_and_confirms() -> None:
    effects = reduce(SubtitleCommand.COPY_LINE, inputs(has_cue_lines=True))

    assert effects == (CopyCueText(), Announce("copied line"))


def test_copying_with_nothing_rendered_warns_instead() -> None:
    effects = reduce(SubtitleCommand.COPY_LINE, inputs(has_cue_lines=False))

    assert effects == (Announce("no line to copy", "warn"),)
    assert not any(isinstance(effect, CopyCueText) for effect in effects)


def test_translation_toggles_unconditionally() -> None:
    assert reduce(SubtitleCommand.TOGGLE_TRANSLATION, inputs()) == (ToggleTranslation(),)
