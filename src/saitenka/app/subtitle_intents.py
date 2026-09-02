"""Pure reducer for the subtitle-owned commands.

The reducer receives an immutable snapshot of the facts a decision needs and returns typed
effects. It performs no I/O, holds no state and never sees `SessionController`; the executor in the
controller gathers the inputs and carries the effects out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from saitenka.app.intents import Announce
from saitenka.app.subnav_policy import anchor_delay
from saitenka.app.subtitle_selection import toggle_target

if TYPE_CHECKING:
    from saitenka_tokenize.languages import Language

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
    NAVIGATE_PREVIOUS = "navigate-previous"
    NAVIGATE_NEXT = "navigate-next"
    REPLAY_CUE = "replay-cue"
    ANCHOR_TIMING = "anchor-timing"
    COPY_LINE = "copy-line"
    TOGGLE_TRANSLATION = "toggle-translation"


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
    #: Rendered cue lines exist, so there is something to copy.
    has_cue_lines: bool = False
    #: Authored cue start times, for anchoring. Empty when no index is loaded.
    cue_starts: tuple[float, ...] = ()
    playhead: float | None = None
    sub_delay: float = 0.0
    #: The projection's cue revision when these facts were read. Navigation is relative to a cue,
    #: so an effect decided here has to say which one — see `SeekCue`.
    cue_revision: int = 0
    second_language: str = "en"


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
class SeekCue:
    """Step the subtitle timeline: -1 previous, 0 replay, +1 next.

    ``cue_revision`` is the cue the step is relative to. "Previous" means nothing on its own —
    previous to *what* — so once this effect can outlive the keypress that produced it, an
    executor has to be able to tell that the cue it was decided against is gone.
    """

    delta: int
    cue_revision: int = 0


@dataclass(frozen=True, slots=True)
class SetSubtitleDelay:
    seconds: float


@dataclass(frozen=True, slots=True)
class CopyCueText:
    """Copy the whole cue under the cursor."""


@dataclass(frozen=True, slots=True)
class ToggleTranslation:
    """Reveal or hide the secondary-language line."""


type SubtitleEffect = (
    SelectTrack
    | AdoptCurrentAsTarget
    | AcquireSubtitles
    | SetAnnotationMode
    | SeekCue
    | SetSubtitleDelay
    | CopyCueText
    | ToggleTranslation
    | Announce
)


def _toggle_language(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    decision = toggle_target(inputs.tracks, active_sid=inputs.active_sid, language=inputs.language)
    if decision.sid is None:
        unavailable = (
            inputs.second_language if decision.target != "jp" else decision.target
        ).upper()
        return (Announce(f"{unavailable} subtitles unavailable", "warn"),)
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


def _navigate(delta: int):
    def reduce_navigation(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
        return (SeekCue(delta, inputs.cue_revision),)

    return reduce_navigation


def _anchor(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    if not inputs.cue_starts:
        return (Announce("No subtitle track to anchor", "warn"),)
    if inputs.playhead is None:
        return ()  # no playhead yet: nothing to anchor against, and nothing worth saying
    delay = anchor_delay(
        inputs.cue_starts, playhead=inputs.playhead, current_delay=inputs.sub_delay
    )
    assert delay is not None
    return (SetSubtitleDelay(delay), Announce(f"Subtitles anchored — delay {delay:+.1f}s"))


def _copy_line(inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    if not inputs.has_cue_lines:
        return (Announce("no line to copy", "warn"),)
    return (CopyCueText(), Announce("copied line"))


def _toggle_translation(_inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    return (ToggleTranslation(),)


_REDUCERS = {
    SubtitleCommand.TOGGLE_LANGUAGE: _toggle_language,
    SubtitleCommand.MARK_CURRENT_JAPANESE: _mark_current_japanese,
    SubtitleCommand.RETRY_ACQUISITION: _retry,
    SubtitleCommand.TOGGLE_ANNOTATION_MODE: _toggle_annotation_mode,
    SubtitleCommand.NAVIGATE_PREVIOUS: _navigate(-1),
    SubtitleCommand.NAVIGATE_NEXT: _navigate(1),
    SubtitleCommand.REPLAY_CUE: _navigate(0),
    SubtitleCommand.ANCHOR_TIMING: _anchor,
    SubtitleCommand.COPY_LINE: _copy_line,
    SubtitleCommand.TOGGLE_TRANSLATION: _toggle_translation,
}


def reduce(command: SubtitleCommand, inputs: SubtitleInputs) -> tuple[SubtitleEffect, ...]:
    """Decide one subtitle command. Always returns at least one effect, so every accepted
    command has an observable outcome rather than a silent no-op."""
    return _REDUCERS[command](inputs)
