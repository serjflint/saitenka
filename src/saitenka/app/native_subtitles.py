"""Opt-in native-visible subtitle geometry orchestration."""

from __future__ import annotations

import logging
import math
import time
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from saitenka import otel_metrics
from saitenka.app import cue_annotation, subtitle_fonts
from saitenka.app.subtitle_geometry_diagnostics import (
    GeometryCacheReason,
    GeometryOutcome,
    geometry_error_code,
    geometry_failure_reason,
)
from saitenka.app.subtitles import WordBox
from saitenka.subtitles import (
    MAX_ASS_SOURCE_BYTES,
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    authored_ass_rows_at,
    canonical_active_ass_rows,
    prepare_ass_hit_map_frame,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import SupportsFloat

    from saitenka.app.subtitle_geometry_job import SubtitleGeometryWorker
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.app.token_cache import TokenizedCue
    from saitenka.app.tokenize import Token
    from saitenka.subtitles import Cue, CueIndex
    from saitenka.subtitles.geometry import GeometrySnapshot

log = logging.getLogger(__name__)

_FALLBACK_REASONS = frozenset(
    {
        "geometry-provider-failed",
        "geometry-token-identity-invalid",
        "mpv-sub-visibility-rejected",
        "subtitle-render-input-unsupported",
        "subtitle-source-encoding-unsupported",
        "subtitle-source-not-authored-ass",
        "subtitle-source-too-large",
        "subtitle-source-unavailable",
        "subtitle-timing-unavailable",
        "subtitle-token-annotation-invalid",
        "subtitle-ass-full-unavailable",
        "subtitle-ass-full-unsupported",
        "subtitle-geometry-cache-miss",
        "subtitle-observation-pending",
        "subtitle-frame-unsupported",
        "subtitle-font-environment-stale",
    }
)
_PENDING_REASONS = frozenset(
    {
        "subtitle-ass-full-unavailable",
        "subtitle-geometry-cache-miss",
        "subtitle-observation-pending",
        "subtitle-timing-unavailable",
    }
)
# Sources geometry can never accept, however long it waits — wrong format, too large, not UTF-8.
# `subtitle-source-unavailable` is deliberately absent: `set_source(None)` is also the reset every
# track load runs, so switching on it would flap the renderer twice per episode.
_UNSUPPORTED_SOURCE_REASONS = frozenset(
    {
        "subtitle-source-encoding-unsupported",
        "subtitle-source-not-authored-ass",
        "subtitle-source-too-large",
    }
)


class AssFullCapability(StrEnum):
    UNKNOWN = "unknown"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class AnnotationSelection:
    annotations: tuple[TokenAnnotation, ...]
    skipped_whitespace: int
    skipped_tokenizer: int
    skipped_unpaintable: int


def _annotation_for_token(
    token: Token,
    token_index: int,
    line: str,
    offset: int,
    is_skippable: Callable[[Token], bool],
) -> tuple[TokenAnnotation | None, str | None]:
    if not 0 <= token.start < token.end <= len(line):
        raise ValueError("token span exceeds subtitle line")
    surface = line[token.start : token.end]
    if surface != token.surface:
        raise ValueError("token surface does not match subtitle text")
    if not surface.strip():
        return None, "whitespace"
    if is_skippable(token):
        return None, "tokenizer"
    if not any(unicodedata.category(char)[0] not in {"C", "Z"} for char in surface):
        return None, "unpaintable"
    return TokenAnnotation(token_index, offset + token.start, offset + token.end), None


def _short_repr(value: object, *, limit: int = 80) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 3]}..."


def _frame_margins(osd: Mapping[str, object]) -> tuple[int, int, int, int]:
    return cast(
        "tuple[int, int, int, int]",
        tuple(
            int(cast("int | float | str", osd.get(name) or 0)) for name in ("mt", "mb", "ml", "mr")
        ),
    )


def _lookahead_tokenized(
    text: str,
    *,
    normalise: Callable[[str], str],
    coordinator: cue_annotation.CueAnnotationCoordinator | None,
    annotation_key: Callable[[str], cue_annotation.AnnotationWorkKey],
    annotation_inputs: Callable[[str], cue_annotation.AnnotationInputs],
    tokenize: Callable[[str], TokenizedCue],
) -> TokenizedCue:
    """Tokenize an off-screen cue. `coordinator` is `None` when the async path is off — the caller
    owns that config, so the switch does not travel here as a second flag."""
    norm = normalise(text)
    if coordinator is None:
        return tokenize(norm)
    return coordinator.resolve(
        annotation_key(norm),
        annotation_inputs(norm),
        priority=cue_annotation.AnnotationPriority.LOOKAHEAD,
    )


def _unsupported_render_inputs(settings: Mapping[str, object]) -> tuple[str, ...]:
    """Which of mpv's render options put the cue somewhere our layout cannot follow.

    The font options are absent on purpose: `subtitle_fonts.resolve` now reproduces each of them
    instead of refusing it, so `--embeddedfonts`, `--sub-fonts-dir`, `--sub-font-provider` and
    `--sub-font` are inputs to the measuring renderer rather than reasons to give up on a track.
    They are still read, so a change to one still invalidates the cache and is still checked
    against the resolved environment.
    """
    supported = {
        "sub-ass-override": settings["sub-ass-override"] in {False, "no"},
        "sub-ass-scale-with-window": settings["sub-ass-scale-with-window"] is False,
        "sub-scale": settings["sub-scale"] == 1.0,
        "sub-pos": settings["sub-pos"] == 100.0,
        "sub-use-margins": settings["sub-use-margins"] is True,
        "sub-ass-force-margins": isinstance(settings["sub-ass-force-margins"], bool),
        "sub-ass-video-aspect-override": settings["sub-ass-video-aspect-override"]
        in {
            None,
            0,
        },
        "sub-ass-use-video-data": settings["sub-ass-use-video-data"] == "all",
        "sub-ass-vsfilter-aspect-compat": settings["sub-ass-vsfilter-aspect-compat"] is None,
        "sub-ass-style-overrides": settings["sub-ass-style-overrides"] in (None, "", (), [], [""]),
        # `configure_ass` takes its forced-override branch for a CONVERTED track, where it reads
        # these two rather than the `sub-ass-*` variants above — and both default `true`
        # (`options/options.c:344-365`). An unmirrored non-default here is a uniform scale error on
        # every box, which no meter can see because nothing else moves.
        "sub-scale-with-window": settings["sub-scale-with-window"] is True,
        "sub-scale-by-window": settings["sub-scale-by-window"] is True,
        # mpv renders subtitles INTO the video frame with this on, at the video's resolution rather
        # than the OSD surface's, so our overlay and its glyphs land at different scales.
        "blend-subtitles": settings["blend-subtitles"] in {False, "no"},
    }
    return tuple(name for name, accepted in supported.items() if not accepted)


