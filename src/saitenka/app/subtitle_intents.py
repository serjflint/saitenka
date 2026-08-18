"""Pure reducer for the subtitle-owned commands (WP4.2 of the runtime migration).

The reducer receives an immutable snapshot of the facts a decision needs and returns typed
effects. It performs no I/O, holds no state and never sees `Reader`; the executor in the
controller gathers the inputs and carries the effects out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from saitenka.app.subtitle_selection import toggle_target

if TYPE_CHECKING:
    from saitenka.app.languages import Language
    from saitenka.app.subtitle_selection import SubtitleTracks

type AnnotationMode = Literal["full", "hover"]
ANNOTATION_FULL: AnnotationMode = "full"
ANNOTATION_HOVER: AnnotationMode = "hover"


class SubtitleCommand(StrEnum):
    """The wire names this reducer owns. Each maps to exactly one intent."""

    TOGGLE_LANGUAGE = "toggle-language"
    MARK_CURRENT_JAPANESE = "mark-current-japanese"
    RETRY_ACQUISITION = "retry-acquisition"
    TOGGLE_ANNOTATION_MODE = "toggle-annotation-mode"


@dataclass(frozen=True, slots=True)
class SubtitleInputs:
    """Every fact the subtitle commands decide from, read once by the executor."""

    tracks: SubtitleTracks
    active_sid: object
    language: Language
    annotation_mode: AnnotationMode
    has_cue: bool
    retry_in_flight: bool
    media_path: str | None
    has_external_sub: bool


# --- effects ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectTrack:
    """Make `sid` the primary track and adopt `target` as the active role."""

    sid: int
    target: Language


@dataclass(frozen=True, slots=True)
class AdoptCurrentAsTarget:
    """Treat the current primary track as the target language whatever its tag says."""

    sid: object


class AcquisitionSource(StrEnum):
    #: Re-time the external subtitles already on screen; never queries a provider.
    RESYNC_CURRENT = "resync-current"
    #: Ask the configured providers for a target-language subtitle.
    PROVIDERS = "providers"


@dataclass(frozen=True, slots=True)
class AcquireSubtitles:
    media_path: str
    source: AcquisitionSource


@dataclass(frozen=True, slots=True)
class SetAnnotationMode:
    mode: AnnotationMode
    redraw: bool


@dataclass(frozen=True, slots=True)
class Announce:
    text: str
    kind: str = "ok"


type SubtitleEffect = (
    SelectTrack | AdoptCurrentAsTarget | AcquireSubtitles | SetAnnotationMode | Announce
)


def _toggle_language(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    decision = toggle_target(inputs.tracks, active_sid=inputs.active_sid, language=inputs.language)
    if decision.sid is None:
        return (Announce(f"{decision.target.upper()} subtitles unavailable", "warn"),)
    return (SelectTrack(decision.sid, decision.target),)


def _mark_current_japanese(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    if inputs.active_sid is None:
        return (Announce("No subtitle track to mark", "warn"),)
    return (
        AdoptCurrentAsTarget(inputs.active_sid),
        Announce("Marked current subtitles as Japanese"),
    )


def _retry(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    if not inputs.media_path:
        return (Announce("No media loaded for subtitle search", "warn"),)
    if inputs.retry_in_flight:
        return (Announce("Subtitle sync already running", "warn"),)
    # Subtitles already on screen only ever need re-timing — querying providers again would
    # replace a file the user already chose.
    source = (
        AcquisitionSource.RESYNC_CURRENT if inputs.has_external_sub else AcquisitionSource.PROVIDERS
    )
    return (AcquireSubtitles(inputs.media_path, source),)


def _toggle_annotation_mode(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    mode: AnnotationMode = (
        ANNOTATION_HOVER if inputs.annotation_mode == ANNOTATION_FULL else ANNOTATION_FULL
    )
    label = "full" if mode == ANNOTATION_FULL else "hover-only"
    return (SetAnnotationMode(mode, redraw=inputs.has_cue), Announce(f"annotations: {label}"))


_REDUCERS = {
    SubtitleCommand.TOGGLE_LANGUAGE: _toggle_language,
    SubtitleCommand.MARK_CURRENT_JAPANESE: _mark_current_japanese,
    SubtitleCommand.RETRY_ACQUISITION: _retry,
    SubtitleCommand.TOGGLE_ANNOTATION_MODE: _toggle_annotation_mode,
}


def reduce(command: SubtitleCommand, inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    """Decide one subtitle command. Always returns at least one effect, so every accepted
    command has an observable outcome rather than a silent no-op."""
    return _REDUCERS[command](inputs)
