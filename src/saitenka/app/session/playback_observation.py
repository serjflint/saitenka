"""The mpv-property observation boundary for one study session."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from saitenka.app.session.mpv_gateway import register_observer_set
from saitenka.runtime import events, playback
from saitenka.runtime.playback_slice import PlaybackReducer, PlaybackStore

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from saitenka.app.subtitle_presentation import SubtitlePresentation
    from saitenka.mpvio.ipc import MpvIPC

log = logging.getLogger(__name__)


# One initial read seeds each property; subsequent values arrive as ordered observations.
OBSERVED_PROPERTIES = (
    "sub-text",
    "sub-text/ass-full",
    "mouse-pos",
    "osd-dimensions",
    "pause",
    "secondary-sub-text",
    "sid",
    "sub-start",
    "sub-end",
    "sub-delay",
    "time-pos",
    "video-out-params",
    "options/sub-ass-override",
    "options/sub-ass-scale-with-window",
    "options/sub-scale",
    "options/sub-pos",
    "options/sub-use-margins",
    "options/sub-ass-force-margins",
    "options/sub-ass-video-aspect-override",
    "options/sub-ass-use-video-data",
    "options/sub-ass-style-overrides",
    "options/sub-scale-with-window",
    "options/sub-scale-by-window",
    "options/blend-subtitles",
    "options/sub-filter-sdh",
    "options/sub-font-provider",
    "options/embeddedfonts",
    "options/sub-fonts-dir",
    "options/sub-font",
    "options/osd-fonts-dir",
    "options/osd-font-provider",
    "options/sub-font-size",
    "options/sub-color",
    "options/sub-outline-color",
    "options/sub-back-color",
    "options/sub-border-style",
    "options/sub-outline-size",
    "options/sub-shadow-offset",
    "options/sub-spacing",
    "options/sub-margin-x",
    "options/sub-margin-y",
    "options/sub-align-x",
    "options/sub-align-y",
    "options/sub-blur",
    "options/sub-bold",
    "options/sub-italic",
    "options/sub-justify",
    "options/video-crop",
    "options/video-rotate",
    "options/sub-shaper",
    "options/sub-ass-justify",
    "options/sub-line-spacing",
    "options/sub-hinting",
    "options/sub-scale-signs",
    "eof-reached",
)


@dataclass(frozen=True, slots=True)
class PlaybackStartup:
    reconcile_cue: Callable[[str], None]
    refresh_render_space: Callable[[], object]
    observe_authored_subtitle: Callable[[dict], None]
    probe_display_sources: Callable[[str, dict], None]


class PlaybackObservationController:
    """Own raw property observation and the resulting playback projection.

    Typed deltas leave through ``apply``; their cross-feature consequences remain the session
    turn's responsibility.
    """

    def __init__(
        self,
        ipc: MpvIPC,
        apply: Callable[[playback.PlaybackDelta], None],
        startup: PlaybackStartup,
    ) -> None:
        self._ipc = ipc
        self._apply = apply
        self._startup = startup
        self._store = PlaybackStore(ipc, reducer=PlaybackReducer())
        self._observing = False

    @property
    def state(self) -> playback.PlaybackState:
        return self._store.current.state

    @property
    def cue(self) -> playback.CueFacts:
        return self.state.cue

    @property
    def observing(self) -> bool:
        return self._observing

    @property
    def routed(self) -> bool:
        return self._store.routed

    def query(self, name: str) -> object | None:
        return self._ipc.query(name)

    def value(self, name: str) -> Any:
        if self._observing and self.state.observes(name):
            return self.state.value(name)
        return self.query(name)

    def text(self, name: str) -> str | None:
        value = self.query(name)
        return value if isinstance(value, str) else None

    def number(self, name: str) -> float | None:
        value = self.query(name)
        return float(value) if isinstance(value, int | float) else None

    def mapping(self, name: str) -> dict:
        value = self.query(name)
        return deepcopy(value) if isinstance(value, dict) else {}

    def sequence(self, name: str) -> list:
        value = self.query(name)
        return deepcopy(value) if isinstance(value, list) else []

    def dispatch(self, event: events.PlaybackEvent | events.EpisodeRetired) -> None:
        for delta in self._store.dispatch(event):
            self._apply(delta)

    def observe(self, name: str, data: object) -> None:
        self.dispatch(events.PropertyObserved(name, data))

    def observe_event(self, event: Mapping[str, object]) -> None:
        name = event.get("name")
        if name:
            self.observe(str(name), event.get("data"))

    def install_seed(self, properties: Mapping[str, object]) -> None:
        """Install an already-read snapshot through the production reducer path."""
        for name, value in properties.items():
            self.dispatch(events.PropertySeeded(name, value))
        self._observing = True

    def start(self, *, connection_replaced: bool = False) -> dict[str, dict]:
        replies = register_observer_set(self._ipc, OBSERVED_PROPERTIES)
        replies = {
            name: replies.get(name) or {"error": "unavailable"} for name in OBSERVED_PROPERTIES
        }
        values = {name: reply.get("data") for name, reply in replies.items()}
        if connection_replaced:
            for name, value in values.items():
                self.observe(name, value)
            self._observing = True
        else:
            self.install_seed(values)
        return replies

    def start_session(self, *, connection_replaced: bool = False) -> None:
        replies = self.start(connection_replaced=connection_replaced)
        self._startup.observe_authored_subtitle(replies["sub-text/ass-full"])
        self._startup.reconcile_cue(str(self.value("sub-text") or ""))
        osd = self.value("osd-dimensions")
        log.info(
            "observing mpv props; seed osd-dimensions=%r sub-text=%r",
            osd,
            self.value("sub-text"),
        )
        self._startup.refresh_render_space()
        if osd is None:
            log.warning(
                "osd-dimensions seed is None — mpv isn't returning get_property replies; "
                "the overlay won't draw until that recovers"
            )
        else:
            self._startup.probe_display_sources(
                "seed",
                osd if isinstance(osd, dict) else {},
            )

    def retire_episode(self) -> None:
        self.dispatch(events.EpisodeRetired())


class AuthoredSubtitleProbe:
    """Own the once-per-file authored-ASS capability probe."""

    def __init__(
        self,
        ipc: MpvIPC,
        playback: PlaybackObservationController,
        presentation: SubtitlePresentation,
    ) -> None:
        self._ipc = ipc
        self._playback = playback
        self._presentation = presentation
        self._dirty = True

    def mark_dirty(self) -> None:
        self._dirty = True

    def resolve(self) -> None:
        native = self._presentation.native
        if native is None or not self._dirty:
            return
        if native.ass_full_capability.value == "unknown":
            reply = self._ipc.probe("sub-text/ass-full")
            self._playback.dispatch(events.PropertySeeded("sub-text/ass-full", reply.get("data")))
            native.observe_ass_full_reply(reply)
        self._dirty = False


@dataclass(frozen=True, slots=True)
class PlaybackApplication:
    """Closed set of owner-thread consequences produced by playback deltas."""

    retire_cue: Callable[[str], None]
    probe_authored_subtitle: Callable[[], None]
    observe_cue: Callable[[playback.ObservedCue], None]
    subtitle_selection_changed: Callable[[object], None]
    subtitle_timing_changed: Callable[[], None]
    geometry_input_changed: Callable[[], None]
    render_space_changed: Callable[[], None]
    end_of_file_changed: EndOfFileEffect
    pause_changed: PauseEffect
    secondary_text_changed: Callable[[object], None]
    pointer_moved: Callable[[], None]


class PlaybackProjection:
    """Interpret typed playback deltas exactly once on the owner thread."""

    def __init__(self, application: PlaybackApplication) -> None:
        self._application = application

    def apply_effect(self, effect: object) -> None:
        from saitenka.runtime.effects import ApplyPlaybackDeltas

        if not isinstance(effect, ApplyPlaybackDeltas):
            raise TypeError(f"expected ApplyPlaybackDeltas, got {type(effect).__name__}")
        for delta in effect.deltas:
            self.apply(delta)

    def apply(self, delta: playback.PlaybackDelta) -> None:
        target = self._application
        if isinstance(delta, playback.CueIdentityRetired):
            target.retire_cue(delta.reason.value)
        elif isinstance(delta, playback.AuthoredCueStale):
            target.probe_authored_subtitle()
        elif isinstance(delta, playback.CueObservationChanged):
            target.observe_cue(delta.cue)
        elif isinstance(delta, playback.SubtitleSelectionChanged):
            target.subtitle_selection_changed(delta.sid)
        elif isinstance(delta, playback.SubtitleTimingChanged):
            target.subtitle_timing_changed()
        elif isinstance(delta, playback.GeometryInputChanged):
            target.geometry_input_changed()
        elif isinstance(delta, playback.RenderSpaceChanged):
            if delta.property_name == "osd-dimensions":
                target.render_space_changed()
        elif isinstance(delta, playback.EndOfFileChanged):
            target.end_of_file_changed(reached=delta.reached)
        elif isinstance(delta, playback.PauseChanged):
            target.pause_changed(paused=delta.paused)
        elif isinstance(delta, playback.SecondaryTextChanged):
            target.secondary_text_changed(delta.value)
        elif isinstance(delta, playback.PointerMoved):
            target.pointer_moved()


class EndOfFileEffect(Protocol):
    def __call__(self, *, reached: bool) -> None: ...


class PauseEffect(Protocol):
    def __call__(self, *, paused: bool) -> None: ...
