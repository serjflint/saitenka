"""Opt-in native-visible subtitle geometry orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.subtitles import WordBox
from saitenka.subtitles import (
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    prepare_ass_hit_map,
)

if TYPE_CHECKING:
    from pathlib import Path

    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_pipeline import SubtitleGeometryWorker


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


class NativeSubtitleGeometry:
    def __init__(self, worker: SubtitleGeometryWorker, *, lookahead: int = 2) -> None:
        if lookahead < 0:
            raise ValueError("subtitle geometry lookahead must be non-negative")
        self.worker = worker
        self.lookahead = lookahead
        self.source_path: Path | None = None
        self.fallback_reason: str | None = "subtitle-source-unavailable"
        self._last_snapshot: object | None = None

    @property
    def status(self) -> NativeSubtitleStatus:
        return NativeSubtitleStatus(
            enabled=True,
            source=str(self.source_path) if self.source_path is not None else None,
            fallback_reason=self.fallback_reason,
            geometry_ready=self._last_snapshot is not None,
        )

    def set_source(self, path: Path | None) -> None:
        self.worker.invalidate_cache()
        self.source_path = None
        self._last_snapshot = None
        if path is None:
            self.fallback_reason = "subtitle-source-unavailable"
        elif path.suffix.casefold() != ".ass":
            self.fallback_reason = "subtitle-source-not-authored-ass"
        else:
            self.source_path = path
            self.fallback_reason = None

    def invalidate(self) -> None:
        self._last_snapshot = None
        self.worker.invalidate_cache()

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
            palette=prepared.palette,
            reserved_rgb=prepared.reserved_rgb,
        )

    @staticmethod
    def _render_inputs(reader: Reader) -> tuple[tuple[int, int], tuple[int, int], float]:
        video = reader._prop("video-out-params") or {}
        frame_size = (int(video.get("dw") or reader.osd[0]), int(video.get("dh") or reader.osd[1]))
        storage_size = (int(video.get("w") or frame_size[0]), int(video.get("h") or frame_size[1]))
        return frame_size, storage_size, float(video.get("par") or 1.0)

    def _prefetch(
        self,
        reader: Reader,
        path: Path,
        track_id: SubtitleTrackId,
        generation: int,
        render_inputs: tuple[tuple[int, int], tuple[int, int], float],
    ) -> None:
        index = reader._sub_index
        if index is None or self.lookahead == 0:
            return
        current = index.locate(text=reader.sub_text, preferred=reader._nav_idx)
        if current < 0:
            return
        frame_size, storage_size, pixel_aspect = render_inputs
        for cue in index.cues[current + 1 : current + 1 + self.lookahead]:
            inputs = _CueInputs(
                round(cue.start * 1_000),
                round(cue.end * 1_000),
                cue.text,
                frame_size,
                storage_size,
                pixel_aspect,
            )

            def build(inputs: _CueInputs = inputs) -> GeometryRequest:
                tokenized = reader._tokenize_cue(reader._cue_norm(inputs.text))
                annotations = self._annotations(inputs.text, tokenized.lines, tokenized.tokens)
                return self._build(path, track_id, generation, inputs, annotations)

            self.worker.prefetch(self._key(path, inputs), generation, build)

    def schedule(self, reader: Reader) -> bool:
        path = self.source_path
        if path is None or not reader.sub_text.strip() or not reader.tokens:
            return False
        start = reader._prop("sub-start")
        end = reader._prop("sub-end")
        if start is None or end is None:
            self.fallback_reason = "subtitle-timing-unavailable"
            return False
        frame_size, storage_size, pixel_aspect = self._render_inputs(reader)
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
        )
        key = self._key(path, cue)
        if cached := self.worker.publish_prefetched(key, generation):
            self.worker.mark_presented(cached)
            self.fallback_reason = None
            self._prefetch(
                reader, path, track_id, generation, (frame_size, storage_size, pixel_aspect)
            )
            return True
        annotations = self._annotations(reader.sub_text, reader.lines, reader.tokens)

        def build() -> GeometryRequest:
            return self._build(path, track_id, generation, cue, annotations)

        accepted = self.worker.submit_job(generation, build)
        if accepted:
            self.worker.mark_not_ready()
            self.fallback_reason = None
            self._prefetch(
                reader,
                path,
                track_id,
                generation,
                (frame_size, storage_size, pixel_aspect),
            )
        return accepted

    def apply(self, reader: Reader) -> bool:
        snapshot = reader.subtitle_pipeline.current
        if snapshot is None:
            if reader.subtitle_pipeline.last_error is not None:
                self.fallback_reason = "geometry-provider-failed"
                reader.boxes = []
            return False
        if snapshot is self._last_snapshot:
            return False
        tokens = snapshot.tokens
        indices = [item.token_index for item in tokens]
        if len(indices) != len(set(indices)) or any(
            not 0 <= index < len(reader.tokens) for index in indices
        ):
            self.fallback_reason = "geometry-token-identity-invalid"
            reader.boxes = []
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
        self.fallback_reason = None
        reader._draw_subtitle()
        return True

    def close(self) -> None:
        self.worker.close()
