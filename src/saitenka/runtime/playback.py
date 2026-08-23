"""Immutable playback projection: ordered mpv observations become typed deltas.

The projection is the sole interpreter of raw mpv property observations. Callers hand it one
observation at a time and receive replaced state plus typed deltas; nothing downstream parses mpv
dictionaries or compares raw property values.

"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True, slots=True, order=True)
class Revision:
    """Explicit monotonic revision; identity components are values, never mutable counters."""

    value: int = 0

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("revisions must be non-negative")

    def advance(self) -> Revision:
        return Revision(self.value + 1)


class FactDomain(StrEnum):
    """The projection's ownership units. Legacy-owned domains publish no deltas."""

    CONNECTION = "connection"
    MEDIA = "media"
    TRACK = "track"
    CUE = "cue"
    RENDER_SPACE = "render-space"
    TIMING = "timing"
    POINTER = "pointer"
    PAUSE = "pause"
    EOF = "eof"


#: Domains whose deltas the legacy driver still owns, so publishing one would give a fact two
#: consumers. `POINTER` left in WP5.1 when hover moved off the interaction tick, and `PAUSE` in
#: WP5.4 when watch time started accruing on the transition rather than per tick. Empty is the
#: end state, not an oversight: every fact the projection sees is now published.
LEGACY_OWNED: frozenset[FactDomain] = frozenset()


class RetireReason(StrEnum):
    CUE_TEXT = "cue-text"
    CUE_START = "cue-start"
    CUE_END = "cue-end"
    TRACK = "track"
    SOURCE = "source"
    CONNECTION = "connection"
    ROLE = "role"


# Raw mpv property names, grouped by the domain that owns their interpretation.
CUE_PROPERTIES = frozenset({"sub-text", "sub-start", "sub-end"})
AUTHORED_PROPERTIES = frozenset({"sub-text/ass-full", "secondary-sub-text"})
TRACK_PROPERTIES = frozenset({"sid"})
TIMING_PROPERTIES = frozenset({"sub-delay", "time-pos"})
POINTER_PROPERTIES = frozenset({"mouse-pos"})
PAUSE_PROPERTIES = frozenset({"pause"})
EOF_PROPERTIES = frozenset({"eof-reached"})
RENDER_SPACE_PROPERTIES = frozenset(
    {
        "osd-dimensions",
        "video-out-params",
        "options/sub-ass-override",
        "options/sub-ass-scale-with-window",
        "options/sub-scale",
        "options/sub-pos",
        "options/sub-use-margins",
        "options/sub-ass-force-margins",
        "options/sub-ass-video-aspect-override",
        "options/sub-ass-use-video-data",
        "options/sub-ass-vsfilter-aspect-compat",
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
        "options/video-crop",
        "options/video-rotate",
    }
)
# A changed authored-text input invalidates the cached ``sub-text/ass-full`` probe.
_AUTHORED_STALE_PROPERTIES = frozenset({"sub-text"}) | TRACK_PROPERTIES
#: Every observation a geometry request is derived from — the render space plus the authored cue
#: rows and their timing. Wider than the render space: a new cue changes the geometry inputs
#: without changing the space they are laid out in.
GEOMETRY_INPUT_PROPERTIES = RENDER_SPACE_PROPERTIES | {"sub-text/ass-full", "sub-start", "sub-end"}

_RETIRE_ON_CHANGE = {
    "sub-text": RetireReason.CUE_TEXT,
    "sub-start": RetireReason.CUE_START,
    "sub-end": RetireReason.CUE_END,
    "sid": RetireReason.TRACK,
}
# sub-start/sub-end only conflict against an installed identity's own observed timing; a bare
# None or a value equal to what was installed is not evidence that the cue changed.
_TIMED_RETIREMENT = {"sub-start": "start", "sub-end": "end"}


@dataclass(frozen=True, slots=True)
class InstalledCue:
    """Timing the owning reducer recorded when it installed the current cue identity."""

    start: object = None
    end: object = None


@dataclass(frozen=True, slots=True)
class ConnectionFacts:
    epoch: int = 0
    ready: bool = True


@dataclass(frozen=True, slots=True)
class MediaFacts:
    source: Revision = Revision()
    path: object = None


@dataclass(frozen=True, slots=True)
class TrackFacts:
    track: Revision = Revision()
    sid: object = None
    role: str = ""


