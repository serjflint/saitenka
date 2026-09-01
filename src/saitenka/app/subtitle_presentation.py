"""Owner-thread state derived while presenting the current subtitle cue."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from saitenka.app import geometry_refresh, native_subtitles
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
        self._current = CueRenderState(boxes=state.boxes, origin=state.origin)

    def install_tokenized(self, cue: TokenizedCue) -> None:
        state = self._current
        self._current = CueRenderState(
            lines=cue.lines,
            tokens=cue.tokens,
            styles=cue.styles,
            boxes=state.boxes,
            origin=state.origin,
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
        self._current = CueRenderState(
            lines=state.lines if lines is _UNCHANGED else cast("list[list[Token]]", lines),
            tokens=state.tokens if tokens is _UNCHANGED else cast("list[Token]", tokens),
            styles=(
                state.styles if styles is _UNCHANGED else cast("list[TokenStyle] | None", styles)
            ),
            boxes=state.boxes,
            origin=state.origin,
        )

    def clear_geometry(self) -> None:
        state = self._current
        self._current = CueRenderState(state.lines, state.tokens, state.styles)

    def publish_geometry(self, boxes: list[WordBox], origin: tuple[int, int]) -> None:
        state = self._current
        self._current = CueRenderState(state.lines, state.tokens, state.styles, boxes, origin)

    def replace_geometry(
        self,
        *,
        boxes: list[WordBox] | object = _UNCHANGED,
        origin: tuple[int, int] | object = _UNCHANGED,
    ) -> None:
        """Replace selected geometry facts through the cue-render owner."""
        state = self._current
        self._current = CueRenderState(
            state.lines,
            state.tokens,
            state.styles,
            state.boxes if boxes is _UNCHANGED else cast("list[WordBox]", boxes),
            state.origin if origin is _UNCHANGED else cast("tuple[int, int]", origin),
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
        current: CurrentSubtitleRenderer = renderer if renderer is not None else _default_renderer()
        if settings.native_visible and renderer is None:
            current = NativeVisibleRenderer()
        self.pipeline = SubtitleModeCoordinator(current, backend)
        self.visual = visual
        self.cue = CueRenderStore()
        self._ports = ports
        self.native: native_subtitles.NativeSubtitleGeometry | None = None
        self.refresh = geometry_refresh.GeometryRefreshController(
            ipc,
            generation=lambda: self.pipeline.generation,
            refresh=self._refresh_geometry,
        )
        if not settings.native_visible:
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
                clear_interaction=self.clear_native_interaction,
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
        current_origin = self.cue.current.origin
        self.cue.publish_geometry(boxes, current_origin if origin is None else origin)

    def draw(self) -> None:
        result = self.pipeline.draw_current(self.target())
        if result is not None:
            self.cue.publish_geometry(result.boxes, result.origin)
        if self.native is not None:
            self.native.sync_pixel_owner(self.pipeline.renderer)

    def toggle_renderer(self) -> bool:
        if self.native is not None:
            self.native.invalidate(live=True)
        forced = self.pipeline.force_legacy(
            self.target(),
            forced=not self.pipeline.legacy_forced,
        )
        self._ports.redraw_cue()
        return forced

    def deactivate(self) -> None:
        self.pipeline.deactivate(self.target())

    def clear_pixels(self) -> None:
        """Clear native-owned pixels; legacy rendering has no retained subtitle surface."""
        if self.native is not None:
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

    def degrade_native_geometry(self) -> None:
        renderer = self.pipeline.renderer
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", None)
        if owner != "legacy":
            self.cue.clear_geometry()
        self.pipeline.geometry_degraded(self.target())

    def use_native_renderer(self) -> bool:
        return self.pipeline.renderer.use_native(self.target())

    def native_ownership_undecided(self) -> bool:
        renderer = self.pipeline.renderer
        return isinstance(renderer, NativeVisibleRenderer) and renderer.assertion_in_flight

    def _refresh_geometry(self) -> None:
        if self.native is not None:
            self.native.refresh(self._ports.geometry())


def _default_renderer() -> SubtitleRenderer:
    from saitenka.app.subtitle_render import SubtitleRenderer

    return SubtitleRenderer()
