"""The mpv-property observation boundary for one study session."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from saitenka.mpvio.gateway import register_observer_set
from saitenka.runtime import events, playback
from saitenka.runtime.playback_slice import PlaybackReducer, PlaybackStore

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from saitenka.mpvio.ipc import MpvIPC


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
    "eof-reached",
)


class PlaybackObservationController:
    """Own raw property observation and the resulting playback projection.

    Typed deltas leave through ``apply``; their cross-feature consequences remain the session
    turn's responsibility.
    """

    def __init__(
        self,
        ipc: MpvIPC,
        apply: Callable[[playback.PlaybackDelta], None],
    ) -> None:
        self._ipc = ipc
        self._apply = apply
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

    def retire_episode(self) -> None:
        self.dispatch(events.EpisodeRetired())
