"""The impure ends of the subtitle commands: track selection, acquisition, seek, delay, copy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app import subtitle_intents, subtitle_modes
from saitenka.app.intents import Announce
from saitenka.app.media import copy_clipboard
from saitenka.app.mpv_egress import send_correlated
from saitenka.runtime import events
from saitenka.runtime.effects import Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.annotation.annotation_controller import AnnotationView
    from saitenka.app.session.context import EpisodeSlot
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.app.toast_controller import NotificationSink
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.playback_slice import PlaybackStore
    from saitenka.runtime.presentation_slice import TranslationStore
    from saitenka.runtime.subtitle import SubtitleTrackState
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore


class SubmitSubtitleFetch(Protocol):
    def __call__(
        self,
        request: subtitle_modes.SubtitleFetchRequest,
        *,
        name: str,
        on_done: Callable[[], None] | None = None,
    ) -> None: ...


class HideTranslation(Protocol):
    def __call__(self, *, release: bool) -> None: ...


class AnnotationViewSource(Protocol):
    """The annotation fact exposed to subtitle command policy."""

    @property
    def view(self) -> AnnotationView: ...


@dataclass
class SubtitleTrackCoordinator:
    """Build fresh track decisions from stable subtitle and episode owners."""

    ipc: MpvIPC
    tracks: SubtitleTrackStore
    episodes: EpisodeSlot
    playback: PlaybackStore
    property_value: Callable[[str], object | None]
    notifications: NotificationSink
    invalidate: Callable[[], None]
    translation_visible: Callable[[], bool]
    rebuild_index: Callable[[], None]
    install_cue: Callable[[str], None]

    def ports(self) -> subtitle_modes.TrackPorts:
        return subtitle_modes.TrackPorts(
            ipc=self.ipc,
            get=self.property_value,
            toast=self.notifications.show,
            tracks=self.current,
            declare=self.tracks.dispatch,
            invalidate=self.invalidate,
            translation_visible=self.translation_visible,
            drop_index=self.drop_index,
            rebuild_index=self.rebuild_index,
            sample_cue=self.sample_cue,
            clear_cue=self.clear_cue,
            redraw_cue=self.redraw_cue,
        )

    def current(self) -> SubtitleTrackState:
        return self.tracks.current

    def drop_index(self) -> None:
        self.episodes.current.sub_index = None

    def sample_cue(self) -> str:
        return subtitle_modes._sample_cue_text(
            self.episodes.current.sub_index,
            self.playback.current.state.cue.text,
        )

    def clear_cue(self) -> None:
        self.install_cue("")

    def redraw_cue(self) -> None:
        self.install_cue(self.playback.current.state.cue.text)


@dataclass(frozen=True, slots=True)
class SubtitleCommandRead:
    """Fresh subtitle facts sampled by the pure command reducer."""

    ipc: MpvIPC
    episodes: EpisodeSlot
    playback: PlaybackStore
    tracks: SubtitleTrackStore
    cue: CueRenderStore
    annotation: AnnotationViewSource
    observed_property: Callable[[str], object]
    property_value: Callable[[str], object | None]
    text_property: Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class SubtitleCommandApply:
    """Named subtitle, translation, and mpv acts the coordinator may perform."""

    ipc: MpvIPC
    episodes: EpisodeSlot
    track: SubtitleTrackCoordinator
    submit_fetch: SubmitSubtitleFetch
    set_annotation_mode: Callable[[subtitle_intents.AnnotationMode], None]
    draw_subtitle: Callable[[], None]
    seek_cue: Callable[[subtitle_intents.SeekCue], bool]
    sentence_lines: Callable[[], list[str]]
    translation_store: TranslationStore
    reveal_translation: Callable[[], None]
    hide_translation: HideTranslation
    translation_visible: Callable[[], bool]
    property_value: Callable[[str], object | None]
    notifications: NotificationSink


class SubtitleCommandCoordinator:
    """Coordinate subtitle commands without retaining the live session shell."""

    def __init__(self, read: SubtitleCommandRead, apply: SubtitleCommandApply) -> None:
        self._read = read
        self._apply = apply

    def inputs(self) -> subtitle_intents.SubtitleInputs:
        from saitenka.app.subtitle_modes import _current_external_sub

        read = self._read
        episode = read.episodes.current
        index = episode.sub_index
        playback = read.playback.current.state
        track = read.tracks.current
        cue = read.cue.current
        playhead = read.observed_property("time-pos")
        return subtitle_intents.SubtitleInputs(
            tracks=subtitle_modes.discover_tracks(read.ipc, track.slang),
            active_sid=read.property_value("sid"),
            language=track.language,
            annotation_mode=read.annotation.view.mode,
            has_cue=bool(playback.cue.text.strip()),
            retry_in_flight=episode.subtitle.retry_active,
            media_path=read.text_property("path"),
            has_external_sub=_current_external_sub(read.ipc) is not None,
            has_cue_lines=bool(cue.lines),
            cue_starts=tuple(cue.start for cue in index.cues) if index is not None else (),
            playhead=None if playhead is None else float(playhead),  # type: ignore[arg-type]
            sub_delay=float(read.observed_property("sub-delay") or 0.0),  # type: ignore[arg-type]
            cue_revision=playback.cue.cue.value,
        )

    def apply(self, effect: subtitle_intents.SubtitleEffect, /) -> None:
        apply = self._apply
        if isinstance(effect, subtitle_intents.SelectTrack):
            subtitle_modes.select_track(apply.track.ports(), effect.sid, effect.target)
        elif isinstance(effect, subtitle_intents.AdoptCurrentAsTarget):
            subtitle_modes.adopt_current_as_target(apply.track.ports(), effect.sid)
        elif isinstance(effect, subtitle_intents.AcquireSubtitles):
            self._acquire(effect)
        elif isinstance(effect, subtitle_intents.SetAnnotationMode):
            apply.set_annotation_mode(effect.mode)
            if effect.redraw:
                apply.draw_subtitle()
        elif isinstance(effect, subtitle_intents.SeekCue):
            apply.seek_cue(effect)
        elif isinstance(effect, subtitle_intents.SetSubtitleDelay):
            send_correlated(
                apply.ipc,
                "sub-delay",
                "set_property",
                "sub-delay",
                f"{effect.seconds:.3f}",
                owner=Owner.SUBTITLE,
            )
        elif isinstance(effect, subtitle_intents.CopyCueText):
            copy_clipboard("\n".join(apply.sentence_lines()))
        elif isinstance(effect, subtitle_intents.ToggleTranslation):
            self._toggle_translation()
        elif isinstance(effect, Announce):
            apply.notifications.show(effect.text, effect.kind)

    def _acquire(self, effect: subtitle_intents.AcquireSubtitles) -> None:
        apply = self._apply
        subtitle_modes.begin_acquisition(
            apply.submit_fetch,
            apply.property_value,
            apply.notifications.show,
            apply.episodes.subtitle_source,
            apply.ipc,
            effect.media_path,
            effect.source,
        )

    def _toggle_translation(self) -> None:
        apply = self._apply
        apply.translation_store.dispatch(
            events.TranslationHeld(not apply.translation_store.current.held)
        )
        if apply.translation_visible():
            apply.reveal_translation()
        else:
            apply.hide_translation(release=True)
