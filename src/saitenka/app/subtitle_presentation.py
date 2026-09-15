"""Owner-thread state derived while presenting the current subtitle cue."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from saitenka import otel_metrics
from saitenka.app import geometry_refresh, native_subtitles
from saitenka.app.mpv_layout_source import LayoutPorts, MpvLayoutSource
from saitenka.app.subtitle_geometry_job import SubtitleGeometryWorker
from saitenka.app.subtitle_geometry_job import configure_runtime_job as configure_geometry_lane
from saitenka.app.subtitle_pipeline import CurrentSubtitleRenderer, SubtitleModeCoordinator
from saitenka.app.subtitle_render import NativeVisibleRenderer, SubtitleTarget

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka_subtitles import GeometryBackend
    from saitenka_tokenize.japanese import Token

    from saitenka.app.config import SubtitleGeometryOptions, TooltipOptions
    from saitenka.app.scoring import TokenStyle
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
    from saitenka.app.subtitles import WordBox
    from saitenka.app.token_cache import TokenizedCue
    from saitenka.mpvio.ipc import MpvIPC

_UNCHANGED = object()
_LAYOUT_WARNINGS = {
    "layout-unsupported-render-mode": "the mpv render mode does not expose subtitle geometry",
    "layout-event-count": "mpv geometry does not yet support overlapping subtitle events",
    "layout-geometry-profile": "this subtitle's geometry is outside the supported profile",
    "layout-margins": "mpv geometry does not yet support these display margins",
    "unsupported-option": "mpv subtitle layout collection could not be enabled",
    "disabled-externally": "mpv subtitle layout collection was disabled",
    "ipc-failed": "the mpv geometry request failed",
    "ipc-unavailable": "the mpv geometry connection is unavailable",
}
_COLOR_EFFECT = re.compile(r"\\(?:[1-4]?[ac](?![a-z])|alpha|k|t\(|fad|fade|r)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SubtitleVisualSettings:
    size_override: int | None
    bottom_margin_fraction: float
    background_opacity: int

    @classmethod
    def from_options(cls, options: TooltipOptions) -> SubtitleVisualSettings:
        return cls(
            options.sub_size,
            options.bottom_margin_frac,
            max(0, min(255, options.sub_background_opacity)),
        )

    def size(self, osd_height: int) -> int:
        return self.size_override or max(28, round(osd_height * 0.05))

    def bottom_margin(self, osd_height: int) -> int:
        return round(osd_height * self.bottom_margin_fraction)


@dataclass(frozen=True, slots=True)
class CueRenderState:
    """Tokenization and geometry derived from the cue owned by playback."""

    lines: list[list[Token]] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
    styles: list[TokenStyle] | None = None
    boxes: list[WordBox] = field(default_factory=list)
    origin: tuple[int, int] = (0, 0)
    paint_allowed: bool = True


def boxes_for(tokens: list[Token], boxes: list[WordBox]) -> list[WordBox]:
    """Only the boxes indexing a token ``tokens`` has.

    `TooltipController.hit` indexes `tokens` by whatever box answers a click, so a box past the end
    is a crash or a hit region on another cue's word. Geometry measured for the cue that left can
    reach the one that arrived: a `DrawRequest` takes its text from `playback.cue.text`, written
    when mpv observes `sub-text`, and its content from this store, written by `set_subtitle`.

    An index check, not an emptiness check — three boxes against six tokens is the same defect.
    Applied on write so drawing and hit testing cannot disagree.

    A drop is counted: a silent filter turns a mispaired box into a missing one, which reads in a
    bundle as geometry that never arrived.
    """
    kept = [box for box in boxes if 0 <= box.index < len(tokens)]
    if len(kept) != len(boxes) and otel_metrics.subtitle_boxes_dropped is not None:
        otel_metrics.subtitle_boxes_dropped.add(len(boxes) - len(kept))
    return kept


class CueRenderStore:
    """Single writer for the current cue's derived render facts."""

    def __init__(self) -> None:
        self._current = CueRenderState()

    @property
    def current(self) -> CueRenderState:
        return self._current

    def reset(self) -> None:
        self._current = CueRenderState()

    def clear_annotation(self) -> None:
        state = self._current
        self._current = CueRenderState(
            boxes=state.boxes, origin=state.origin, paint_allowed=state.paint_allowed
        )

    def install_tokenized(self, cue: TokenizedCue) -> None:
        state = self._current
        self._current = CueRenderState(
            lines=cue.lines,
            tokens=cue.tokens,
            styles=cue.styles,
            boxes=boxes_for(cue.tokens, state.boxes),
            origin=state.origin,
            paint_allowed=state.paint_allowed,
        )

    def replace_tokenized(
        self,
        *,
        lines: list[list[Token]] | object = _UNCHANGED,
        tokens: list[Token] | object = _UNCHANGED,
        styles: list[TokenStyle] | object | None = _UNCHANGED,
    ) -> None:
        """Replace selected derived cue facts while preserving one atomic state value."""
        state = self._current
        replaced = state.tokens if tokens is _UNCHANGED else cast("list[Token]", tokens)
        self._current = CueRenderState(
            lines=state.lines if lines is _UNCHANGED else cast("list[list[Token]]", lines),
            tokens=replaced,
            styles=(
                state.styles if styles is _UNCHANGED else cast("list[TokenStyle] | None", styles)
            ),
            boxes=boxes_for(replaced, state.boxes),
            origin=state.origin,
            paint_allowed=state.paint_allowed,
        )

    def clear_geometry(self) -> None:
        state = self._current
        self._current = CueRenderState(
            state.lines, state.tokens, state.styles, paint_allowed=state.paint_allowed
        )

    def publish_geometry(
        self, boxes: list[WordBox], origin: tuple[int, int], *, paint_allowed: bool = True
    ) -> None:
        state = self._current
        self._current = CueRenderState(
            state.lines,
            state.tokens,
            state.styles,
            boxes_for(state.tokens, boxes),
            origin,
            paint_allowed,
        )

    def replace_geometry(
        self,
        *,
        boxes: list[WordBox] | object = _UNCHANGED,
        origin: tuple[int, int] | object = _UNCHANGED,
        paint_allowed: bool | None = None,
    ) -> None:
        """Replace selected geometry facts through the cue-render owner."""
        state = self._current
        self._current = CueRenderState(
            state.lines,
            state.tokens,
            state.styles,
            boxes_for(
                state.tokens,
                state.boxes if boxes is _UNCHANGED else cast("list[WordBox]", boxes),
            ),
            state.origin if origin is _UNCHANGED else cast("tuple[int, int]", origin),
            state.paint_allowed if paint_allowed is None else paint_allowed,
        )