def _validate_frame(frame_size: tuple[int, int], margins: tuple[int, int, int, int]) -> None:
    if any(value < 0 for value in margins):
        raise ValueError(f"osd-margins={_short_repr(margins)}")
    if margins[0] + margins[1] >= frame_size[1] or margins[2] + margins[3] >= frame_size[0]:
        raise ValueError(f"osd-margins={_short_repr(margins)}")


def _pixel_aspect(osd: Mapping[str, object], video: Mapping[str, object]) -> float:
    osd_par = cast("int | float | str", osd.get("par") or 1.0)
    video_par = cast("int | float | str", video.get("par") or 1.0)
    value = float(osd_par) * float(video_par)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            "pixel-aspect=" + _short_repr({"osd": osd.get("par"), "video": video.get("par")})
        )
    return value


def render_inputs_of(
    osd: Mapping[str, object],
    video: Mapping[str, object],
    settings: Mapping[str, object],
    *,
    frame_size: tuple[int, int],
) -> _RenderInputs:
    """Whether mpv's current render configuration lets us key geometry off it, and the frame it
    implies. Raises `ValueError` naming what disqualified it.

    This is the gate on native geometry, and everything it rejects has the same consequence: our
    boxes would be computed against a frame mpv is not drawing into, so hit regions land beside the
    words. `sub-scale`, `sub-pos` and the margin flags all move the text; the font settings change
    which glyphs get laid out; a non-finite or non-positive pixel aspect makes the mapping
    meaningless.

    `frame_size` is the host's OSD surface, passed in rather than re-derived from the `osd` mapping
    here. Both were reads of `osd-dimensions`, and two reads of one property are two values: the
    boxes were laid out against one and drawn onto the other, with the same scale-and-offset error
    on every box and nothing to say so. Margins and pixel aspect still come from the mapping — the
    host does not carry those.
    """

    def _dim(source: Mapping[str, object], key: str, default: int) -> int:
        return int(cast("int | float | str", source.get(key) or default))

    storage_size = (_dim(video, "w", frame_size[0]), _dim(video, "h", frame_size[1]))
    margins = _frame_margins(osd)
    unsupported = _unsupported_render_inputs(settings)
    if unsupported:
        raise ValueError(", ".join(f"{name}={_short_repr(settings[name])}" for name in unsupported))
    _validate_frame(frame_size, margins)
    return _RenderInputs(
        frame_size,
        storage_size,
        _pixel_aspect(osd, video),
        margins,
        cast("bool", settings["sub-ass-force-margins"]),
        tuple(sorted((name, repr(value)) for name, value in settings.items())),
        subtitle_fonts.option_snapshot(settings),
    )


def _subtitle_clock(
    raw_time: SupportsFloat | None, raw_delay: SupportsFloat | None, fallback: float | None
) -> tuple[float, float, float, int]:
    """Video time, `sub-delay`, and the delay-adjusted subtitle time (plus its ms key) from mpv's two
    raw properties. Rejects a clock the geometry can't be keyed off — non-finite or negative — rather
    than let a garbage timestamp reach the cache; `fallback` is `sub-start`, used when mpv has no
    `time-pos` yet."""
    sub_delay = 0.0 if raw_delay is None else float(raw_delay)
    if raw_time is None:
        if fallback is None:
            raise ValueError("subtitle clock is unavailable")
        subtitle_time = fallback
        video_time = subtitle_time + sub_delay
    else:
        video_time = float(raw_time)
        subtitle_time = video_time - sub_delay
    if not all(math.isfinite(value) for value in (video_time, sub_delay, subtitle_time)):
        raise ValueError("subtitle clock must be finite")
    if subtitle_time < 0:
        raise ValueError("subtitle clock must be non-negative")
    return video_time, sub_delay, subtitle_time, round(subtitle_time * 1_000)


@dataclass(frozen=True, slots=True)
class NativeSubtitleStatus:
    enabled: bool
    source: str | None
    fallback_reason: str | None
    geometry_ready: bool
    ass_full_capability: AssFullCapability
    eligible_tokens: int
    skipped_tokens: int
    owner: str
    outcome: GeometryOutcome | None
    last_transition: str | None
    last_recovery: str | None
    source_epoch: int


@dataclass(frozen=True, slots=True)
class _RenderInputs:
    frame_size: tuple[int, int]
    storage_size: tuple[int, int]
    pixel_aspect: float
    margins: tuple[int, int, int, int]
    use_margins: bool
    profile: tuple[tuple[str, str], ...]
    #: Just the font options, so a change to one can be told from a change to the render space.
    font_options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _CueInputs:
    start_ms: int
    end_ms: int
    timestamp_ms: int
    text: str
    active_rows: str
    frame_size: tuple[int, int]
    storage_size: tuple[int, int]
    pixel_aspect: float
    margins: tuple[int, int, int, int]
    use_margins: bool
    render_profile: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _ScheduleInputs:
    path: Path
    source: bytes
    track_id: SubtitleTrackId
    generation: int
    render: _RenderInputs
    cue: _CueInputs
    key: str
    observation_key: str | None


