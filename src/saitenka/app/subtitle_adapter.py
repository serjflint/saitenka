"""The impure ends of the subtitle commands: track selection, acquisition, seek, delay, copy."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka import otel_metrics
from saitenka.app import subnav, subnav_settle, subtitle_intents, subtitle_modes
from saitenka.app.intents import Announce
from saitenka.app.media import copy_clipboard
from saitenka.app.mpv_egress import send_correlated
from saitenka.runtime import EffectFinished, EffectOutcome
from saitenka.runtime.effects import Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.annotation.annotation_controller import AnnotationView
    from saitenka.app.features.subtitle import SubtitleAcquisitionController
    from saitenka.app.features.subtitle.navigation_state import NavigationStore
    from saitenka.app.features.translation import TranslationController, TranslationInputs
    from saitenka.app.native_subtitles import NativeSubtitleGeometry
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.app.toast_controller import NotificationSink
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime import events
    from saitenka.runtime.playback import PlaybackCueView
    from saitenka.runtime.subtitle import SubtitleTrackState
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore


class AnnotationViewSource(Protocol):
    """The annotation fact exposed to subtitle command policy."""

    @property
    def view(self) -> AnnotationView: ...


class SourceReplacer(Protocol):
    def __call__(self, path: object = None, *, reason: str) -> None: ...


@dataclass
class SubtitleTrackCoordinator:
    """Build fresh track decisions from stable subtitle and episode owners."""

    ipc: MpvIPC
    tracks: SubtitleTrackStore
    navigation: NavigationStore
    playback: PlaybackCueView
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

    def declare(self, event: events.SubtitleEvent) -> SubtitleTrackState:
        return self.tracks.dispatch(event)

    def acquire(self) -> object:
        return subtitle_modes.setup_secondary(self.ports())

    def release(self) -> None:
        subtitle_modes.release_secondary(self.ports())

    def drop_index(self) -> None:
        self.navigation.current.sub_index = None

    def sample_cue(self) -> str:
        return subtitle_modes._sample_cue_text(
            self.navigation.current.sub_index,
            self.playback.cue.text,
        )

    def clear_cue(self) -> None:
        self.install_cue("")

    def redraw_cue(self) -> None:
        self.install_cue(self.playback.cue.text)


_SETTLE_TIMER = "subtitle:navigation-settle"


@dataclass
class SubtitleNavigationCoordinator:
    """Own subtitle-index mutation, seek admission, and the settle-window lifecycle."""

    ipc: MpvIPC
    navigation: NavigationStore
    geometry: Callable[[], NativeSubtitleGeometry | None]
    get: Callable[[str], object | None]
    cue_text: Callable[[], str]
    cue_retired: Callable[[], bool]
    draw_cue: Callable[[str], None]
    replace_source: SourceReplacer
    invalidate: Callable[[], None]
    warm_tokens: Callable[[], None]
    index_changed: Callable[[], None]
    cue_revision: Callable[[], int]
    invalidate_pipeline: Callable[[], object]

    def ports(self) -> subnav.NavPorts:
        def geometry_hint(cue) -> None:
            self.navigation.current.geometry_cue_hint = cue

        return subnav.NavPorts(
            episode=self.navigation.current,
            geometry=self.geometry(),
            get=self.get,
            cue_text=self.cue_text,
            cue_retired=self.cue_retired,
            draw_cue=self.draw_cue,
            replace_source=self.replace_source,
            invalidate=self.invalidate,
            open_settle=self.open_settle,
            retire_settle=self.retire_settle,
            warm_tokens=self.warm_tokens,
            index_changed=self.index_changed,
            geometry_hint=geometry_hint,
        )

    def load_index(self, path) -> None:
        subnav.load_sub_index(self.ports(), path)

    def reconcile(self, text: str) -> None:
        subnav.reconcile_sub_text(self.ports(), text)

    def seek(self, effect: subtitle_intents.SeekCue) -> bool:
        with otel_metrics.traced("sub_nav_identity") as span:
            span.set("delta", effect.delta)
            span.set("requested_for", effect.cue_revision)
            if effect.cue_revision != self.cue_revision():
                span.set("outcome", "superseded")
                return False
            span.set("outcome", "executed")
        self.invalidate_pipeline()
        self.navigate(effect.delta)
        send_correlated(
            self.ipc,
            "sub-seek",
            "sub-seek",
            str(effect.delta),
            owner=Owner.SUBTITLE,
        )
        return True

    def navigate(self, delta: int) -> bool:
        return subnav.sub_nav(self.ports(), delta)

    def open_settle(self) -> None:
        window = self.navigation.current.sub_settle.begin()
        self.navigation.current.sub_settle = window
        identity = window.identity

        def due(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self.settle_due(identity)

        if not self.ipc.schedule_runtime_timer(
            owner=Owner.SUBTITLE,
            identity=identity,
            timer=_SETTLE_TIMER,
            due_at=time.monotonic() + subnav_settle.SETTLE_SECONDS,
            on_finished=due,
        ):
            self.navigation.current.sub_settle = window.retire()

    def settle_due(self, identity: subnav_settle.NavigationSettleDue) -> None:
        self.navigation.current.sub_settle = self.navigation.current.sub_settle.due(identity)

    def retire_settle(self) -> None:
        if not self.navigation.current.sub_settle.open:
            return
        self.navigation.current.sub_settle = self.navigation.current.sub_settle.retire()
        self.ipc.cancel_runtime_timer(_SETTLE_TIMER)


@dataclass(frozen=True, slots=True)
class SubtitleCommandRead:
    """Fresh subtitle facts sampled by the pure command reducer."""

    ipc: MpvIPC
    navigation: NavigationStore
    playback: PlaybackCueView
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
    track: SubtitleTrackCoordinator
    acquisition: SubtitleAcquisitionController
    set_annotation_mode: Callable[[subtitle_intents.AnnotationMode], None]
    draw_subtitle: Callable[[], None]
    seek_cue: Callable[[subtitle_intents.SeekCue], bool]
    sentence_lines: Callable[[], list[str]]
    translation: TranslationController
    translation_inputs: Callable[[], TranslationInputs]
    notifications: NotificationSink


class SubtitleCommandCoordinator:
    """Coordinate subtitle commands without retaining the live session shell."""

    def __init__(self, read: SubtitleCommandRead, apply: SubtitleCommandApply) -> None:
        self._read = read
        self._apply = apply

    def inputs(self) -> subtitle_intents.SubtitleInputs:
        from saitenka.app.subtitle_modes import _current_external_sub

        read = self._read
        episode = read.navigation.current
        index = episode.sub_index
        cue_facts = read.playback.cue
        track = read.tracks.current
        cue = read.cue.current
        playhead = read.observed_property("time-pos")
        return subtitle_intents.SubtitleInputs(
            tracks=subtitle_modes.discover_tracks(read.ipc, track.slang),
            active_sid=read.property_value("sid"),
            language=track.language,
            annotation_mode=read.annotation.view.mode,
            has_cue=bool(cue_facts.text.strip()),
            retry_in_flight=self._apply.acquisition.retry_in_flight,
            media_path=read.text_property("path"),
            has_external_sub=_current_external_sub(read.ipc) is not None,
            has_cue_lines=bool(cue.lines),
            cue_starts=tuple(cue.start for cue in index.cues) if index is not None else (),
            playhead=None if playhead is None else float(playhead),  # type: ignore[arg-type]
            sub_delay=float(read.observed_property("sub-delay") or 0.0),  # type: ignore[arg-type]
            cue_revision=cue_facts.cue.value,
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
        apply.acquisition.begin(effect.media_path, effect.source)

    def _toggle_translation(self) -> None:
        apply = self._apply
        apply.translation.toggle(apply.translation_inputs())