@dataclass(frozen=True, slots=True)
class SubtitlePresentationPorts:
    target: Callable[
        [SubtitleModeCoordinator, native_subtitles.NativeSubtitleGeometry | None], SubtitleTarget
    ]
    geometry: Callable[[], native_subtitles.GeometryObservation]
    clear_interaction: Callable[[], None]
    redraw_cue: Callable[[], None]
    tokenize_lookahead: Callable[[str], TokenizedCue]
    #: Tell the user, not just the log. A geometry refusal costs scanning and overpaint for the
    #: whole episode, and until this existed the only sign was a log line nobody reads live.
    notify: Callable[[str, str], None]


class SubtitlePresentation:
    """Own the current subtitle renderer, native geometry, and derived cue pixels."""

    def __init__(
        self,
        ipc: MpvIPC,
        *,
        settings: SubtitleGeometryOptions,
        visual: SubtitleVisualSettings,
        renderer: SubtitleRenderer | NullRenderer | None,
        backend: GeometryBackend | None,
        ports: SubtitlePresentationPorts,
    ) -> None:
        if settings.source == "mpv" and backend is not None:
            raise ValueError("mpv geometry source conflicts with an injected shadow backend")
        current: CurrentSubtitleRenderer = renderer if renderer is not None else _default_renderer()
        if settings.native_visible and renderer is None:
            current = NativeVisibleRenderer()
        self.pipeline = SubtitleModeCoordinator(current, backend)
        self.visual = visual
        self.cue = CueRenderStore()
        self._ports = ports
        self._source = settings.source
        self._scan_source = "none"
        self._layout_warnings: set[str] = set()
        self._last_geometry_record: dict[str, str | int | bool] = {}
        self._shadow_boxes: list[WordBox] = []
        self._shadow_generation = -1
        self.native: native_subtitles.NativeSubtitleGeometry | None = None
        self.layout: MpvLayoutSource | None = None
        self.refresh = geometry_refresh.GeometryRefreshController(
            ipc,
            generation=lambda: self.pipeline.generation,
            refresh=self._refresh_geometry,
        )
        if not settings.native_visible:
            return
        if settings.source in {"mpv", "auto"}:
            if renderer is None:
                self.layout = MpvLayoutSource(
                    ipc,
                    self.pipeline,
                    LayoutPorts(
                        observe=ports.geometry,
                        clear=self._clear_layout,
                        invalidate=self.clear_native_interaction,
                        publish=self._publish_layout,
                        reschedule=self.refresh.arm,
                        permitted=lambda: not self.pipeline.legacy_forced,
                        unavailable=self._layout_unavailable,
                        changed=self._record_geometry,
                    ),
                    formats=settings.native_formats,
                )
            if settings.source == "mpv":
                return
        self.native = native_subtitles.NativeSubtitleGeometry(
            SubtitleGeometryWorker(
                self.pipeline,
                cache_max=settings.cache_max,
                submit=configure_geometry_lane(ipc),
            ),
            native_subtitles.GeometryPorts(
                pipeline=self.pipeline,
                degrade=self.degrade_native_geometry,
                clear_interaction=self._clear_shadow,
                use_native=self.use_native_renderer,
                ownership_undecided=self.native_ownership_undecided,
                redraw=self.draw,
                reschedule=self.refresh.arm,
                publish=self.publish_geometry,
                tokenize_lookahead=ports.tokenize_lookahead,
                notify=ports.notify,
            ),
            lookahead=settings.lookahead,
            formats=native_subtitles.native_formats(settings.native_formats),
        )
        native_subtitles.connect_drift_sink(current, self.native)

    @property
    def renderer(self) -> CurrentSubtitleRenderer:
        return self.pipeline.renderer

    @renderer.setter
    def renderer(self, renderer: CurrentSubtitleRenderer) -> None:
        self.pipeline.renderer = renderer

    def target(self) -> SubtitleTarget:
        return self._ports.target(self.pipeline, self.native)

    def publish_geometry(self, boxes: list, origin: tuple[int, int] | None = None) -> None:
        self._shadow_boxes = boxes
        self._shadow_generation = self.pipeline.generation
        if self.using_layout:
            self.cue.replace_geometry(paint_allowed=self._paint_ready())
            self._record_geometry()
            return
        self._scan_source = "shadow"
        current_origin = self.cue.current.origin
        self.cue.publish_geometry(
            boxes,
            current_origin if origin is None else origin,
            paint_allowed=self._shadow_paint_allowed(),
        )
        self._record_geometry()

    def draw(self) -> None:
        result = self.pipeline.draw_current(self.target())
        if result is not None:
            self._scan_source = "legacy"
            self.cue.publish_geometry(result.boxes, result.origin)
        if self.native is not None:
            self.native.sync_pixel_owner(self.pipeline.renderer)
        self._record_geometry()

    def _paint_diagnosis(self, *, allowed: bool) -> str:
        if allowed:
            return "eligible"
        if not self.cue.current.boxes:
            return "no-scan-regions"
        if self._source == "mpv":
            return "scan-only-policy"
        return "shadow-paint-unqualified"

    def _warn_layout_failure(self, reason: str) -> None:
        detail = _LAYOUT_WARNINGS.get(reason)
        if detail is None or reason in self._layout_warnings:
            return
        self._layout_warnings.add(reason)
        fallback = (
            self._source == "auto"
            and self.layout is not None
            and self.layout.fallback_reason is not None
        )
        message = (
            f"Using shadow subtitle geometry: {detail}."
            if fallback
            else f"Subtitle scanning unavailable: {detail}. Try shadow geometry."
        )
        self._ports.notify(message, "warn")

    def _record_geometry(self) -> None:
        if self.native is None and self.layout is None:
            return
        cue = self.cue.current
        native = (
            isinstance(self.renderer, NativeVisibleRenderer) and not self.pipeline.legacy_forced
        )
        selected = ("mpv" if self.using_layout else "shadow") if native else "legacy"
        scan_source = self._scan_source if cue.boxes else "none"
        paint_allowed = bool(cue.boxes) and cue.paint_allowed
        paint_source = (
            ("legacy" if scan_source == "legacy" else "shadow") if paint_allowed else "none"
        )
        reason = "configured-shadow" if native else "legacy"
        if self.layout is not None:
            reason = self.layout.selection_reason
        record: dict[str, str | int | bool] = {
            "configured_source": self._source,
            "selected_source": selected,
            "scan_source": scan_source,
            "paint_source": paint_source,
            "paint_allowed": paint_allowed,
            "paint_reason": self._paint_diagnosis(allowed=paint_allowed),
            "eligible_tokens": len(cue.boxes),
            "cue_revision": self._ports.geometry().cue_revision,
            "generation": self.pipeline.generation,
            "reason": reason,
        }
        if record == self._last_geometry_record:
            return
        self._last_geometry_record = record
        self.pipeline.record_geometry_source(record)
        self._warn_layout_failure(reason)
        with otel_metrics.traced("subtitle_geometry_source") as span:
            for key, value in record.items():
                span.set(key, value)

    def _publish_layout(self, boxes: list[WordBox]) -> bool:
        if self.use_native_renderer():
            self._scan_source = "mpv"
            self.cue.publish_geometry(boxes, (0, 0), paint_allowed=self._paint_ready())
            self.draw()
            return True
        return False

    @property
    def using_layout(self) -> bool:
        return self.layout is not None and (
            self._source == "mpv"
            or (self.layout.supported is not False and self.layout.fallback_reason is None)
        )

    @property
    def paint_boxes(self) -> list[WordBox] | None:
        return self._shadow_boxes if self.using_layout else None

    def _shadow_paint_allowed(self) -> bool:
        if self._source != "auto":
            return True
        raw = self._ports.geometry().prop("sub-text/ass-full")
        return isinstance(raw, str) and len(raw) <= 32768 and not _COLOR_EFFECT.search(raw)

    def _paint_ready(self) -> bool:
        if self._source != "auto" or self.layout is None or self.layout.current is None:
            return False
        if self._shadow_generation != self.pipeline.generation or not self._shadow_boxes:
            return False
        raw = self._ports.geometry().prop("sub-text/ass-full")
        frame = self.layout.current
        if not isinstance(raw, str) or len(raw) > 32768:
            return False
        rows = raw.splitlines()
        if len(rows) != 1 or not rows[0].startswith("Dialogue:"):
            return False
        fields = rows[0].split(",", 9)
        if len(fields) != 10:
            return False
        plain = fields[9].replace(r"\N", "\n")
        if plain != frame.text or any(c in plain for c in "{\\"):
            return False
        try:
            start = sum(
                float(v) * factor
                for v, factor in zip(fields[1].split(":"), (3600, 60, 1), strict=True)
            )
            end = sum(
                float(v) * factor
                for v, factor in zip(fields[2].split(":"), (3600, 60, 1), strict=True)
            )
        except ValueError:
            return False
        return start == frame.start and end == frame.end

    def _clear_layout(self) -> None:
        if self.using_layout:
            self.clear_native_interaction()

    def _clear_shadow(self) -> None:
        self._shadow_boxes = []
        self._shadow_generation = -1
        if self.using_layout:
            self.cue.replace_geometry(paint_allowed=False)
            self.draw()
        else:
            self.clear_native_interaction()

    def _layout_unavailable(self) -> None:
        if self._source == "auto":
            if self._shadow_generation == self.pipeline.generation:
                self._scan_source = "shadow"
                self.cue.publish_geometry(
                    self._shadow_boxes, (0, 0), paint_allowed=self._shadow_paint_allowed()
                )
                self.draw()
            else:
                self.clear_native_interaction()
            self.refresh.arm()
        elif self.layout is not None and self.layout.supported is False:
            self._ports.notify(
                "This mpv does not provide the required subtitle layout capabilities.", "warn"
            )

    def geometry_changed(self, property_name: str = "") -> None:
        if self.layout is not None:
            self.layout.input_changed(shared=property_name != "subtitle-layout-revision")
        elif self.native is not None:
            self.refresh.arm()

    def invalidate_geometry(self) -> None:
        if self.layout is not None:
            self.layout.invalidate()
        elif self.native is not None:
            self.native.invalidate(live=True)
        else:
            self.pipeline.invalidate()

    def connection_replaced(self) -> None:
        self._layout_warnings.clear()
        if self.layout is not None:
            self.layout.connection_replaced()
        self.pipeline.connection_replaced(self.target())

    def toggle_renderer(self) -> bool:
        if self.layout is not None:
            self.layout.suspend()
        if self.native is not None:
            self.native.invalidate(live=True)
        forced = self.pipeline.force_legacy(
            self.target(),
            forced=not self.pipeline.legacy_forced,
        )
        self._ports.redraw_cue()
        if self.layout is not None:
            self.refresh.arm()
        return forced

    def deactivate(self) -> None:
        if self.layout is not None:
            self.layout.close()
        self.pipeline.deactivate(self.target())

    def clear_pixels(self) -> None:
        """Clear native-owned pixels; legacy rendering has no retained subtitle surface."""
        if self.native is not None or self.layout is not None:
            target = self.target()
            self.pipeline.clear(target.surfaces, target.ipc)

    def close_raster(self) -> None:
        """Close whichever object owns the active subtitle raster."""
        if self.native is not None:
            self.native.close()
        else:
            self.pipeline.close()

    def clear_native_interaction(self) -> None:
        self._ports.clear_interaction()
        self.cue.clear_geometry()
        target = self.target()
        self.pipeline.clear(target.surfaces, target.ipc)
        self._record_geometry()

    def degrade_native_geometry(self) -> None:
        if self.using_layout:
            self._clear_shadow()
            return
        renderer = self.pipeline.renderer
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", None)
        if owner != "legacy":
            self.cue.clear_geometry()
        self.pipeline.geometry_degraded(self.target())
        self._record_geometry()

    def use_native_renderer(self) -> bool:
        return self.pipeline.renderer.use_native(self.target())

    def native_ownership_undecided(self) -> bool:
        renderer = self.pipeline.renderer
        return isinstance(renderer, NativeVisibleRenderer) and renderer.assertion_in_flight

    def _refresh_geometry(self) -> None:
        if self.native is not None:
            self.native.refresh(self._ports.geometry())
        if self.layout is not None:
            self.layout.refresh()


def _default_renderer() -> SubtitleRenderer:
    from saitenka.app.subtitle_render import SubtitleRenderer

    return SubtitleRenderer()