@dataclass(frozen=True, slots=True)
class CueFacts:
    cue: Revision = Revision()
    text: str = ""
    start: object = None
    end: object = None
    installed: InstalledCue | None = None
    authored_stale: bool = True


@dataclass(frozen=True, slots=True)
class RenderSpaceFacts:
    render_space: Revision = Revision()


@dataclass(frozen=True, slots=True)
class TimingFacts:
    delay: object = None
    position: object = None


@dataclass(frozen=True, slots=True)
class PointerFacts:
    position: object = None


@dataclass(frozen=True, slots=True)
class ObservedCue:
    """Semantic cue identity. Identical text under a new source/track/role/cue is a new value."""

    source: Revision
    track: Revision
    role: str
    text: str
    start: object
    end: object
    cue: Revision


# --- deltas -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceChanged:
    source: Revision


@dataclass(frozen=True, slots=True)
class SubtitleSelectionChanged:
    track: Revision
    sid: object
    role: str


@dataclass(frozen=True, slots=True)
class CueObservationChanged:
    cue: ObservedCue


@dataclass(frozen=True, slots=True)
class CueIdentityRetired:
    reason: RetireReason


@dataclass(frozen=True, slots=True)
class AuthoredCueStale:
    """The cached authored-ASS probe no longer matches the observed cue."""


@dataclass(frozen=True, slots=True)
class RenderSpaceChanged:
    render_space: Revision
    property_name: str


@dataclass(frozen=True, slots=True)
class GeometryInputChanged:
    """An input a geometry request is derived from changed; the cached hit map is stale."""

    property_name: str


@dataclass(frozen=True, slots=True)
class SubtitleTimingChanged:
    delay: object


@dataclass(frozen=True, slots=True)
class PointerMoved:
    position: object


@dataclass(frozen=True, slots=True)
class PauseChanged:
    paused: bool


@dataclass(frozen=True, slots=True)
class EndOfFileChanged:
    """Playback reached, or left, the end of the file.

    A delta *is* the edge — it exists only when the value changed — so a consumer needs no
    seen-it-already flag to keep mpv sitting paused at EOF from re-triggering it.
    """

    reached: bool


@dataclass(frozen=True, slots=True)
class ConnectionChanged:
    epoch: int
    ready: bool


type PlaybackDelta = (
    SourceChanged
    | SubtitleSelectionChanged
    | CueObservationChanged
    | CueIdentityRetired
    | AuthoredCueStale
    | RenderSpaceChanged
    | GeometryInputChanged
    | SubtitleTimingChanged
    | PointerMoved
    | PauseChanged
    | EndOfFileChanged
    | ConnectionChanged
)

_DELTA_DOMAIN: dict[type, FactDomain] = {
    SourceChanged: FactDomain.MEDIA,
    SubtitleSelectionChanged: FactDomain.TRACK,
    CueObservationChanged: FactDomain.CUE,
    CueIdentityRetired: FactDomain.CUE,
    AuthoredCueStale: FactDomain.CUE,
    RenderSpaceChanged: FactDomain.RENDER_SPACE,
    GeometryInputChanged: FactDomain.RENDER_SPACE,
    SubtitleTimingChanged: FactDomain.TIMING,
    PointerMoved: FactDomain.POINTER,
    PauseChanged: FactDomain.PAUSE,
    EndOfFileChanged: FactDomain.EOF,
    ConnectionChanged: FactDomain.CONNECTION,
}

_EMPTY_PROPERTIES: Mapping[str, object] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class PlaybackState:
    """One immutable projection of every observed playback fact."""

    connection: ConnectionFacts = ConnectionFacts()
    media: MediaFacts = MediaFacts()
    track: TrackFacts = TrackFacts()
    cue: CueFacts = CueFacts()
    render_space: RenderSpaceFacts = RenderSpaceFacts()
    timing: TimingFacts = TimingFacts()
    pointer: PointerFacts = PointerFacts()
    paused: bool = False
    properties: Mapping[str, object] = _EMPTY_PROPERTIES

    def value(self, name: str) -> object:
        return self.properties.get(name)

    def observes(self, name: str) -> bool:
        return name in self.properties

    def identity(self) -> ObservedCue:
        """The cue identity implied by the current facts."""
        return ObservedCue(
            self.media.source,
            self.track.track,
            self.track.role,
            self.cue.text,
            self.cue.start,
            self.cue.end,
            self.cue.cue,
        )

    def _with_property(self, name: str, data: object) -> PlaybackState:
        properties = dict(self.properties)
        properties[name] = data
        return replace(self, properties=MappingProxyType(properties))