@dataclass(frozen=True, slots=True)
class GeometryPorts:
    """What the geometry owner asks the session to do, and the pipeline it defers to.

    Bound once, at construction, rather than reached for per call: the geometry owner is a
    session-lived collaborator, so a port that could change between calls would mean a different
    session, not a different frame. That is what separates it from `SubtitleTarget`, whose members
    are live facts and must be snapshotted per operation.
    """

    pipeline: SubtitleModeCoordinator
    degrade: Callable[[], None]
    clear_interaction: Callable[[], None]
    use_native: Callable[[], bool]
    ownership_undecided: Callable[[], bool]
    redraw: Callable[[], None]
    #: Hand the hit boxes (and, when the cue is installed, its origin) to whoever presents them.
    #: The geometry owner IS the publisher of these — unlike a renderer, which returns them so a
    #: superseded cue cannot write over a live one. Here the generation fence is what orders them.
    publish: Callable[[list[WordBox], tuple[int, int] | None], None]
    #: Tokenize a cue that is not on screen, for the lookahead. Session-lived, like the rest here;
    #: the *active* tokenizer is not, because a profile switch replaces it.
    tokenize_lookahead: Callable[[str], TokenizedCue]


@dataclass(frozen=True, slots=True)
class GeometryObservation:
    """The live facts one geometry decision is made from, snapshotted per operation.

    Cut by contract: mpv-read (`prop`), presentation (`osd`), cue identity and nav (`text`,
    `index`, `normalise`, `nav_index`, `cue_hint`, `cue_revision`), and the rendered cue
    (`tokens`, `lines`). `is_skippable` is the dictionary's, and it travels here rather than in
    the ports because a profile switch replaces the tokenizer mid-session.

    Eleven members. That is a feature value, the size the sidebar's is — and the count is what
    says this module is one feature and `tooltip`, at ninety-two, is not yet.
    """

    prop: Callable[[str], Any]
    #: The OSD surface mpv composites onto, and therefore the frame the boxes are laid out in.
    #: One value for both: it used to be the host's for drawing and a second read of
    #: `osd-dimensions` for the layout, which is two values whenever they disagree.
    osd: tuple[int, int]
    text: str
    tokens: list[Token]
    lines: list[list[Token]]
    index: CueIndex | None
    normalise: Callable[[str], str]
    nav_index: int
    cue_hint: Cue | None
    cue_revision: int
    is_skippable: Callable[[Token], bool]


