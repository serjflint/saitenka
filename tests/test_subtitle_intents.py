"""WP4.2: the subtitle-owned commands decide purely and return typed effects."""

from __future__ import annotations

from saitenka.app.languages import MAIN_LANG, SECOND_LANG
from saitenka.app.subtitle_intents import (
    AcquireSubtitles,
    AcquisitionSource,
    AdoptCurrentAsTarget,
    Announce,
    SelectTrack,
    SetAnnotationMode,
    SubtitleCommand,
    SubtitleInputs,
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
