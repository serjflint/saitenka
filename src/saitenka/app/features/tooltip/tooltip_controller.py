"""Own the tooltip stack's mutable state and volatile background work."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from saitenka.app.feature_bindings import (
    HOVER_PAUSE_STATEFUL_BINDING,
    HOVER_STATEFUL_BINDING,
    HOVERED_WORD_STATEFUL_BINDING,
    PULSE_STATEFUL_BINDING,
    TIP_NAV_STATEFUL_BINDING,
)
from saitenka.app.features.tooltip import (
    hover_metadata,
    hover_snapshot,
    mined_feedback,
    nested_popup,
    tooltip,
    tooltip_engaged,
    tooltip_panel,
    tooltip_raster,
)
from saitenka.app.features.tooltip.popups import (
    HoverInputs,
    ShowActions,
    TipPorts,
    TooltipState,
    hovered_meta,
)
from saitenka.app.interaction.surfaces import (
    ClickTarget,
    SurfaceSpec,
    WheelStep,
    tip_wheel_pixels,
)
from saitenka.runtime import events

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from saitenka.app.features.help.help_controller import TooltipKeyContext
    from saitenka.app.features.tooltip.popups import (
        HoverMetadata as HoverMetadataView,
    )
    from saitenka.app.features.tooltip.popups import (
        Panel,
        PopupView,
        WordLookup,
    )
    from saitenka.app.features.tooltip.prefetch import TipScale
    from saitenka.app.features.tooltip.tooltip_panel import PanelKey, PanelPorts, PanelStyle
    from saitenka.app.interaction.presentation import InteractionSurfaces
    from saitenka.app.render_cache import LoadedView
    from saitenka.runtime import EffectFinished
    from saitenka.runtime.hover import HoverDelays
    from saitenka.runtime.hover_pause import PauseClaim
    from saitenka.runtime.jobs import JobSubmitter
    from saitenka.runtime.pulse import PulseState, Repaint

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TooltipApply:
    """Fresh owner-thread values used to apply one worker completion."""

    ports: TipPorts
    panel: PanelPorts
    lookup: WordLookup
    hover: HoverInputs
    show: ShowActions
    generation: int


@dataclass(frozen=True, slots=True)
class TooltipObservation:
    """Frozen product facts other features may observe during one owner-thread turn."""

    selected: int
    pause_enabled: bool
    navigation_depth: int
    can_go_back: bool
    pulse: PulseState
    pause: PauseClaim
    reading: str
    kanji_index: int
    metadata: HoverMetadataView


@dataclass(frozen=True, slots=True)
class HoverDiagnostics:
    """Immutable hover-machine facts used by transition diagnostics."""

    word_target: int | None
    nested_hide_pending: bool


@dataclass(frozen=True, slots=True)
class TooltipRuntimeJobs:
    """Construction-time placement of Tooltip's three volatile job lanes."""

    metadata: JobSubmitter | None
    engaged: JobSubmitter | None
    render_ahead: JobSubmitter | None


@dataclass(frozen=True, slots=True)
class TooltipPresentation:
    """Fresh physical capabilities bound to one Tooltip presentation turn."""

    scale: TipScale
    surfaces: InteractionSurfaces
    request_render_ahead: Callable[[PopupView, int], bool]
    osd: tuple[int, int]
    nested_max_frac: float
    peek_render_cache: Callable[[object], LoadedView | None]
    schedule_flash_expiry: Callable[[], bool]
    toast: Callable[..., None]
    request_engaged_tooltip: Callable[[tooltip_engaged.EngagedRequest], bool]