@dataclass(frozen=True, slots=True)
class Projected:
    state: PlaybackState
    deltas: tuple[PlaybackDelta, ...] = ()


class PlaybackProjection:
    """Pure reducer over ordered mpv observations; performs no I/O and holds no state."""

    def __init__(self, *, legacy_owned: frozenset[FactDomain] = LEGACY_OWNED) -> None:
        unknown = legacy_owned - frozenset(FactDomain)
        if unknown:
            raise ValueError(f"unknown legacy-owned domains: {sorted(unknown)!r}")
        self._legacy_owned = legacy_owned

    @property
    def legacy_owned(self) -> frozenset[FactDomain]:
        return self._legacy_owned

    def _publish(self, deltas: Iterable[PlaybackDelta]) -> tuple[PlaybackDelta, ...]:
        return tuple(
            delta for delta in deltas if _DELTA_DOMAIN[type(delta)] not in self._legacy_owned
        )

    def observe(
        self,
        state: PlaybackState,
        name: str,
        data: object,
        *,
        connection_epoch: int | None = None,
    ) -> Projected:
        """Reduce one ordered property observation. An older epoch cannot change state."""
        if connection_epoch is not None and connection_epoch != state.connection.epoch:
            return Projected(state)
        if not name:
            return Projected(state)
        changed = not state.observes(name) or state.value(name) != data
        state = state._with_property(name, data)
        if not changed:
            return Projected(state)
        state, deltas = self._interpret(state, name, data)
        return Projected(state, self._publish(deltas))

    def seed(self, state: PlaybackState, name: str, data: object) -> PlaybackState:
        """Record an initial observer snapshot. A seed establishes facts, it observes no change."""
        return self.observe(state, name, data).state

    def seed_all(self, state: PlaybackState, values: Mapping[str, object]) -> PlaybackState:
        for name, data in values.items():
            state = self.seed(state, name, data)
        return state

    def _interpret(
        self, state: PlaybackState, name: str, data: object
    ) -> tuple[PlaybackState, tuple[PlaybackDelta, ...]]:
        deltas: list[PlaybackDelta] = []
        state, retired = self._retire_conflict(state, name, data)
        deltas.extend(retired)
        if name in _AUTHORED_STALE_PROPERTIES:
            state = replace(state, cue=replace(state.cue, authored_stale=True))
            deltas.append(AuthoredCueStale())
        state, domain_deltas = self._apply_domain(state, name, data)
        deltas.extend(domain_deltas)
        if name in GEOMETRY_INPUT_PROPERTIES:
            deltas.append(GeometryInputChanged(name))
        return state, tuple(deltas)

    def _apply_domain(
        self, state: PlaybackState, name: str, data: object
    ) -> tuple[PlaybackState, tuple[PlaybackDelta, ...]]:
        if name in TRACK_PROPERTIES:
            track = replace(state.track, track=state.track.track.advance(), sid=data)
            state = replace(state, track=track)
            return state, (SubtitleSelectionChanged(track.track, data, track.role),)
        if name in CUE_PROPERTIES:
            # One delta per changed cue fact, carrying the identity that fact implies. The reducer
            # sees one observation at a time and has no batch, so coalescing a split burst
            # (sub-start, sub-text, sub-end) is the drain's job, by ObservedCue equality.
            state = self._apply_cue(state, name, data)
            return state, (CueObservationChanged(state.identity()),)
        if name in RENDER_SPACE_PROPERTIES:
            render_space = RenderSpaceFacts(state.render_space.render_space.advance())
            state = replace(state, render_space=render_space)
            return state, (RenderSpaceChanged(render_space.render_space, name),)
        if name in TIMING_PROPERTIES:
            field = "delay" if name == "sub-delay" else "position"
            state = replace(state, timing=replace(state.timing, **{field: data}))
            if name != "sub-delay":
                return state, ()
            return state, (SubtitleTimingChanged(data),)
        if name in POINTER_PROPERTIES:
            state = replace(state, pointer=PointerFacts(data))
            return state, (PointerMoved(data),)
        if name in PAUSE_PROPERTIES:
            paused = bool(data)
            state = replace(state, paused=paused)
            return state, (PauseChanged(paused),)
        if name in EOF_PROPERTIES:
            return state, (EndOfFileChanged(bool(data)),)
        return state, ()

    def _apply_cue(self, state: PlaybackState, name: str, data: object) -> PlaybackState:
        if name == "sub-text":
            cue = replace(state.cue, text=str(data or ""), cue=state.cue.cue.advance())
        elif name == "sub-start":
            cue = replace(state.cue, start=data)
        else:
            cue = replace(state.cue, end=data)
        return replace(state, cue=cue)

    def _retire_conflict(
        self, state: PlaybackState, name: str, data: object
    ) -> tuple[PlaybackState, tuple[PlaybackDelta, ...]]:
        reason = _RETIRE_ON_CHANGE.get(name)
        if reason is None or state.cue.installed is None:
            return state, ()
        timed = _TIMED_RETIREMENT.get(name)
        if timed is not None and (data is None or data == getattr(state.cue.installed, timed)):
            return state, ()
        return self.retire(state, reason)

    # --- identity lifecycle, owned by the projection ------------------------------------------

    def install(self, state: PlaybackState, *, start: object, end: object) -> PlaybackState:
        """Record the timing the owning reducer bound to the cue identity it just installed."""
        return replace(state, cue=replace(state.cue, installed=InstalledCue(start, end)))

    def retire(
        self, state: PlaybackState, reason: RetireReason
    ) -> tuple[PlaybackState, tuple[PlaybackDelta, ...]]:
        """Retire the installed identity. Idempotent: a second conflict emits nothing."""
        if state.cue.installed is None:
            return state, ()
        state = replace(state, cue=replace(state.cue, installed=None))
        return state, (CueIdentityRetired(reason),)

    # --- lifecycle observations ---------------------------------------------------------------

    def cue_replaced(self, state: PlaybackState, text: str) -> PlaybackState:
        """Record a cue the owning reducer chose itself rather than observed from mpv.

        A Reader-side cue change — a language or track switch clearing the line — is a statement
        about the very fact ``sub-text`` names. Without it the projection keeps mpv's last text and
        the next changed cue fact reconciles that back over the Reader's, so the cleared cue
        returns: two representations of one fact, which is what invariant 13 forbids.

        The observed value moves with it, so a later mpv observation of the *old* text is a change
        again and republishes. Emitting no delta is deliberate — the caller is the one making the
        decision, and handing it back would only invite it to act on its own write.
        """
        if text == state.cue.text:
            return state
        cue = replace(state.cue, text=text, cue=state.cue.cue.advance())
        return replace(state, cue=cue)._with_property("sub-text", text)

    def source_replaced(self, state: PlaybackState, path: object = None) -> Projected:
        """A new media source is live: bump the source revision and retire the old identity."""
        media = MediaFacts(state.media.source.advance(), path)
        state, retired = self.retire(replace(state, media=media), RetireReason.SOURCE)
        state = replace(state, cue=replace(state.cue, authored_stale=True))
        return Projected(state, self._publish((SourceChanged(media.source), *retired)))

    def role_changed(self, state: PlaybackState, role: str) -> Projected:
        """A same-SID subtitle role change retires the old semantic identity."""
        if role == state.track.role:
            return Projected(state)
        track = replace(state.track, track=state.track.track.advance(), role=role)
        state, retired = self.retire(replace(state, track=track), RetireReason.ROLE)
        return Projected(
            state,
            self._publish((SubtitleSelectionChanged(track.track, track.sid, role), *retired)),
        )

    def connection_changed(self, state: PlaybackState, *, epoch: int, ready: bool) -> Projected:
        """Apply a connection transition. A replacement epoch retires cue identity first."""
        if epoch < state.connection.epoch:
            return Projected(state)
        replaced = epoch > state.connection.epoch
        state = replace(state, connection=ConnectionFacts(epoch, ready))
        deltas: tuple[PlaybackDelta, ...] = ()
        if replaced or not ready:
            state, deltas = self.retire(state, RetireReason.CONNECTION)
        # Observed values survive a replacement on purpose: the gateway replays every observer, and
        # a replayed identical value is not evidence that the cue changed.
        return Projected(state, self._publish((*deltas, ConnectionChanged(epoch, ready))))