class NativeSubtitleGeometry:
    def __init__(
        self, worker: SubtitleGeometryWorker, ports: GeometryPorts, *, lookahead: int = 2
    ) -> None:
        if lookahead < 0:
            raise ValueError("subtitle geometry lookahead must be non-negative")
        self.worker = worker
        self._ports = ports
        self.lookahead = lookahead
        self.source_path: Path | None = None
        self._source_bytes: bytes | None = None
        self.fallback_reason: str | None = "subtitle-source-unavailable"
        self._last_snapshot: object | None = None
        self.ass_full_capability = AssFullCapability.UNKNOWN
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self._last_decision: tuple[GeometryOutcome, str] | None = None
        self._owner = "unknown"
        self._submitted_at: tuple[int, float] | None = None
        self._pending_key: tuple[int, str] | None = None
        self._published_key: str | None = None
        self._source_epoch = 0
        self._last_transition: str | None = None
        self._last_recovery: str | None = None
        self._last_render_inputs: _RenderInputs | None = None
        self._failure_diagnostic: tuple[str, str | None] | None = None
        self._fonts = subtitle_fonts.FontEnvironment()

    def _skipped_tokens(self) -> int:
        return (
            self._last_selection.skipped_whitespace
            + self._last_selection.skipped_tokenizer
            + self._last_selection.skipped_unpaintable
        )

    def sync_pixel_owner(self, renderer) -> None:
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", self._owner)
        if owner != self._owner:
            self._transition_owner(owner, self._last_decision)

    def _record_decision_metrics(
        self, outcome: GeometryOutcome, reason: str, active_events: int
    ) -> None:
        if otel_metrics.subtitle_geometry_decisions is not None:
            otel_metrics.subtitle_geometry_decisions.add(1, {"outcome": outcome, "reason": reason})
        if otel_metrics.subtitle_geometry_active_events is not None:
            otel_metrics.subtitle_geometry_active_events.record(active_events)
        if otel_metrics.subtitle_geometry_eligible_tokens is not None:
            otel_metrics.subtitle_geometry_eligible_tokens.record(self._eligible_tokens)
        if otel_metrics.subtitle_geometry_skipped_tokens is not None:
            otel_metrics.subtitle_geometry_skipped_tokens.record(self._skipped_tokens())

    def _trace_decision(
        self,
        outcome: GeometryOutcome,
        reason: str,
        target_owner: str,
        error_code: str | None,
        active_events: int,
    ) -> None:
        with otel_metrics.traced("subtitle_geometry_decision") as span:
            span.set("outcome", outcome)
            span.set("reason", reason)
            span.set("generation", self.worker.generation)
            span.set("source_epoch", self._source_epoch)
            span.set("source_class", "external-ass" if self.source_path is not None else "none")
            span.set("ass_full_capability", self.ass_full_capability)
            span.set("active_events", active_events)
            span.set("eligible_tokens", self._eligible_tokens)
            span.set("skipped_whitespace", self._last_selection.skipped_whitespace)
            span.set("skipped_tokenizer", self._last_selection.skipped_tokenizer)
            span.set("skipped_unpaintable", self._last_selection.skipped_unpaintable)
            if error_code is not None:
                span.set("error_code", error_code)
            if self._last_render_inputs is not None:
                render = self._last_render_inputs
                span.set("frame_width", render.frame_size[0])
                span.set("frame_height", render.frame_size[1])
                span.set("storage_width", render.storage_size[0])
                span.set("storage_height", render.storage_size[1])
                span.set("pixel_aspect", render.pixel_aspect)
                span.set("margins", repr(render.margins))
            if target_owner != self._owner:
                span.set("owner_transition", f"{self._owner}_to_{target_owner}")

    def _transition_owner(
        self,
        target_owner: str,
        previous: tuple[GeometryOutcome, str] | None,
    ) -> None:
        if target_owner == self._owner:
            return
        transition = f"{self._owner}_to_{target_owner}"
        self._last_transition = transition
        if otel_metrics.subtitle_geometry_owner_transitions is not None:
            otel_metrics.subtitle_geometry_owner_transitions.add(1, {"transition": transition})
        recovery_reason = (
            previous[1]
            if target_owner == "native"
            and previous is not None
            and previous[0] != GeometryOutcome.EMPTY
            else None
        )
        if recovery_reason is not None and otel_metrics.subtitle_geometry_recoveries is not None:
            otel_metrics.subtitle_geometry_recoveries.add(1, {"reason": recovery_reason})
        if recovery_reason is not None:
            self._last_recovery = recovery_reason
        self._owner = target_owner

    def _set_decision(
        self,
        outcome: GeometryOutcome,
        reason: str,
        *,
        error_code: str | None = None,
        active_events: int = 0,
        owner: str | None = None,
    ) -> bool:
        decision = (outcome, reason)
        target_owner = owner or self._owner
        if decision == self._last_decision and target_owner == self._owner:
            return False
        previous = self._last_decision
        self._last_decision = decision
        self._record_decision_metrics(outcome, reason, active_events)
        self._trace_decision(outcome, reason, target_owner, error_code, active_events)
        self._transition_owner(target_owner, previous)
        return True

    def _set_fallback(
        self,
        reason: str | None,
        *,
        error_code: str | None = None,
        log_detail: str | None = None,
    ) -> None:
        if reason is None:
            self.fallback_reason = None
            self._set_decision(GeometryOutcome.READY, "ready")
            return
        if reason not in _FALLBACK_REASONS:
            reason = "geometry-provider-failed"
        outcome = self._fallback_outcome(reason)
        if self._preserves_latched_failure(outcome):
            return
        self.fallback_reason = reason
        if not self._set_decision(outcome, reason, error_code=error_code):
            return
        self._emit_fallback(outcome, reason, error_code=error_code, log_detail=log_detail)

    @staticmethod
    def _fallback_outcome(reason: str) -> GeometryOutcome:
        if reason in _PENDING_REASONS:
            return GeometryOutcome.PENDING
        if reason.startswith("geometry-") or reason == "mpv-sub-visibility-rejected":
            return GeometryOutcome.FAILED
        return GeometryOutcome.UNSUPPORTED

    def _preserves_latched_failure(self, outcome: GeometryOutcome) -> bool:
        return bool(
            outcome == GeometryOutcome.PENDING
            and self._last_decision is not None
            and self._last_decision[0] == GeometryOutcome.FAILED
        )

    def _emit_fallback(
        self,
        outcome: GeometryOutcome,
        reason: str,
        *,
        error_code: str | None,
        log_detail: str | None,
    ) -> None:
        diagnostic_key = (reason, error_code)
        if outcome == GeometryOutcome.FAILED and diagnostic_key == self._failure_diagnostic:
            return
        if outcome == GeometryOutcome.FAILED:
            self._failure_diagnostic = diagnostic_key
        if (
            outcome == GeometryOutcome.FAILED
            and otel_metrics.subtitle_geometry_failures is not None
        ):
            otel_metrics.subtitle_geometry_failures.add(1, {"reason": reason})
        level = logging.WARNING if outcome == GeometryOutcome.FAILED else logging.INFO
        diagnostic = error_code or log_detail
        log.log(
            level,
            "native subtitle interaction unavailable: %s%s",
            reason,
            f" detail={diagnostic}" if diagnostic else "",
        )

    def _set_ready(self, *, active_events: int = 0) -> None:
        self._failure_diagnostic = None
        self.fallback_reason = None
        self._set_decision(
            GeometryOutcome.READY,
            "ready",
            active_events=active_events,
            owner="native",
        )

    def _set_pending_with_current_geometry(self, *, active_events: int) -> None:
        self.fallback_reason = "subtitle-observation-pending"
        self._set_decision(
            GeometryOutcome.PENDING,
            "subtitle-observation-pending",
            active_events=active_events,
            owner=self._owner,
        )

    def _degrade_geometry(self, reason: str, *, error_code: str | None = None) -> None:
        self._ports.degrade()
        renderer = self._ports.pipeline.renderer
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", self._owner)
        self._set_fallback(reason, error_code=error_code)
        if owner != self._owner:
            self._transition_owner(owner, self._last_decision)

    @property
    def status(self) -> NativeSubtitleStatus:
        return NativeSubtitleStatus(
            enabled=True,
            source=str(self.source_path) if self.source_path is not None else None,
            fallback_reason=self.fallback_reason,
            geometry_ready=self._last_snapshot is not None,
            ass_full_capability=self.ass_full_capability,
            eligible_tokens=self._eligible_tokens,
            skipped_tokens=self._skipped_tokens(),
            owner=self._owner,
            outcome=self._last_decision[0] if self._last_decision is not None else None,
            last_transition=self._last_transition,
            last_recovery=self._last_recovery,
            source_epoch=self._source_epoch,
        )

    @property
    def source_unsupported(self) -> bool:
        """Whether this track can never produce geometry, so legacy should own its pixels.

        A stable property of the *source*, not a geometry outcome — which is what lets it select a
        renderer without breaking the rule that geometry availability may not. It moves only when
        the source does, so the switch happens once per track and never between cues.
        """
        return self.fallback_reason in _UNSUPPORTED_SOURCE_REASONS

    def observe_ass_full_reply(self, reply: Mapping[str, object]) -> None:
        error = reply.get("error")
        if error == "success":
            self.ass_full_capability = AssFullCapability.SUPPORTED
        elif error in {"property unavailable", "property is unavailable"}:
            self.ass_full_capability = AssFullCapability.UNKNOWN
        elif error in {"property not found", "unknown property"}:
            self.ass_full_capability = AssFullCapability.UNSUPPORTED

    def set_fonts(self, environment: subtitle_fonts.FontEnvironment) -> None:
        """Adopt the font set mpv holds for this track, resolved by whoever loaded it.

        Not read from mpv here: it costs an ffmpeg dump and two `expand-path` round trips, and gate
        6 keeps that off the interaction loop. Arriving late is safe — it invalidates, so the next
        cue re-measures; arriving never leaves the environment empty and the check below refuses
        the frame rather than laying it out in whatever the system happens to offer.
        """
        if environment == self._fonts:
            return
        self._fonts = environment
        log.info(
            "subtitle font sources: %s (%d attachment(s), fonts-dir %s)",
            "+".join(environment.sources) or "none",
            len(environment.attachments),
            environment.setup.fonts_dir or "-",
        )
        if otel_metrics.subtitle_geometry_font_sources is not None:
            otel_metrics.subtitle_geometry_font_sources.add(
                1, {"sources": "+".join(environment.sources) or "none"}
            )
        self.invalidate(cause=GeometryCacheReason.RENDER_INPUT_CHANGED)

    def _fonts_are_current(self, render: _RenderInputs) -> bool:
        """Whether the resolved environment still describes what mpv is doing.

        mpv treats every one of these options as `UPDATE_SUB_HARD` — it rebuilds its own subtitle
        decoder — so a change between track loads means our faces and its faces have diverged.
        """
        return self._fonts.options == render.font_options

    def set_source(self, path: Path | None, *, live: bool = False) -> None:
        """Point at a new authored source.

        `live` says a running session is switching, so whatever the old source left on screen has
        to be retired. It used to be spelled by passing the host and testing it for `None` — a
        parameter whose only use was its own presence, which reads as a dependency and is a flag.
        """
        if live:
            self._consume_failure()
        self._source_epoch += 1
        self.worker.invalidate(cause=GeometryCacheReason.SOURCE_CHANGED)
        self.source_path = None
        self._source_bytes = None
        self._last_snapshot = None
        self._submitted_at = None
        self._pending_key = None
        self._published_key = None
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self._last_render_inputs = None
        self._failure_diagnostic = None
        if live:
            self._ports.clear_interaction()
            self._ports.degrade()
        if path is None:
            self._set_fallback("subtitle-source-unavailable")
        elif path.suffix.casefold() != ".ass":
            self._set_fallback("subtitle-source-not-authored-ass")
        else:
            try:
                with path.open("rb") as source_file:
                    source = source_file.read(MAX_ASS_SOURCE_BYTES + 1)
            except OSError:
                self._set_fallback("subtitle-source-unavailable")
            else:
                if len(source) > MAX_ASS_SOURCE_BYTES:
                    self._set_fallback("subtitle-source-too-large")
                else:
                    self._source_bytes = source
                    self.source_path = path
                    self.fallback_reason = None

    def invalidate(
        self,
        *,
        live: bool = False,
        cause: GeometryCacheReason = GeometryCacheReason.RENDER_INPUT_CHANGED,
    ) -> None:
        """Drop the cached geometry. `live` retires the interaction it was backing — see
        `set_source`."""
        if live:
            self._consume_failure()
        self._last_snapshot = None
        self._submitted_at = None
        self._pending_key = None
        self._published_key = None
        self.worker.invalidate(cause=cause)
        if live:
            self._ports.clear_interaction()

    def refresh(self, seen: GeometryObservation) -> None:
        identity = self._observation_key(seen)
        snapshot = self._ports.pipeline.current
        if (
            identity is not None
            and identity == self._published_key
            and snapshot is not None
            and snapshot is self._last_snapshot
        ):
            active_events = len(snapshot.frame_id.active_event_ids)
            if seen.prop("sub-start") is None or seen.prop("sub-end") is None:
                self._set_pending_with_current_geometry(active_events=active_events)
            else:
                self._set_ready(active_events=active_events)
            return
        self.invalidate(live=True)
        if seen.text.strip():
            self.schedule(seen)

    @staticmethod
    def record_clock_change(prop) -> None:
        with otel_metrics.traced("subtitle_geometry_clock") as span:
            try:
                raw_start = prop("sub-start")
                video_time, sub_delay, subtitle_time, _timestamp_ms = _subtitle_clock(
                    prop("time-pos"),
                    prop("sub-delay"),
                    None if raw_start is None else float(raw_start),
                )
            except (TypeError, ValueError):
                span.set("outcome", "invalid")
                log.info("mpv sub-delay changed; subtitle clock is temporarily unavailable")
                return
            span.set("outcome", "ready")
            span.set("video_time_ms", round(video_time * 1_000))
            span.set("sub_delay_ms", round(sub_delay * 1_000))
            span.set("subtitle_time_ms", round(subtitle_time * 1_000))
            log.info(
                "mpv sub-delay changed: video=%+.3fs delay=%+.3fs subtitle=%+.3fs",
                video_time,
                sub_delay,
                subtitle_time,
            )

    def mark_empty(self) -> None:
        self._last_snapshot = None
        self._pending_key = None
        self._published_key = None
        self._submitted_at = None
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self.fallback_reason = None
        self._set_decision(GeometryOutcome.EMPTY, "empty", owner=self._owner)

    @staticmethod
    def _annotations(
        text: str,
        lines,
        tokens,
        is_skippable: Callable[[Token], bool],
    ) -> AnnotationSelection:
        source_lines = text.replace("\\N", "\n").replace("\r", "").split("\n")
        annotated: list[TokenAnnotation] = []
        skipped: list[str] = []
        token_index = 0
        offset = 0
        line_index = 0
        for line_tokens in lines:
            while line_index < len(source_lines) and not source_lines[line_index].strip():
                offset += len(source_lines[line_index]) + 1
                line_index += 1
            if line_index >= len(source_lines):
                raise ValueError("token lines exceed subtitle semantic text")
            line = source_lines[line_index]
            for token in line_tokens:
                annotation, reason = _annotation_for_token(
                    token, token_index, line, offset, is_skippable
                )
                if annotation is not None:
                    annotated.append(annotation)
                if reason is not None:
                    skipped.append(reason)
                token_index += 1
            offset += len(line) + 1
            line_index += 1
        if token_index != len(tokens):
            raise ValueError("token lines do not cover the flattened subtitle tokens")
        return AnnotationSelection(
            tuple(annotated),
            skipped.count("whitespace"),
            skipped.count("tokenizer"),
            skipped.count("unpaintable"),
        )

    @staticmethod
    def _key(path: Path, cue: _CueInputs) -> str:
        return repr(
            (
                str(path.resolve()),
                cue.text,
                canonical_active_ass_rows(cue.active_rows),
                cue.frame_size,
                cue.storage_size,
                cue.pixel_aspect,
                cue.margins,
                cue.use_margins,
                cue.render_profile,
            )
        )

    def _observation_key(self, seen: GeometryObservation) -> str | None:
        path = self.source_path
        active_rows = seen.prop("sub-text/ass-full")
        if path is None or not isinstance(active_rows, str) or not active_rows.strip():
            return None
        try:
            render = self._render_inputs(seen.prop, seen.osd)
            cue = _CueInputs(
                0,
                1,
                0,
                seen.text,
                active_rows,
                render.frame_size,
                render.storage_size,
                render.pixel_aspect,
                render.margins,
                render.use_margins,
                render.profile,
            )
            token_identity = tuple((token.surface, token.start, token.end) for token in seen.tokens)
            return repr((self._key(path, cue), token_identity))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build(
        source: bytes,
        track_id: SubtitleTrackId,
        generation: int,
        cue: _CueInputs,
        annotations: tuple[TokenAnnotation, ...],
        fonts: subtitle_fonts.FontEnvironment,
    ) -> GeometryRequest:
        with otel_metrics.traced("subtitle_geometry_prepare") as span:
            span.set("observed_rows", len(cue.active_rows.splitlines()))
            span.set("eligible_tokens", len(annotations))
            span.set("timestamp_ms", cue.timestamp_ms)
            started = time.perf_counter_ns()
            try:
                prepared = prepare_ass_hit_map_frame(
                    source,
                    track_id,
                    active_rows=cue.active_rows,
                    text=cue.text,
                    tokens=annotations,
                )
            except Exception as error:
                span.set("outcome", "failed")
                span.set("error_code", geometry_error_code(error))
                raise
            prepare_ms = (time.perf_counter_ns() - started) / 1_000_000
            span.set("outcome", "ready")
            span.set("matched_events", len(prepared.events))
            span.set("prepare_ms", prepare_ms)
            if otel_metrics.subtitle_geometry_prepare_ms is not None:
                otel_metrics.subtitle_geometry_prepare_ms.record(prepare_ms)
        return GeometryRequest(
            generation,
            track_id,
            prepared.frame_id,
            cue.timestamp_ms,
            cue.frame_size,
            cue.storage_size,
            prepared.ass,
            pixel_aspect=cue.pixel_aspect,
            margins=cue.margins,
            use_margins=cue.use_margins,
            render_profile=cue.render_profile,
            palette=prepared.palette,
            reserved_rgb=prepared.reserved_rgb,
            attachments=fonts.attachments,
            font_setup=fonts.setup,
        )

    @staticmethod
    def _render_inputs(prop: Callable[[str], Any], osd: tuple[int, int]) -> _RenderInputs:
        """Read the mpv properties native geometry depends on, then decide.

        Takes the property reader and the OSD surface rather than the host: those two are all it
        ever wanted, and a shim that takes the whole `Reader` cannot be driven by the session
        runtime. The surface is the host's, and is the frame — see `render_inputs_of`.
        """
        return render_inputs_of(
            prop("osd-dimensions") or {},
            prop("video-out-params") or {},
            {
                name: prop(f"options/{name}")
                for name in (
                    "sub-ass-override",
                    "sub-ass-scale-with-window",
                    "sub-scale",
                    "sub-pos",
                    "sub-use-margins",
                    "sub-ass-force-margins",
                    "sub-ass-video-aspect-override",
                    "sub-ass-use-video-data",
                    "sub-ass-vsfilter-aspect-compat",
                    "sub-ass-style-overrides",
                    "sub-scale-with-window",
                    "sub-scale-by-window",
                    "blend-subtitles",
                    *subtitle_fonts.FONT_OPTIONS,
                )
            },
            frame_size=osd,
        )

    def _prefetch(
        self,
        seen: GeometryObservation,
        path: Path,
        track_id: SubtitleTrackId,
        generation: int,
        render_inputs: _RenderInputs,
        after_ms: int,
    ) -> None:
        index = seen.index
        if index is None or self.lookahead == 0:
            return
        source = self._source_bytes
        if source is None:
            return
        queued_keys: set[str] = set()
        for boundary in index.boundaries_after(after_ms / 1_000):
            timestamp_ms = round(boundary * 1_000) + 1
            try:
                active_rows, text = authored_ass_rows_at(source, track_id, timestamp_ms)
            except (TypeError, ValueError):
                continue
            if not active_rows.strip() or not text.strip():
                continue
            inputs = _CueInputs(
                timestamp_ms,
                timestamp_ms + 1,
                timestamp_ms,
                text,
                active_rows,
                render_inputs.frame_size,
                render_inputs.storage_size,
                render_inputs.pixel_aspect,
                render_inputs.margins,
                render_inputs.use_margins,
                render_inputs.profile,
            )
            key = self._key(path, inputs)
            if key in queued_keys:
                continue
            queued_keys.add(key)

            def build(
                inputs: _CueInputs = inputs,
                fonts: subtitle_fonts.FontEnvironment = self._fonts,
            ) -> GeometryRequest:
                tokenized = self._ports.tokenize_lookahead(inputs.text)
                selection = self._annotations(
                    inputs.text,
                    tokenized.lines,
                    tokenized.tokens,
                    seen.is_skippable,
                )
                if not selection.annotations:
                    raise ValueError("prefetched frame has no interaction-eligible tokens")
                return self._build(
                    source,
                    track_id,
                    generation,
                    inputs,
                    selection.annotations,
                    fonts,
                )

            self.worker.prefetch(key, generation, build)
            if len(queued_keys) >= self.lookahead:
                break

    def schedule(self, seen: GeometryObservation) -> bool:
        try:
            return self._schedule(seen)
        except Exception as error:  # noqa: BLE001  # optional provider must fail interaction closed
            self.worker.mark_not_ready()
            reason, code = geometry_failure_reason(error)
            self._degrade_geometry(
                reason,
                error_code=code,
            )
            return False

    def _active_observation(
        self,
        seen: GeometryObservation,
        source: bytes,
        track_id: SubtitleTrackId,
        start: float,
        end: float,
        active_rows: object,
    ) -> tuple[float, float, int, str] | None:
        hint = seen.cue_hint
        if hint is not None:
            timestamp_ms = round(hint.start * 1_000) + 1
            rows, semantic_text = authored_ass_rows_at(source, track_id, timestamp_ms)
            if semantic_text != seen.normalise(seen.text):
                self._degrade_geometry("subtitle-observation-pending")
                return None
            return hint.start, hint.end, timestamp_ms, rows
        if not isinstance(active_rows, str) or not active_rows.strip():
            self._degrade_geometry("subtitle-ass-full-unavailable")
            return None
        try:
            _video_time, _sub_delay, subtitle_time, timestamp_ms = _subtitle_clock(
                seen.prop("time-pos"), seen.prop("sub-delay"), start
            )
        except (TypeError, ValueError):
            self._degrade_geometry("subtitle-timing-unavailable")
            return None
        if seen.index is not None:
            position = seen.index.locate(
                text=seen.text,
                sub_start=start,
                time_pos=subtitle_time,
                preferred=seen.nav_index,
            )
            if position >= 0:
                indexed = seen.index.cues[position]
                if indexed.text == seen.text:
                    start, end = indexed.start, indexed.end
        return start, end, timestamp_ms, active_rows

    def _trace_unscheduled(self, reason: str, cue_revision: int) -> None:
        """Name a geometry schedule that never started. Not a degrade: the inputs are not assembled
        yet, so pixels and ownership are untouched — only the silence is the bug. The revision is
        what ties a dropped schedule back to the observation that armed the refresh."""
        with otel_metrics.traced("subtitle_geometry_inputs") as span:
            span.set("reason", reason)
            span.set("cue_revision", cue_revision)
        log.debug("geometry schedule skipped: %s (cue revision %d)", reason, cue_revision)

    def _unassembled_input(self, *, cue_text: str, has_tokens: bool) -> str | None:
        """Which of the four schedule preconditions is not met yet, if any. These are "inputs not
        assembled", not a failure — name which and do not degrade."""
        if self.source_path is None:
            return "no-source-path"
        if self._source_bytes is None:
            return "no-source-bytes"
        if not cue_text.strip():
            return "no-cue-text"
        if not has_tokens:
            return "no-tokens"
        return None

    def _resolve_schedule_inputs(self, seen: GeometryObservation) -> _ScheduleInputs | None:
        path = self.source_path
        source = self._source_bytes
        unassembled = self._unassembled_input(cue_text=seen.text, has_tokens=bool(seen.tokens))
        if unassembled is not None or path is None or source is None:
            # Every other exit below records a reason; this one used to return a bare None, so a
            # geometry schedule that never ran was invisible in the logs and in telemetry.
            self._trace_unscheduled(unassembled or "no-source-path", seen.cue_revision)
            return None
        if self.ass_full_capability == AssFullCapability.UNSUPPORTED:
            self._degrade_geometry("subtitle-ass-full-unsupported")
            return None
        try:
            render = self._render_inputs(seen.prop, seen.osd)
        except (TypeError, ValueError) as error:
            self.worker.mark_not_ready()
            self._set_fallback("subtitle-render-input-unsupported", log_detail=str(error))
            self._ports.degrade()
            return None
        self._last_render_inputs = render
        if not self._fonts_are_current(render):
            self.worker.mark_not_ready()
            self._set_fallback(
                "subtitle-font-environment-stale",
                log_detail=f"{self._fonts.options} != {render.font_options}",
            )
            self._ports.degrade()
            return None
        active_rows = seen.prop("sub-text/ass-full")
        if isinstance(active_rows, str):
            self.ass_full_capability = AssFullCapability.SUPPORTED
        start = seen.prop("sub-start")
        end = seen.prop("sub-end")
        if start is None or end is None:
            self._degrade_geometry("subtitle-observation-pending")
            return None
        generation = self._ports.pipeline.generation
        track_id = SubtitleTrackId(f"sid:{seen.prop('sid')}:{path.resolve()}")
        active = self._active_observation(
            seen, source, track_id, float(start), float(end), active_rows
        )
        if active is None:
            return None
        start, end, timestamp_ms, rows = active
        cue = _CueInputs(
            round(start * 1_000),
            round(end * 1_000),
            timestamp_ms,
            seen.text,
            rows,
            render.frame_size,
            render.storage_size,
            render.pixel_aspect,
            render.margins,
            render.use_margins,
            render.profile,
        )
        return _ScheduleInputs(
            path,
            source,
            track_id,
            generation,
            render,
            cue,
            self._key(path, cue),
            self._observation_key(seen),
        )

    def _publish_cached(self, seen: GeometryObservation, inputs: _ScheduleInputs) -> bool:
        cached = self.worker.publish_prefetched(inputs.key, inputs.generation)
        if cached is None:
            return False
        if inputs.observation_key is not None:
            self._pending_key = (inputs.generation, inputs.observation_key)
        self._eligible_tokens = len(cached.palette)
        self.worker.mark_presented(cached)
        if not self._ports.use_native():
            if self._ports.ownership_undecided():
                return True  # the assertion's terminal re-drives the refresh
            self._degrade_geometry("mpv-sub-visibility-rejected")
            return True
        self._set_ready(active_events=len(cached.frame_id.active_event_ids))
        self._prefetch(
            seen,
            inputs.path,
            inputs.track_id,
            inputs.generation,
            inputs.render,
            inputs.cue.timestamp_ms,
        )
        return True

    def _schedule(self, seen: GeometryObservation) -> bool:
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        inputs = self._resolve_schedule_inputs(seen)
        if inputs is None:
            return False
        with otel_metrics.traced("subtitle_geometry_cache") as span:
            cache_hit = self._publish_cached(seen, inputs)
            stats = self.worker.stats
            span.set("outcome", "hit" if cache_hit else "miss")
            if not cache_hit:
                span.set("reason", self.worker.prefetch_miss_reason(inputs.key))
            span.set("cache_hits", stats.cache_hits)
            span.set("prefetch_dropped", stats.prefetch_dropped)
            span.set("prefetch_cache_entries", stats.prefetch_cache_entries)
        if cache_hit:
            # A hit resolves inside this call, so there is no terminal to wait for: publish here, or
            # a cached cue would sit unapplied until some later miss happened to land.
            self.apply(seen)
            return True
        try:
            selection = self._annotations(
                seen.text,
                seen.lines,
                seen.tokens,
                seen.is_skippable,
            )
        except ValueError:
            self.worker.mark_not_ready()
            self._degrade_geometry("subtitle-token-annotation-invalid")
            return False
        self._last_selection = selection
        self._eligible_tokens = len(selection.annotations)
        if not selection.annotations:
            self._ports.publish([], None)
            self.worker.mark_not_ready()
            if self._ports.use_native():
                self._published_key = inputs.observation_key
                self._set_ready()
                return True
            if self._ports.ownership_undecided():
                return False  # the assertion's terminal re-drives the refresh
            self._degrade_geometry("mpv-sub-visibility-rejected")
            return False

        def build(fonts: subtitle_fonts.FontEnvironment = self._fonts) -> GeometryRequest:
            return self._build(
                inputs.source,
                inputs.track_id,
                inputs.generation,
                inputs.cue,
                selection.annotations,
                fonts,
            )

        def settled() -> None:
            """The lane terminal is what publishes the result — nothing polls for it."""
            self.apply(seen)

        # Everything the result will be judged against is established BEFORE the work is queued.
        # These used to run after, which reads fine while a completion is guaranteed to be later —
        # and silently wipes the result the moment one is not, because `_degrade_geometry` clears
        # the very boxes the completion just published.
        if inputs.observation_key is not None:
            self._pending_key = (inputs.generation, inputs.observation_key)
        self._submitted_at = (inputs.generation, time.perf_counter())
        self.worker.mark_not_ready()
        self._degrade_geometry("subtitle-geometry-cache-miss")
        accepted = self.worker.submit_job(
            inputs.generation, build, work_key=inputs.key, on_settled=settled
        )
        if accepted:
            self._prefetch(
                seen,
                inputs.path,
                inputs.track_id,
                inputs.generation,
                inputs.render,
                inputs.cue.timestamp_ms,
            )
        return accepted

    def apply(self, seen: GeometryObservation) -> bool:
        try:
            return self._apply(seen)
        except Exception as error:  # noqa: BLE001  # optional provider must fail interaction closed
            reason, code = geometry_failure_reason(error)
            self._degrade_geometry(
                reason,
                error_code=code,
            )
            return False

    def _consume_failure(self) -> None:
        error = self._ports.pipeline.consume_error()
        if error is None:
            return
        reason, code = geometry_failure_reason(error)
        if error.startswith("subtitle-source-"):
            reason = error
        self._degrade_geometry(
            reason,
            error_code=code if not reason.startswith("subtitle-source-") else None,
        )

    @staticmethod
    def _snapshot_identities_are_valid(snapshot: GeometrySnapshot, token_count: int) -> bool:
        """Whether a worker's geometry can be painted against the cue currently tokenized.

        Each guard rejects a different way the snapshot and the cue can disagree: a repeated token
        index would paint one box twice; an event the frame no longer lists is geometry for a cue
        that has gone; and an index past ``token_count`` is a box for a token this cue does not
        have. Any of them paints a hit region over the wrong word.

        There was a fourth, rejecting a repeated ``(event, token)`` pair. It could never fire:
        equal pairs have equal indices, so the index guard already rejected them.
        """
        identities = [(item.event_id, item.token_index) for item in snapshot.tokens]
        indices = [token_index for _event_id, token_index in identities]
        active_events = set(snapshot.frame_id.active_event_ids)
        return bool(
            len(indices) == len(set(indices))
            and all(event_id in active_events for event_id, _token_index in identities)
            and all(0 <= index < token_count for index in indices)
        )

    def _install_snapshot(self, snapshot: GeometrySnapshot) -> list[WordBox]:
        """Retain ``snapshot`` and return its boxes; the caller owns writing them onto the host.

        The origin is the caller's to set to (0, 0) alongside: these boxes are already in frame
        coordinates, so a leftover subtitle origin would offset every hit region by it.
        """
        self._last_snapshot = snapshot
        if self._pending_key is not None and self._pending_key[0] == snapshot.generation:
            self._published_key = self._pending_key[1]
        self._pending_key = None
        return [
            WordBox(
                item.token_index,
                item.bounds.x,
                item.bounds.y,
                item.bounds.width,
                item.bounds.height,
            )
            for item in snapshot.tokens
        ]

    def _record_ready_latency(self, generation: int) -> None:
        if (
            self._submitted_at is not None
            and self._submitted_at[0] == generation
            and otel_metrics.subtitle_geometry_ready_ms is not None
        ):
            otel_metrics.subtitle_geometry_ready_ms.record(
                (time.perf_counter() - self._submitted_at[1]) * 1_000
            )
        self._submitted_at = None

    def _apply(self, seen: GeometryObservation) -> bool:
        snapshot = self._ports.pipeline.current
        if snapshot is None:
            self._consume_failure()
            return False
        if snapshot is self._last_snapshot:
            return False
        if not self._snapshot_identities_are_valid(snapshot, len(seen.tokens)):
            self._degrade_geometry("geometry-token-identity-invalid")
            return False
        self._ports.publish(self._install_snapshot(snapshot), (0, 0))
        self._record_ready_latency(snapshot.generation)
        if not self._ports.use_native():
            if self._ports.ownership_undecided():
                return False  # the assertion's terminal re-drives the refresh
            self._set_fallback("mpv-sub-visibility-rejected")
            return False
        self._set_ready(active_events=len(snapshot.frame_id.active_event_ids))
        self._ports.redraw()
        return True

    def close(self) -> None:
        self.worker.close()