class TooltipController:
    """Own tooltip state, work admission, stale refusal, fallback, and publication."""

    def __init__(
        self,
        ipc,
        build: tooltip_engaged.EngagedBuildPorts,
        *,
        panel_cache_max: int,
        pause_enabled: bool,
        delays: HoverDelays,
        flash_seconds: float,
        key_context: TooltipKeyContext,
        runtime_jobs: Callable[[TooltipRuntimeJobs], TooltipRuntimeJobs] | None = None,
    ) -> None:
        self._cache_lock = threading.Lock()
        self._state = TooltipState(
            panel_cache_max=panel_cache_max,
            cache_lock=self._cache_lock,
        )
        self._selected = -1
        self._pause_enabled = pause_enabled
        self._delays = delays
        self._flash_seconds = flash_seconds
        self._key_context = key_context
        self._hover_store = HOVER_STATEFUL_BINDING.store(ipc)
        self._nav_store = TIP_NAV_STATEFUL_BINDING.store(ipc)
        self._pulse_store = PULSE_STATEFUL_BINDING.store(ipc)
        self._pause_store = HOVER_PAUSE_STATEFUL_BINDING.store(ipc)
        self._word_store = HOVERED_WORD_STATEFUL_BINDING.store(ipc)
        self._hover_store.dispatch(events.HoverConfigured(delays))
        self._metadata = hover_metadata.InteractionMetadataState()
        metadata_submitter = hover_metadata.configure_runtime_job(ipc)
        self._engaged = tooltip_engaged.EngagedState()
        self._engaged_backend = tooltip_engaged.PortsEngagedBackend(build)
        engaged_submitter = tooltip_engaged.configure_runtime_job(ipc, self._engaged_backend)
        self._raster = tooltip_raster.RenderAheadState()
        render_ahead_submitter = tooltip_raster.configure_runtime_job(ipc)
        jobs = TooltipRuntimeJobs(metadata_submitter, engaged_submitter, render_ahead_submitter)
        if runtime_jobs is not None:
            jobs = runtime_jobs(jobs)
        self._metadata_submitter = jobs.metadata
        self._engaged_submitter = jobs.engaged
        self._render_ahead_submitter = jobs.render_ahead

    def surface_state(self) -> TooltipState:
        """Mutable paint machinery exposed only to the physical surface boundary."""
        return self._state

    def observation(self) -> TooltipObservation:
        """Capture the Tooltip-owned facts safe for cross-feature decisions."""
        word = self._word_store.current
        return TooltipObservation(
            selected=self._selected,
            pause_enabled=self._pause_enabled,
            navigation_depth=len(self._nav_store.current.back),
            can_go_back=self._nav_store.current.can_go_back,
            pulse=self._pulse_store.current,
            pause=self._pause_store.current,
            reading=word.reading,
            kanji_index=word.kanji,
            metadata=hovered_meta(self._word_store),
        )

    def hover_diagnostics(self) -> HoverDiagnostics:
        hysteresis = self._hover_store.current.hysteresis
        return HoverDiagnostics(
            word_target=hysteresis.word_target,
            nested_hide_pending=hysteresis.nest_hide_pending,
        )

    def hover_view(self) -> hover_snapshot.HoverView:
        """Capture the rendered hover stack without publishing its mutable containers."""
        hysteresis = self._hover_store.current.hysteresis
        return hover_snapshot.snapshot(
            self._state.nest,
            self._state.view,
            paused=self._pause_store.current.held,
            scan_target=hysteresis.scan_target,
            hide_pending=hysteresis.tip_hide_pending,
        )

    @staticmethod
    def _surface_click(target: ClickTarget, _x: float, _y: float) -> bool:
        tooltip.on_click(target.tip, target.panel, target.click, target.hover)
        return True

    @staticmethod
    def _surface_scroll(wheel: WheelStep, steps: int) -> bool:
        wheel.scroll_tip(tip_wheel_pixels(wheel.tip_ref_h, steps))
        return True

    def surface_binding(self) -> SurfaceSpec:
        return SurfaceSpec(
            "tooltip",
            state_of=self.surface_state,
            on_click=self._surface_click,
            scroll=self._surface_scroll,
        )

    def select(self, index: int) -> None:
        self._selected = index

    def retire_selection(self) -> None:
        self.select(-1)

    def advance_kanji(self) -> None:
        self._word_store.dispatch(events.HoverKanjiAdvanced())

    def set_pause_enabled(self, *, enabled: bool) -> None:
        self._pause_enabled = enabled

    @property
    def delays(self) -> HoverDelays:
        return self._delays

    def configure_delays(
        self,
        *,
        scan: float | None = None,
        hide: float | None = None,
        switch: float | None = None,
    ) -> None:
        self._delays = replace(
            self._delays,
            scan=self._delays.scan if scan is None else scan,
            hide=self._delays.hide if hide is None else hide,
            switch=self._delays.switch if switch is None else switch,
        )
        self._hover_store.dispatch(events.HoverConfigured(self._delays))

    @property
    def flash_seconds(self) -> float:
        return self._flash_seconds

    def build_tip_ports(self, presentation: TooltipPresentation) -> TipPorts:
        """Bind Tooltip-private state to fresh owner-thread presentation capabilities."""
        return TipPorts(
            tip=self._state,
            scale=presentation.scale,
            surfaces=presentation.surfaces,
            hover_store=self._hover_store,
            nav_store=self._nav_store,
            pulse_store=self._pulse_store,
            pause_store=self._pause_store,
            word_store=self._word_store,
            request_render_ahead=presentation.request_render_ahead,
            osd=presentation.osd,
            nested_max_frac=presentation.nested_max_frac,
            peek_render_cache=presentation.peek_render_cache,
            schedule_flash_expiry=presentation.schedule_flash_expiry,
            toast=presentation.toast,
            request_engaged_tooltip=presentation.request_engaged_tooltip,
        )

    def build_panel_ports(
        self,
        *,
        style: PanelStyle,
        mined_set: Collection[str],
        during_scroll: bool,
        cap: int,
    ) -> PanelPorts:
        """Bind Tooltip's cache to the fresh facts for one panel build."""
        return tooltip_panel.PanelPorts(
            style, mined_set, during_scroll, self._state.panel_cache, cap
        )

    def has_cached_panel(self, key: object) -> bool:
        return key in self._state.panel_cache

    @property
    def keybindings_bound(self) -> bool:
        return self._key_context.bound

    def claim_keybindings(self) -> bool:
        return self._key_context.claim()

    def release_keybindings(self) -> bool:
        return self._key_context.release()

    def retire_state(self) -> None:
        """Clear mutable tooltip facts after their physical surfaces and keys retire."""
        self._state.view.rect = None
        self._state.view.state = None
        self._state.view.key = None
        self._state.tip_tok = self._state.tip_inflected = None
        self._nav_store.dispatch(events.TipNavCleared())
        self._word_store.dispatch(events.HoverWordForgotten())

    def retire_episode(self) -> None:
        self._hover_store.dispatch(events.EpisodeRetired())

    @staticmethod
    def mark_mined(expression: str, apply: TooltipApply) -> None:
        mined_feedback.mark_mined(
            apply.ports,
            apply.panel,
            apply.hover,
            apply.show,
            expression,
        )

    def expire_pulse(self) -> tuple[Repaint, ...]:
        return self._pulse_store.dispatch(events.CopyPulseExpired())

    def release_pause_claim(self) -> bool:
        return tooltip.release_frame(self._pause_store)

    def cache_setdefault(self, key: PanelKey, panel: Panel) -> Panel:
        return self._state.panel_cache.setdefault(key, panel)

    @property
    def cache_limit(self) -> int:
        return self._state.panel_cache.limit

    def cache_totals(self) -> tuple[int, int]:
        with self._cache_lock:
            return (
                len(self._state.panel_cache),
                sum(panel.retained_nbytes for panel in self._state.panel_cache.values()),
            )

    @property
    def metadata_deferred(self) -> bool:
        return self._metadata_submitter is not None

    def request_metadata(
        self,
        request: hover_metadata.MetadataRequest,
        on_finished,
    ) -> bool:
        return hover_metadata.submit(
            self._metadata,
            request,
            self._metadata_submitter,
            on_finished,
        )

    def finish_metadata(
        self,
        completion: EffectFinished,
        apply_factory: Callable[[], TooltipApply],
        on_finished,
    ) -> None:
        result = hover_metadata.finish(self._metadata, completion)
        try:
            if isinstance(result, hover_metadata.HoverMetadata):
                apply = apply_factory()
                tooltip.apply_hover_metadata(
                    apply.ports,
                    apply.panel,
                    apply.lookup,
                    apply.hover,
                    apply.show,
                    result,
                )
            elif isinstance(result, hover_metadata.NestedMetadata):
                apply = apply_factory()
                nested_popup.apply_nested_metadata(apply.ports, apply.panel, apply.lookup, result)
        finally:
            hover_metadata.finish_publication(self._metadata)
            hover_metadata.submit_pending(
                self._metadata,
                self._metadata_submitter,
                on_finished,
            )

    def request_render_ahead(
        self,
        view,
        direction: int,
        *,
        generation: int,
        scale: float,
        on_finished,
    ) -> bool:
        panel = view.state
        if panel is None:
            return False
        return tooltip_raster.request(
            self._raster,
            tooltip_raster.RenderAheadRequest(
                panel,
                view.desired_scroll,
                view.view_h,
                direction,
                scale,
                threading.Event(),
            ),
            generation=generation,
            job_id=view.job_id,
            submit=self._render_ahead_submitter,
            on_finished=on_finished,
        )

    def finish_render_ahead(
        self,
        completion: EffectFinished,
        *,
        generation: int,
        ports: TipPorts,
        on_finished,
    ) -> None:
        finished = tooltip_raster.finish(
            self._raster,
            completion,
            self._render_ahead_submitter,
            on_finished,
        )
        if finished is None:
            return
        identity, request, succeeded = finished
        if identity.generation != generation:
            return
        for view in (self._state.view, self._state.nest):
            if (
                view.state is request.panel
                and view.desired_scroll == identity.scroll
                and view.job_id == identity.job_id
            ):
                if succeeded:
                    tooltip_panel.apply_pending_scroll(ports, view)
                else:
                    view.desired_scroll = view.scroll
                    self._state.jobs.finish("scroll", "failed", job_id=identity.job_id)
                break
        if not succeeded:
            return
        for view in (self._state.view, self._state.nest):
            tooltip_panel.apply_pending_crisp(ports, view)

    def request_engaged(
        self,
        request: tooltip_engaged.EngagedRequest,
        *,
        generation: int,
        on_finished,
    ) -> bool:
        return tooltip_engaged.submit(
            self._engaged,
            request,
            generation=generation,
            submitter=self._engaged_submitter,
            on_finished=on_finished,
        )

    def finish_engaged(
        self,
        completion: EffectFinished,
        *,
        generation: int,
        apply_factory: Callable[[], TooltipApply],
        on_finished,
    ) -> None:
        finished = tooltip_engaged.finish(
            self._engaged,
            completion,
            self._engaged_submitter,
            on_finished,
        )
        if finished is None:
            return
        identity, request, result, succeeded, superseded, rejected = finished
        apply: TooltipApply | None = None
        if rejected is not None:
            rejected_identity, rejected_request = rejected
            if rejected_identity.generation == generation:
                apply = apply_factory()
                self._fallback_engaged(rejected_request, apply)
        if identity.generation != generation or superseded:
            return
        if isinstance(request, tooltip_engaged.HoverRequest) and not succeeded:
            self._state.jobs.finish("tooltip", "failed", job_id=request.job_id)
            return
        apply = apply or apply_factory()
        if not succeeded:
            self._fallback_engaged(request, apply)
            return
        self._apply_engaged(result, apply)

    def _fallback_engaged(
        self,
        request: tooltip_engaged.EngagedRequest,
        apply: TooltipApply,
    ) -> None:
        if isinstance(request, tooltip_engaged.NavigateRequest | tooltip_engaged.OpenRequest) and (
            request.origin != id(self._state.view.state)
        ):
            return
        try:
            result = tooltip_engaged.run_engaged(
                tooltip_engaged.EngagedWork(request, threading.Event()),
                threading.Event(),
                self._engaged_backend,
            )
        except Exception:
            log.warning("engaged tooltip fallback failed", exc_info=True)
            return
        self._apply_engaged(result, apply)

    @staticmethod
    def _apply_engaged(result, apply: TooltipApply) -> None:
        if isinstance(result, tooltip_engaged.HoverReady):
            tooltip.apply_engaged_hover(apply.ports, apply.panel, apply.hover, apply.show, result)
        elif isinstance(result, tooltip_engaged.NavigateReady):
            tooltip.apply_engaged_nav(apply.ports, result)
        elif isinstance(result, tooltip_engaged.OpenReady):
            tooltip.apply_engaged_open(apply.ports, apply.panel, result)

    def run_engaged(self, work: tooltip_engaged.EngagedWork):
        """Execute submitted work synchronously through the runtime lane's backend."""
        return tooltip_engaged.run_engaged(
            work,
            threading.Event(),
            self._engaged_backend,
        )

    def publish_pending(self, ports: TipPorts) -> None:
        """Apply any viewport whose bands became warm during this turn."""
        for view in (self._state.view, self._state.nest):
            tooltip_panel.apply_pending_scroll(ports, view)
            tooltip_panel.apply_pending_crisp(ports, view)

    def cancel_current_work(self) -> None:
        tooltip_engaged.cancel(self._engaged)
        tooltip_raster.cancel(self._raster)

    def invalidate_dependencies(self) -> None:
        """Refuse work and cached panels built against replaced collaborators."""
        self.cancel_current_work()
        self._state.panel_cache.clear()

    def cancel_jobs(self) -> None:
        self._state.jobs.cancel_all()

    def close_metadata(self) -> None:
        hover_metadata.close(self._metadata)

    def close_render_ahead(self) -> None:
        tooltip_raster.close(self._raster)

    def close_engaged(self) -> None:
        tooltip_engaged.close(self._engaged)
