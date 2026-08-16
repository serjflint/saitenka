"""Opt-in native-visible subtitle geometry orchestration."""

from __future__ import annotations

import logging
import math
import time
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from saitenka import otel_metrics
from saitenka.app.subtitle_geometry_diagnostics import (
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

    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_pipeline import SubtitleGeometryWorker
    from saitenka.app.tokenize import Token
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
        "subtitle-observation-pending",
        "subtitle-frame-unsupported",
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


def _unsupported_render_inputs(settings: Mapping[str, object]) -> tuple[str, ...]:
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
        "sub-font-provider": settings["sub-font-provider"] == "auto",
        "embeddedfonts": settings["embeddedfonts"] is False,
        "sub-fonts-dir": settings["sub-fonts-dir"] in {None, ""},
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


def _subtitle_clock(reader: Reader, fallback: float | None) -> tuple[float, float, float, int]:
    raw_time = reader._prop("time-pos")
    raw_delay = reader._prop("sub-delay")
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


class NativeSubtitleGeometry:
    def __init__(self, worker: SubtitleGeometryWorker, *, lookahead: int = 2) -> None:
        if lookahead < 0:
            raise ValueError("subtitle geometry lookahead must be non-negative")
        self.worker = worker
        self.lookahead = lookahead
        self.source_path: Path | None = None
        self._source_bytes: bytes | None = None
        self.fallback_reason: str | None = "subtitle-source-unavailable"
        self._last_snapshot: object | None = None
        self.ass_full_capability = AssFullCapability.UNKNOWN
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self._last_decision: tuple[GeometryOutcome, str] | None = None
        self._owner = "legacy"
        self._submitted_at: tuple[int, float] | None = None
        self._pending_key: tuple[int, str] | None = None
        self._published_key: str | None = None
        self._source_epoch = 0
        self._last_transition: str | None = None
        self._last_recovery: str | None = None
        self._last_render_inputs: _RenderInputs | None = None

    def _skipped_tokens(self) -> int:
        return (
            self._last_selection.skipped_whitespace
            + self._last_selection.skipped_tokenizer
            + self._last_selection.skipped_unpaintable
        )

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
        if decision == self._last_decision:
            return False
        previous = self._last_decision
        self._last_decision = decision
        target_owner = owner or ("native" if outcome == GeometryOutcome.READY else "legacy")
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
        self.fallback_reason = reason
        if reason is None:
            self._set_decision(GeometryOutcome.READY, "ready")
            return
        if reason not in _FALLBACK_REASONS:
            reason = "geometry-provider-failed"
        outcome = (
            GeometryOutcome.PENDING
            if reason
            in {
                "subtitle-ass-full-unavailable",
                "subtitle-observation-pending",
                "subtitle-timing-unavailable",
            }
            else GeometryOutcome.FAILED
            if reason.startswith("geometry-") or reason == "mpv-sub-visibility-rejected"
            else GeometryOutcome.UNSUPPORTED
        )
        if not self._set_decision(outcome, reason, error_code=error_code):
            return
        if otel_metrics.subtitle_geometry_fallbacks is not None:
            otel_metrics.subtitle_geometry_fallbacks.add(1, {"reason": reason})
        level = logging.WARNING if outcome == GeometryOutcome.FAILED else logging.INFO
        diagnostic = error_code or log_detail
        log.log(level, "native subtitle geometry uses legacy renderer: %s%s", reason,
                f" detail={diagnostic}" if diagnostic else "")  # fmt: skip

    def _set_ready(self, *, active_events: int = 0) -> None:
        self.fallback_reason = None
        self._set_decision(GeometryOutcome.READY, "ready", active_events=active_events)

    def _set_pending_with_current_geometry(self, *, active_events: int) -> None:
        self.fallback_reason = "subtitle-observation-pending"
        self._set_decision(
            GeometryOutcome.PENDING,
            "subtitle-observation-pending",
            active_events=active_events,
            owner=self._owner,
        )

    def _fallback_to_legacy(
        self, reader: Reader, reason: str, *, error_code: str | None = None
    ) -> None:
        self._set_fallback(reason, error_code=error_code)
        reader._use_legacy_subtitle_renderer()

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

    def observe_ass_full_reply(self, reply: Mapping[str, object]) -> None:
        error = reply.get("error")
        if error == "success":
            self.ass_full_capability = AssFullCapability.SUPPORTED
        elif error in {"property unavailable", "property is unavailable"}:
            self.ass_full_capability = AssFullCapability.UNKNOWN
        elif error in {"property not found", "unknown property"}:
            self.ass_full_capability = AssFullCapability.UNSUPPORTED

    def set_source(self, path: Path | None, *, reader: Reader | None = None) -> None:
        self._source_epoch += 1
        self.worker.invalidate()
        self.source_path = None
        self._source_bytes = None
        self._last_snapshot = None
        self._submitted_at = None
        self._pending_key = None
        self._published_key = None
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self._last_render_inputs = None
        if reader is not None:
            reader._clear_native_interaction()
            reader._use_legacy_subtitle_renderer(draw=False)
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

    def invalidate(self, reader: Reader | None = None) -> None:
        self._last_snapshot = None
        self._submitted_at = None
        self._pending_key = None
        self._published_key = None
        self.worker.invalidate()
        if reader is not None:
            reader._clear_native_interaction()

    def refresh(self, reader: Reader) -> None:
        identity = self._observation_key(reader)
        snapshot = reader.subtitle_pipeline.current
        if (
            identity is not None
            and identity == self._published_key
            and snapshot is not None
            and snapshot is self._last_snapshot
        ):
            active_events = len(snapshot.frame_id.active_event_ids)
            if reader._prop("sub-start") is None or reader._prop("sub-end") is None:
                self._set_pending_with_current_geometry(active_events=active_events)
            else:
                self._set_ready(active_events=active_events)
            return
        self.invalidate(reader)
        if reader.sub_text.strip():
            self.schedule(reader)

    @staticmethod
    def record_clock_change(reader: Reader) -> None:
        with otel_metrics.traced("subtitle_geometry_clock") as span:
            try:
                raw_start = reader._prop("sub-start")
                video_time, sub_delay, subtitle_time, _timestamp_ms = _subtitle_clock(
                    reader, None if raw_start is None else float(raw_start)
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

    def _observation_key(self, reader: Reader) -> str | None:
        path = self.source_path
        active_rows = reader._prop("sub-text/ass-full")
        if path is None or not isinstance(active_rows, str) or not active_rows.strip():
            return None
        try:
            render = self._render_inputs(reader)
            cue = _CueInputs(
                0,
                1,
                0,
                reader.sub_text,
                active_rows,
                render.frame_size,
                render.storage_size,
                render.pixel_aspect,
                render.margins,
                render.use_margins,
                render.profile,
            )
            token_identity = tuple(
                (token.surface, token.start, token.end) for token in reader.tokens
            )
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
        )

    @staticmethod
    def _render_inputs(reader: Reader) -> _RenderInputs:
        video = reader._prop("video-out-params") or {}
        osd = reader._prop("osd-dimensions") or {}
        frame_size = (int(osd.get("w") or reader.osd[0]), int(osd.get("h") or reader.osd[1]))
        storage_size = (int(video.get("w") or frame_size[0]), int(video.get("h") or frame_size[1]))
        margins = _frame_margins(osd)
        settings = {
            "sub-ass-override": reader._prop("options/sub-ass-override"),
            "sub-ass-scale-with-window": reader._prop("options/sub-ass-scale-with-window"),
            "sub-scale": reader._prop("options/sub-scale"),
            "sub-pos": reader._prop("options/sub-pos"),
            "sub-use-margins": reader._prop("options/sub-use-margins"),
            "sub-ass-force-margins": reader._prop("options/sub-ass-force-margins"),
            "sub-ass-video-aspect-override": reader._prop("options/sub-ass-video-aspect-override"),
            "sub-ass-use-video-data": reader._prop("options/sub-ass-use-video-data"),
            "sub-ass-vsfilter-aspect-compat": reader._prop(
                "options/sub-ass-vsfilter-aspect-compat"
            ),
            "sub-ass-style-overrides": reader._prop("options/sub-ass-style-overrides"),
            "sub-font-provider": reader._prop("options/sub-font-provider"),
            "embeddedfonts": reader._prop("options/embeddedfonts"),
            "sub-fonts-dir": reader._prop("options/sub-fonts-dir"),
        }
        unsupported = _unsupported_render_inputs(settings)
        if unsupported:
            raise ValueError(
                ", ".join(f"{name}={_short_repr(settings[name])}" for name in unsupported)
            )
        _validate_frame(frame_size, margins)
        return _RenderInputs(
            frame_size,
            storage_size,
            _pixel_aspect(osd, video),
            margins,
            cast("bool", settings["sub-ass-force-margins"]),
            tuple(sorted((name, repr(value)) for name, value in settings.items())),
        )

    def _prefetch(
        self,
        reader: Reader,
        path: Path,
        track_id: SubtitleTrackId,
        generation: int,
        render_inputs: _RenderInputs,
    ) -> None:
        index = reader._sub_index
        if index is None or self.lookahead == 0:
            return
        current = index.locate(text=reader.sub_text, preferred=reader._nav_idx)
        if current < 0:
            return
        source = self._source_bytes
        if source is None:
            return
        for indexed in index.cues[current + 1 : current + 1 + self.lookahead]:
            timestamp_ms = round(indexed.start * 1_000) + 1
            try:
                active_rows, text = authored_ass_rows_at(source, track_id, timestamp_ms)
            except (TypeError, ValueError):
                continue
            inputs = _CueInputs(
                round(indexed.start * 1_000),
                round(indexed.end * 1_000),
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

            def build(inputs: _CueInputs = inputs) -> GeometryRequest:
                tokenized = reader._tokenize_cue(reader._cue_norm(inputs.text))
                selection = self._annotations(
                    inputs.text,
                    tokenized.lines,
                    tokenized.tokens,
                    reader.tokenizer.is_skippable,
                )
                if not selection.annotations:
                    raise ValueError("prefetched frame has no interaction-eligible tokens")
                return self._build(
                    source,
                    track_id,
                    generation,
                    inputs,
                    selection.annotations,
                )

            self.worker.prefetch(self._key(path, inputs), generation, build)

    def schedule(self, reader: Reader) -> bool:
        try:
            return self._schedule(reader)
        except Exception as error:  # noqa: BLE001  # optional provider must restore legacy rendering
            self.worker.mark_not_ready()
            reason, code = geometry_failure_reason(error)
            self._fallback_to_legacy(
                reader,
                reason,
                error_code=code,
            )
            return False

    def _active_observation(
        self,
        reader: Reader,
        source: bytes,
        track_id: SubtitleTrackId,
        start: float,
        end: float,
        active_rows: object,
    ) -> tuple[float, float, int, str] | None:
        hint = reader._geometry_cue_hint
        if hint is not None:
            timestamp_ms = round(hint.start * 1_000) + 1
            rows, semantic_text = authored_ass_rows_at(source, track_id, timestamp_ms)
            if semantic_text != reader._cue_norm(reader.sub_text):
                self._fallback_to_legacy(reader, "subtitle-observation-pending")
                return None
            return hint.start, hint.end, timestamp_ms, rows
        if not isinstance(active_rows, str) or not active_rows.strip():
            self._fallback_to_legacy(reader, "subtitle-ass-full-unavailable")
            return None
        try:
            _video_time, _sub_delay, subtitle_time, timestamp_ms = _subtitle_clock(reader, start)
        except (TypeError, ValueError):
            self._fallback_to_legacy(reader, "subtitle-timing-unavailable")
            return None
        if reader._sub_index is not None:
            position = reader._sub_index.locate(
                text=reader.sub_text,
                sub_start=start,
                time_pos=subtitle_time,
                preferred=reader._nav_idx,
            )
            if position >= 0:
                indexed = reader._sub_index.cues[position]
                if indexed.text == reader.sub_text:
                    start, end = indexed.start, indexed.end
        return start, end, timestamp_ms, active_rows

    def _resolve_schedule_inputs(self, reader: Reader) -> _ScheduleInputs | None:
        path = self.source_path
        source = self._source_bytes
        if path is None or source is None or not reader.sub_text.strip() or not reader.tokens:
            return None
        if self.ass_full_capability == AssFullCapability.UNSUPPORTED:
            self._fallback_to_legacy(reader, "subtitle-ass-full-unsupported")
            return None
        active_rows = reader._prop("sub-text/ass-full")
        if isinstance(active_rows, str):
            self.ass_full_capability = AssFullCapability.SUPPORTED
        start = reader._prop("sub-start")
        end = reader._prop("sub-end")
        if start is None or end is None:
            self._fallback_to_legacy(reader, "subtitle-observation-pending")
            return None
        try:
            render = self._render_inputs(reader)
        except (TypeError, ValueError) as error:
            self.worker.mark_not_ready()
            self._set_fallback("subtitle-render-input-unsupported", log_detail=str(error))
            reader._use_legacy_subtitle_renderer()
            return None
        self._last_render_inputs = render
        generation = reader.subtitle_pipeline.generation
        track_id = SubtitleTrackId(f"sid:{reader._prop('sid')}:{path.resolve()}")
        active = self._active_observation(
            reader, source, track_id, float(start), float(end), active_rows
        )
        if active is None:
            return None
        start, end, timestamp_ms, rows = active
        cue = _CueInputs(
            round(start * 1_000),
            round(end * 1_000),
            timestamp_ms,
            reader.sub_text,
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
            self._observation_key(reader),
        )

    def _publish_cached(self, reader: Reader, inputs: _ScheduleInputs) -> bool:
        cached = self.worker.publish_prefetched(inputs.key, inputs.generation)
        if cached is None:
            return False
        if inputs.observation_key is not None:
            self._pending_key = (inputs.generation, inputs.observation_key)
        self._eligible_tokens = len(cached.palette)
        self.worker.mark_presented(cached)
        self._set_ready(active_events=len(cached.frame_id.active_event_ids))
        self._prefetch(
            reader,
            inputs.path,
            inputs.track_id,
            inputs.generation,
            inputs.render,
        )
        return True

    def _schedule(self, reader: Reader) -> bool:
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        inputs = self._resolve_schedule_inputs(reader)
        if inputs is None:
            return False
        if self._publish_cached(reader, inputs):
            return True
        try:
            selection = self._annotations(
                reader.sub_text,
                reader.lines,
                reader.tokens,
                reader.tokenizer.is_skippable,
            )
        except ValueError:
            self.worker.mark_not_ready()
            self._fallback_to_legacy(reader, "subtitle-token-annotation-invalid")
            return False
        self._last_selection = selection
        self._eligible_tokens = len(selection.annotations)
        if not selection.annotations:
            reader.boxes = []
            self.worker.mark_not_ready()
            if reader._use_native_subtitle_renderer():
                self._published_key = inputs.observation_key
                self._set_ready()
                return True
            self._fallback_to_legacy(reader, "mpv-sub-visibility-rejected")
            return False

        def build() -> GeometryRequest:
            return self._build(
                inputs.source,
                inputs.track_id,
                inputs.generation,
                inputs.cue,
                selection.annotations,
            )

        accepted = self.worker.submit_job(inputs.generation, build)
        if accepted:
            if inputs.observation_key is not None:
                self._pending_key = (inputs.generation, inputs.observation_key)
            self._submitted_at = (inputs.generation, time.perf_counter())
            self.worker.mark_not_ready()
            if self._last_decision is None or self._last_decision[0] != GeometryOutcome.FAILED:
                self._fallback_to_legacy(reader, "subtitle-observation-pending")
            else:
                reader._use_legacy_subtitle_renderer()
            self._prefetch(
                reader,
                inputs.path,
                inputs.track_id,
                inputs.generation,
                inputs.render,
            )
        return accepted

    def apply(self, reader: Reader) -> bool:
        try:
            return self._apply(reader)
        except Exception as error:  # noqa: BLE001  # optional provider must restore legacy rendering
            reason, code = geometry_failure_reason(error)
            self._fallback_to_legacy(
                reader,
                reason,
                error_code=code,
            )
            return False

    def _consume_failure(self, reader: Reader) -> None:
        error = reader.subtitle_pipeline.consume_error()
        if error is None:
            return
        reason, code = geometry_failure_reason(error)
        if error.startswith("subtitle-source-"):
            reason = error
        self._fallback_to_legacy(
            reader,
            reason,
            error_code=code if not reason.startswith("subtitle-source-") else None,
        )

    @staticmethod
    def _snapshot_identities_are_valid(reader: Reader, snapshot: GeometrySnapshot) -> bool:
        identities = [(item.event_id, item.token_index) for item in snapshot.tokens]
        indices = [token_index for _event_id, token_index in identities]
        active_events = set(snapshot.frame_id.active_event_ids)
        return bool(
            len(identities) == len(set(identities))
            and len(indices) == len(set(indices))
            and all(event_id in active_events for event_id, _token_index in identities)
            and all(0 <= index < len(reader.tokens) for index in indices)
        )

    def _install_snapshot(self, reader: Reader, snapshot: GeometrySnapshot) -> None:
        reader.boxes = [
            WordBox(
                item.token_index,
                item.bounds.x,
                item.bounds.y,
                item.bounds.width,
                item.bounds.height,
            )
            for item in snapshot.tokens
        ]
        reader.sub_origin = (0, 0)
        self._last_snapshot = snapshot
        if self._pending_key is not None and self._pending_key[0] == snapshot.generation:
            self._published_key = self._pending_key[1]
        self._pending_key = None

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

    def _apply(self, reader: Reader) -> bool:
        snapshot = reader.subtitle_pipeline.current
        if snapshot is None:
            self._consume_failure(reader)
            return False
        if snapshot is self._last_snapshot:
            return False
        if not self._snapshot_identities_are_valid(reader, snapshot):
            self._fallback_to_legacy(reader, "geometry-token-identity-invalid")
            return False
        self._install_snapshot(reader, snapshot)
        self._record_ready_latency(snapshot.generation)
        if not reader._use_native_subtitle_renderer():
            self._set_fallback("mpv-sub-visibility-rejected")
            reader._use_legacy_subtitle_renderer()
            return False
        self._set_ready(active_events=len(snapshot.frame_id.active_event_ids))
        reader._draw_subtitle()
        return True

    def close(self) -> None:
        self.worker.close()
