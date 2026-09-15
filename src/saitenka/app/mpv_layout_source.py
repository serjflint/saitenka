"""Owner-thread acquisition of retained mpv layout, fenced by cue and connection identity."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from saitenka_subtitles.mpv_layout import (
    LayoutSnapshot,
    decode_layout,
    supports_layout,
    token_regions,
)

from saitenka import otel_metrics
from saitenka.app.subtitles import WordBox
from saitenka.runtime import EffectError, EffectFinished, EffectOutcome, Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.native_subtitles import GeometryObservation
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.mpvio.ipc import MpvIPC


@dataclass(frozen=True, slots=True)
class LayoutPorts:
    observe: Callable[[], GeometryObservation]
    clear: Callable[[], None]
    invalidate: Callable[[], None]
    publish: Callable[[list[WordBox]], bool]
    reschedule: Callable[[], None]
    permitted: Callable[[], bool]
    unavailable: Callable[[], None]
    changed: Callable[[], None]


def _binding(seen: GeometryObservation) -> tuple[object, ...]:
    return (
        seen.cue_revision,
        seen.text,
        seen.osd,
        seen.prop("sid"),
        seen.prop("sub-start"),
        seen.prop("sub-end"),
        seen.prop("subtitle-layout-revision"),
        tuple((token.surface, token.start, token.end) for token in seen.tokens),
    )


def _matches(layout: LayoutSnapshot, seen: GeometryObservation, formats: str) -> bool:
    playlist = seen.prop("playlist")
    if not isinstance(playlist, list) or len(playlist) > 10000:
        return False
    playing = [
        item.get("id") for item in playlist if isinstance(item, dict) and item.get("current")
    ]
    return (
        playing == [layout.playlist_entry_id]
        and seen.prop("sid") == layout.track_id
        and layout.text == seen.text
        and layout.start == seen.prop("sub-start")
        and layout.end == seen.prop("sub-end")
        and layout.size == seen.osd
        and layout.revision == seen.prop("subtitle-layout-revision")
        and (layout.format == "ass" or formats == "all")
    )


def _token_spans(text: str, seen: GeometryObservation) -> tuple[tuple[int, int], ...]:
    source_lines = iter(text.split("\n"))
    offset = 0
    spans = []
    flattened = []
    for tokens in seen.lines:
        line = next(source_lines, None)
        while line is not None and not line.strip():
            offset += len(line) + 1
            line = next(source_lines, None)
        if line is None:
            raise ValueError("layout-token-lines")
        for token in tokens:
            if (
                not 0 <= token.start < token.end <= len(line)
                or line[token.start : token.end] != token.surface
            ):
                raise ValueError("layout-token-text")
            spans.append((offset + token.start, offset + token.end))
            flattened.append(token)
        offset += len(line) + 1
    if flattened != seen.tokens:
        raise ValueError("layout-token-lines")
    return tuple(spans)


def layout_boxes(layout: LayoutSnapshot, seen: GeometryObservation) -> list[WordBox]:
    spans = _token_spans(layout.text, seen)
    regions = token_regions(layout, spans)
    boxes = []
    for index, rects in enumerate(regions):
        if not rects or seen.is_skippable(seen.tokens[index]):
            continue
        left, top = min(r.x for r in rects), min(r.y for r in rects)
        right = max(r.x + r.width for r in rects)
        bottom = max(r.y + r.height for r in rects)
        boxes.append(
            WordBox(
                index,
                left,
                top,
                right - left,
                bottom - top,
                overprint_safe=False,
                hit_regions=rects,
            )
        )
    return boxes


def _refusal_reason(error: ValueError | TypeError | OverflowError) -> str:
    # Unicode exceptions can include subtitle content in their messages.
    return "layout-text-encoding" if isinstance(error, UnicodeError) else str(error)


_SHADOW_FALLBACK_REASONS = frozenset(
    {
        "layout-unsupported-render-mode",
        "layout-event-count",
        "layout-geometry-profile",
        "layout-margins",
    }
)


class MpvLayoutSource:
    """At most one fetch/validate chain; observations retire hits before scheduling replacement."""

    def __init__(
        self, ipc: MpvIPC, pipeline: SubtitleModeCoordinator, ports: LayoutPorts, *, formats: str
    ) -> None:
        self._ipc = ipc
        self._pipeline = pipeline
        self._ports = ports
        self._formats = formats
        self._epoch = 0
        self._busy = False
        self._dirty = False
        self._closed = False
        self._configured = False
        self._restore: bool | None = None
        self._external_disabled = False
        self._saw_enabled = False
        self.status = "unprobed"
        self.fallback_reason: str | None = None
        self.attempt = 0
        self._request_generation = 0
        self._request_cue_revision = 0
        self.supported: bool | None = None
        self.current: LayoutSnapshot | None = None

    @property
    def selection_reason(self) -> str:
        return self.fallback_reason or self.status

    def invalidate(self) -> None:
        self.current = None
        self._pipeline.invalidate()
        if self.supported is not False:
            self.status = "invalidated"
        self._ports.invalidate()
        self._dirty = True
        self._record_status()

    def connection_replaced(self) -> None:
        self.supported = None
        self.fallback_reason = None
        self._epoch += 1
        self._busy = False
        self._configured = False
        self._restore = None
        self._external_disabled = False
        self._saw_enabled = False
        self.invalidate()
        self._ports.reschedule()

    def input_changed(self) -> None:
        if not self._ports.permitted():
            return
        option = self._ports.observe().prop("options/subtitle-layout")
        if option is True:
            self._saw_enabled = True
            self._external_disabled = False
        if self._configured and self._saw_enabled and option is False:
            self._restore = None
            self._external_disabled = True
        self.invalidate()
        self._ports.reschedule()

    def refresh(self) -> None:
        if self._closed or self.supported is False or not self._ports.permitted():
            return
        if self._busy:
            self._dirty = True
            return
        if self._external_disabled:
            self.status = "disabled-externally"
            self._record_status()
            return
        self._busy = True
        self._dirty = False
        self.attempt += 1
        self.status = "fetching"
        generation = self._pipeline.generation
        seen = self._ports.observe()
        self._request_generation = generation
        self._request_cue_revision = seen.cue_revision
        self._record_status()
        binding = _binding(seen)

        self._send(
            ("subtitle-layout", "primary"), partial(self._received, generation, seen, binding)
        )

    def _received(
        self, generation: int, seen: GeometryObservation, binding: tuple[object, ...], value: object
    ) -> None:
        if not supports_layout(value):
            self.supported = False
            self._refuse("unsupported-api")
            self._ports.unavailable()
            return
        self.supported = True
        if not self._configured:
            self._configure()
            return
        try:
            layout = decode_layout(value)
            boxes = layout_boxes(layout, seen)
        except (ValueError, TypeError, OverflowError) as error:
            self._refuse(_refusal_reason(error))
            return
        if not _matches(layout, seen, self._formats):
            self._refuse("unbound-event")
            return

        def validated(valid: object) -> None:
            current = self._ports.observe()
            if (
                valid is True
                and generation == self._pipeline.generation
                and binding == _binding(current)
                and _matches(layout, current, self._formats)
                and self._ports.permitted()
            ):
                self.current = layout
                self.fallback_reason = None
                accepted = self._ports.publish(boxes)
                self.status = (
                    ("scan-only" if boxes else "unmapped-tokens")
                    if accepted
                    else "waiting-for-native-owner"
                )
            else:
                self.current = None
                self._ports.clear()
                self.status = "stale"
            self._finish()

        self._send(("subtitle-layout-valid", layout.snapshot_id), validated)

    def _refuse(self, reason: str) -> None:
        self.current = None
        if reason == "unsupported-api":
            self.fallback_reason = None
        self._ports.clear()
        self.status = reason
        if reason in _SHADOW_FALLBACK_REASONS and self.fallback_reason != reason:
            self.fallback_reason = reason
            self._ports.unavailable()
        self._finish()

    def _configure(self) -> None:
        def option(value: object) -> None:
            if type(value) is not bool:
                self.status = "unsupported-option"
                self._finish()
                return
            if value:
                self._saw_enabled = True
                self._configured = True
                self.status = "collection-ready"
                self._dirty = True
                self._finish()
                return

            def enabled(_value: object) -> None:
                self._configured = True
                self.status = "collection-ready"
                self._dirty = True
                self._finish()

            self._restore = False
            self._send(("set_property", "subtitle-layout", True), enabled)

        self._send(("get_property", "subtitle-layout"), option)

    def _send(self, command: tuple, callback: Callable[[object], None]) -> None:
        epoch = self._epoch

        def finished(completion: EffectFinished) -> None:
            if self._closed or epoch != self._epoch:
                return
            if completion.outcome is not EffectOutcome.SUCCEEDED:
                if (
                    command[0] == "subtitle-layout"
                    and completion.error is EffectError.INVALID_RESULT
                ):
                    self.supported = False
                    self._refuse("unsupported-api")
                    self._ports.unavailable()
                    return
                self._refuse("ipc-failed")
                return
            callback(completion.result)

        if not self._ipc.submit_runtime_mpv(
            owner=Owner.SUBTITLE,
            identity=("native-layout", epoch, command[0]),
            command=command,
            timeout_s=5.0,
            on_finished=finished,
        ):
            self._refuse("ipc-unavailable")

    def _record_status(self) -> None:
        with otel_metrics.traced("subtitle_geometry_acquisition") as span:
            span.set("geometry_source", "mpv")
            span.set("reason", self.status)
            span.set("attempt", self.attempt)
            span.set("source_epoch", self._epoch)
            span.set("generation", self._pipeline.generation)
            span.set("cue_revision", self._ports.observe().cue_revision)
            span.set("request_generation", self._request_generation)
            span.set("request_cue_revision", self._request_cue_revision)
            span.set(
                "capability", "unknown" if self.supported is None else str(self.supported).lower()
            )
        self._ports.changed()

    def _finish(self) -> None:
        self._busy = False
        self._record_status()
        if self._dirty and not self._closed:
            self._ports.reschedule()

    def suspend(self) -> None:
        self._epoch += 1
        self._busy = False
        self._configured = False
        self._saw_enabled = False
        self._external_disabled = False
        self.invalidate()
        # Restore only an option this connection enabled and the user has not withdrawn.
        if self._restore is False:
            reply = self._ipc.probe("subtitle-layout")
            if reply.get("data") is True:
                self._ipc.command("set_property", "subtitle-layout", self._restore)
        self._restore = None

    def close(self) -> None:
        self._closed = True
        self.suspend()
