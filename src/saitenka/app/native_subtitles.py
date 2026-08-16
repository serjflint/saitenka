"""Opt-in native-visible subtitle geometry orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from saitenka import otel_metrics
from saitenka.app.subtitles import WordBox
from saitenka.subtitles import (
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    prepare_ass_hit_map,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_pipeline import SubtitleGeometryWorker

log = logging.getLogger(__name__)

_FALLBACK_REASONS = frozenset(
    {
        "geometry-provider-failed",
        "geometry-token-identity-invalid",
        "mpv-sub-visibility-rejected",
        "subtitle-render-input-unsupported",
        "subtitle-source-encoding-unsupported",
        "subtitle-source-not-authored-ass",
        "subtitle-source-unavailable",
        "subtitle-timing-unavailable",
        "subtitle-token-annotation-invalid",
    }
)


def _short_repr(value: object, *, limit: int = 80) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 3]}..."


def _osd_margins(osd: Mapping[str, object]) -> tuple[int, int, int, int]:
    return cast(
        "tuple[int, int, int, int]",
        tuple(
            int(cast("int | float | str", osd.get(name) or 0)) for name in ("ml", "mr", "mt", "mb")
        ),
    )


def _unsupported_render_inputs(
    frame_size: tuple[int, int],
    display_size: tuple[object, object],
    margins: tuple[int, int, int, int],
    settings: Mapping[str, object],
) -> tuple[str, ...]:
    supported = {
        "display-size": frame_size == display_size,
        "osd-margins": not any(margins),
        "sub-ass-override": settings["sub-ass-override"] in {False, "no"},
        "sub-ass-scale-with-window": settings["sub-ass-scale-with-window"] is False,
        "sub-scale": settings["sub-scale"] == 1.0,
        "sub-pos": settings["sub-pos"] == 100.0,
        "sub-use-margins": settings["sub-use-margins"] is True,
        "sub-ass-use-video-data": settings["sub-ass-use-video-data"] == "all",
        "sub-ass-vsfilter-aspect-compat": settings["sub-ass-vsfilter-aspect-compat"] is None,
        "sub-ass-style-overrides": settings["sub-ass-style-overrides"] in (None, "", (), [], [""]),
        "sub-font-provider": settings["sub-font-provider"] == "auto",
        "embeddedfonts": settings["embeddedfonts"] is False,
        "sub-fonts-dir": settings["sub-fonts-dir"] in {None, ""},
    }
    return tuple(name for name, accepted in supported.items() if not accepted)


@dataclass(frozen=True, slots=True)
class NativeSubtitleStatus:
    enabled: bool
    source: str | None
    fallback_reason: str | None
    geometry_ready: bool


@dataclass(frozen=True, slots=True)
class _CueInputs:
    start_ms: int
    end_ms: int
    text: str
    frame_size: tuple[int, int]
    storage_size: tuple[int, int]
    pixel_aspect: float
    render_profile: tuple[tuple[str, str], ...]


class NativeSubtitleGeometry:
    def __init__(self, worker: SubtitleGeometryWorker, *, lookahead: int = 2) -> None:
        if lookahead < 0:
            raise ValueError("subtitle geometry lookahead must be non-negative")
        self.worker = worker
        self.lookahead = lookahead
        self.source_path: Path | None = None
        self.fallback_reason: str | None = "subtitle-source-unavailable"
        self._last_snapshot: object | None = None

    def _set_fallback(self, reason: str | None, *, detail: str | None = None) -> None:
        if reason == self.fallback_reason:
            return
        self.fallback_reason = reason
        if reason is None:
            return
        telemetry_reason = reason if reason in _FALLBACK_REASONS else "other"
        if otel_metrics.subtitle_geometry_fallbacks is not None:
            otel_metrics.subtitle_geometry_fallbacks.add(1, {"reason": telemetry_reason})
        with otel_metrics.traced("subtitle_geometry_fallback", reason=telemetry_reason):
            pass
        suffix = f" ({detail})" if detail else ""
        level = logging.WARNING if reason == "geometry-provider-failed" else logging.INFO
        log.log(
            level,
            "native subtitle geometry unavailable; using Saitenka renderer: %s%s",
            reason,
            suffix,
        )

    def _fallback_to_legacy(
        self, reader: Reader, reason: str, *, detail: str | None = None
    ) -> None:
        self._set_fallback(reason, detail=detail)
        reader._use_legacy_subtitle_renderer()

    @property
    def status(self) -> NativeSubtitleStatus:
        return NativeSubtitleStatus(
            enabled=True,
            source=str(self.source_path) if self.source_path is not None else None,
            fallback_reason=self.fallback_reason,
            geometry_ready=self._last_snapshot is not None,
        )

    def set_source(self, path: Path | None, *, reader: Reader | None = None) -> None:
        self.worker.invalidate()
        self.source_path = None
        self._last_snapshot = None
        if reader is not None:
            reader._clear_native_interaction()
            reader._use_legacy_subtitle_renderer()
        if path is None:
            self._set_fallback("subtitle-source-unavailable")
        elif path.suffix.casefold() != ".ass":
            self._set_fallback("subtitle-source-not-authored-ass")
        else:
            self.source_path = path
            self._set_fallback(None)

    def invalidate(self, reader: Reader | None = None) -> None:
        self._last_snapshot = None
        self.worker.invalidate()
        if reader is not None:
            reader._clear_native_interaction()

    def refresh(self, reader: Reader) -> None:
        self.invalidate(reader)
        if reader.sub_text.strip():
            self.schedule(reader)

    @staticmethod
    def _annotations(text: str, lines, tokens) -> tuple[TokenAnnotation, ...]:
        source_lines = text.replace("\\N", "\n").replace("\r", "").split("\n")
        annotated: list[TokenAnnotation] = []
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
                if not 0 <= token.start < token.end <= len(line):
                    raise ValueError("token span exceeds subtitle line")
                if line[token.start : token.end] != token.surface:
                    raise ValueError("token surface does not match subtitle text")
                annotated.append(
                    TokenAnnotation(token_index, offset + token.start, offset + token.end)
                )
                token_index += 1
            offset += len(line) + 1
            line_index += 1
        if token_index != len(tokens):
            raise ValueError("token lines do not cover the flattened subtitle tokens")
        return tuple(annotated)

    @staticmethod
    def _key(path: Path, cue: _CueInputs) -> str:
        return repr(
            (
                str(path.resolve()),
                cue.start_ms,
                cue.end_ms,
                cue.text,
                cue.frame_size,
                cue.storage_size,
                cue.pixel_aspect,
                cue.render_profile,
            )
        )

    @staticmethod
    def _build(
        path: Path,
        track_id: SubtitleTrackId,
        generation: int,
        cue: _CueInputs,
        annotations: tuple[TokenAnnotation, ...],
    ) -> GeometryRequest:
        prepared = prepare_ass_hit_map(
            path.read_bytes(),
            track_id,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            text=cue.text,
            tokens=annotations,
        )
        return GeometryRequest(
            generation,
            track_id,
            prepared.event.decoded.source.identity,
            cue.start_ms + 1,
            cue.frame_size,
            cue.storage_size,
            prepared.ass,
            pixel_aspect=cue.pixel_aspect,
            render_profile=cue.render_profile,
            palette=prepared.palette,
            reserved_rgb=prepared.reserved_rgb,
        )

    @staticmethod
    def _render_inputs(
        reader: Reader,
    ) -> tuple[tuple[int, int], tuple[int, int], float, tuple[tuple[str, str], ...]]:
        video = reader._prop("video-out-params") or {}
        osd = reader._prop("osd-dimensions") or {}
        frame_size = (int(osd.get("w") or reader.osd[0]), int(osd.get("h") or reader.osd[1]))
        storage_size = (int(video.get("w") or frame_size[0]), int(video.get("h") or frame_size[1]))
        display_size = (
            int(video.get("dw") or frame_size[0]),
            int(video.get("dh") or frame_size[1]),
        )
        margins = _osd_margins(osd)
        settings = {
            "sub-ass-override": reader._prop("options/sub-ass-override"),
            "sub-ass-scale-with-window": reader._prop("options/sub-ass-scale-with-window"),
            "sub-scale": reader._prop("options/sub-scale"),
            "sub-pos": reader._prop("options/sub-pos"),
            "sub-use-margins": reader._prop("options/sub-use-margins"),
            "sub-ass-use-video-data": reader._prop("options/sub-ass-use-video-data"),
            "sub-ass-vsfilter-aspect-compat": reader._prop(
                "options/sub-ass-vsfilter-aspect-compat"
            ),
            "sub-ass-style-overrides": reader._prop("options/sub-ass-style-overrides"),
            "sub-font-provider": reader._prop("options/sub-font-provider"),
            "embeddedfonts": reader._prop("options/embeddedfonts"),
            "sub-fonts-dir": reader._prop("options/sub-fonts-dir"),
        }
        unsupported = _unsupported_render_inputs(frame_size, display_size, margins, settings)
        if unsupported:
            observed = {
                "display-size": {"frame": frame_size, "video": display_size},
                "osd-margins": margins,
                **settings,
            }
            raise ValueError(
                ", ".join(f"{name}={_short_repr(observed[name])}" for name in unsupported)
            )
        profile = tuple(sorted((name, repr(value)) for name, value in settings.items()))
        return frame_size, storage_size, float(video.get("par") or 1.0), profile

    def _prefetch(
        self,
        reader: Reader,
        path: Path,
        track_id: SubtitleTrackId,
        generation: int,
        render_inputs: tuple[tuple[int, int], tuple[int, int], float, tuple[tuple[str, str], ...]],
    ) -> None:
        index = reader._sub_index
        if index is None or self.lookahead == 0:
            return
        current = index.locate(text=reader.sub_text, preferred=reader._nav_idx)
        if current < 0:
            return
        frame_size, storage_size, pixel_aspect, render_profile = render_inputs
        for cue in index.cues[current + 1 : current + 1 + self.lookahead]:
            inputs = _CueInputs(
                round(cue.start * 1_000),
                round(cue.end * 1_000),
                cue.text,
                frame_size,
                storage_size,
                pixel_aspect,
                render_profile,
            )

            def build(inputs: _CueInputs = inputs) -> GeometryRequest:
                tokenized = reader._tokenize_cue(reader._cue_norm(inputs.text))
                annotations = self._annotations(inputs.text, tokenized.lines, tokenized.tokens)
                return self._build(path, track_id, generation, inputs, annotations)

            self.worker.prefetch(self._key(path, inputs), generation, build)

    def schedule(self, reader: Reader) -> bool:
        try:
            return self._schedule(reader)
        except Exception as error:  # noqa: BLE001  # optional provider must restore legacy rendering
            self.worker.mark_not_ready()
            self._fallback_to_legacy(
                reader,
                "geometry-provider-failed",
                detail=f"{type(error).__name__}: {error}",
            )
            return False

    def _schedule(self, reader: Reader) -> bool:
        path = self.source_path
        if path is None or not reader.sub_text.strip() or not reader.tokens:
            return False
        start = reader._prop("sub-start")
        end = reader._prop("sub-end")
        if start is None or end is None:
            self._fallback_to_legacy(reader, "subtitle-timing-unavailable")
            return False
        try:
            frame_size, storage_size, pixel_aspect, render_profile = self._render_inputs(reader)
        except (TypeError, ValueError) as error:
            self.worker.mark_not_ready()
            self._fallback_to_legacy(reader, "subtitle-render-input-unsupported", detail=str(error))
            return False
        generation = reader.subtitle_pipeline.generation
        track_id = SubtitleTrackId(f"sid:{reader._prop('sid')}:{path.resolve()}")
        hint = reader._geometry_cue_hint
        if hint is not None:
            start, end = hint.start, hint.end
        elif reader._sub_index is not None:
            position = reader._sub_index.locate(
                text=reader.sub_text,
                sub_start=float(start),
                time_pos=float(reader._prop("time-pos") or start),
                preferred=reader._nav_idx,
            )
            if position >= 0:
                indexed = reader._sub_index.cues[position]
                if indexed.text == reader.sub_text:
                    start, end = indexed.start, indexed.end
        cue = _CueInputs(
            round(float(start) * 1_000),
            round(float(end) * 1_000),
            reader.sub_text,
            frame_size,
            storage_size,
            pixel_aspect,
            render_profile,
        )
        key = self._key(path, cue)
        if cached := self.worker.publish_prefetched(key, generation):
            self.worker.mark_presented(cached)
            self._set_fallback(None)
            self._prefetch(
                reader,
                path,
                track_id,
                generation,
                (frame_size, storage_size, pixel_aspect, render_profile),
            )
            return True
        try:
            annotations = self._annotations(reader.sub_text, reader.lines, reader.tokens)
        except ValueError:
            self.worker.mark_not_ready()
            self._fallback_to_legacy(reader, "subtitle-token-annotation-invalid")
            return False

        def build() -> GeometryRequest:
            return self._build(path, track_id, generation, cue, annotations)

        accepted = self.worker.submit_job(generation, build)
        if accepted:
            self.worker.mark_not_ready()
            self._prefetch(
                reader,
                path,
                track_id,
                generation,
                (frame_size, storage_size, pixel_aspect, render_profile),
            )
        return accepted

    def apply(self, reader: Reader) -> bool:
        try:
            return self._apply(reader)
        except Exception as error:  # noqa: BLE001  # optional provider must restore legacy rendering
            self._fallback_to_legacy(
                reader,
                "geometry-provider-failed",
                detail=f"{type(error).__name__}: {error}",
            )
            return False

    def _apply(self, reader: Reader) -> bool:
        snapshot = reader.subtitle_pipeline.current
        if snapshot is None:
            if (error := reader.subtitle_pipeline.consume_error()) is not None:
                reason = (
                    error if error.startswith("subtitle-source-") else "geometry-provider-failed"
                )
                self._fallback_to_legacy(
                    reader,
                    reason,
                    detail=error if reason == "geometry-provider-failed" else None,
                )
            return False
        if snapshot is self._last_snapshot:
            return False
        tokens = snapshot.tokens
        indices = [item.token_index for item in tokens]
        if len(indices) != len(set(indices)) or any(
            not 0 <= index < len(reader.tokens) for index in indices
        ):
            self._fallback_to_legacy(reader, "geometry-token-identity-invalid")
            return False
        reader.boxes = [
            WordBox(
                item.token_index,
                item.bounds.x,
                item.bounds.y,
                item.bounds.width,
                item.bounds.height,
            )
            for item in tokens
        ]
        reader.sub_origin = (0, 0)
        self._last_snapshot = snapshot
        if not reader._use_native_subtitle_renderer():
            self._set_fallback("mpv-sub-visibility-rejected")
            reader._use_legacy_subtitle_renderer()
            return False
        self._set_fallback(None)
        reader._draw_subtitle()
        return True

    def close(self) -> None:
        self.worker.close()
