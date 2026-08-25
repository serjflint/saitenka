"""Own the tooltip stack's mutable state and volatile background work."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
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
    nested_popup,
    tooltip,
    tooltip_engaged,
    tooltip_panel,
    tooltip_raster,
)
from saitenka.app.features.tooltip.popups import HoverInputs, ShowActions, TipPorts, TooltipState
from saitenka.app.interaction.surfaces import (
    ClickTarget,
    SurfaceSpec,
    WheelStep,
    tip_wheel_pixels,
)
from saitenka.runtime import events

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from saitenka.app.features.help.help_controller import TooltipKeyContext
    from saitenka.app.features.tooltip.popups import Panel, WordLookup
    from saitenka.app.features.tooltip.tooltip_panel import PanelKey, PanelPorts
    from saitenka.runtime import EffectFinished
    from saitenka.runtime.hover import HoverDelays
    from saitenka.runtime.interaction_slice import (
        HoveredWordStore,
        HoverPauseStore,
        HoverStore,
        PulseStore,
        TipNavStore,
    )
    from saitenka.runtime.jobs import JobSubmitter
    from saitenka.runtime.pulse import Repaint

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
        self._metadata_submitter = hover_metadata.configure_runtime_job(ipc)
        self._engaged = tooltip_engaged.EngagedState()
        self._engaged_backend = tooltip_engaged.PortsEngagedBackend(build)
        self._engaged_submitter = tooltip_engaged.configure_runtime_job(ipc, self._engaged_backend)
        self._raster = tooltip_raster.RenderAheadState()
        self._render_ahead_submitter = tooltip_raster.configure_runtime_job(ipc)

    @property
    def state(self) -> TooltipState:
        return self._state

    def surface_state(self) -> TooltipState:
        return self.state

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

    @property
    def metadata(self) -> hover_metadata.InteractionMetadataState:
        return self._metadata

    @property
    def engaged(self) -> tooltip_engaged.EngagedState:
        return self._engaged

    @property
    def render_ahead(self) -> tooltip_raster.RenderAheadState:
        return self._raster

    @property
    def selected(self) -> int:
        return self._selected

    def select(self, index: int) -> None:
        self._selected = index

    def retire_selection(self) -> None:
        self.select(-1)

    @property
    def pause_enabled(self) -> bool:
        return self._pause_enabled

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

    @property
    def hover_store(self) -> HoverStore:
        return self._hover_store

    @property
    def nav_store(self) -> TipNavStore:
        return self._nav_store

    @property
    def pulse_store(self) -> PulseStore:
        return self._pulse_store

    @property
    def pause_store(self) -> HoverPauseStore:
        return self._pause_store

    @property
    def word_store(self) -> HoveredWordStore:
        return self._word_store

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
    def metadata_submitter(self) -> JobSubmitter | None:
        return self._metadata_submitter

    @metadata_submitter.setter
    def metadata_submitter(self, submitter: JobSubmitter | None) -> None:
        self._metadata_submitter = submitter

    @property
    def engaged_submitter(self) -> JobSubmitter | None:
        return self._engaged_submitter

    @engaged_submitter.setter
    def engaged_submitter(self, submitter: JobSubmitter | None) -> None:
        self._engaged_submitter = submitter

    @property
    def render_ahead_submitter(self) -> JobSubmitter | None:
        return self._render_ahead_submitter

    @render_ahead_submitter.setter
    def render_ahead_submitter(self, submitter: JobSubmitter | None) -> None:
        self._render_ahead_submitter = submitter

    @property
    def metadata_deferred(self) -> bool:
        return self._metadata_submitter is not None

    @contextmanager
    def blocking(self) -> Iterator[None]:
        """Run deterministic tooltip preparation without metadata/build deferral."""
        metadata, engaged = self._metadata_submitter, self._engaged_submitter
        self._metadata_submitter = None
        self._engaged_submitter = None
        try:
            yield
        finally:
            self._metadata_submitter = metadata
            self._engaged_submitter = engaged

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

    def cancel_jobs(self) -> None:
        self._state.jobs.cancel_all()

    def close_metadata(self) -> None:
        hover_metadata.close(self._metadata)

    def close_render_ahead(self) -> None:
        tooltip_raster.close(self._raster)

    def close_engaged(self) -> None:
        tooltip_engaged.close(self._engaged)
