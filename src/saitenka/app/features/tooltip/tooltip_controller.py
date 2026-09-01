"""Own the tooltip stack's mutable state and volatile background work."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from saitenka_tokenize.languages import MAIN_LANG

from saitenka import otel_metrics
from saitenka.app import subtitles
from saitenka.app.bindings import active_bindings
from saitenka.app.feature_bindings import (
    HOVER_PAUSE_STATEFUL_BINDING,
    HOVER_STATEFUL_BINDING,
    HOVERED_WORD_STATEFUL_BINDING,
    PULSE_STATEFUL_BINDING,
    TIP_NAV_STATEFUL_BINDING,
)
from saitenka.app.features.mining import mine_intents
from saitenka.app.features.tooltip import (
    hover_intents,
    hover_metadata,
    hover_snapshot,
    mined_feedback,
    nested_popup,
    prefetch,
    tooltip,
    tooltip_engaged,
    tooltip_panel,
    tooltip_raster,
)
from saitenka.app.features.tooltip.navigation_endpoint import TooltipNavigationEndpoint
from saitenka.app.features.tooltip.popups import (
    ClickPorts,
    HoverActions,
    HoverInputs,
    ShowActions,
    TipPorts,
    TooltipState,
    WordLookup,
    hovered_meta,
)
from saitenka.app.features.tooltip.preparation import TooltipPreparationInputs
from saitenka.app.interaction.surfaces import (
    ClickTarget,
    SurfaceSpec,
    WheelStep,
    tip_wheel_pixels,
)
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.runtime import Owner, events

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from saitenka_tokenize.registry import Tokenizer

    from saitenka.app.config import KeyOptions, TooltipOptions
    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.features.annotation.annotation_controller import AnnotationView
    from saitenka.app.features.help.help_controller import (
        HelpController,
        ScreenState,
        TooltipKeyContext,
    )
    from saitenka.app.features.mining.mining_controller import MiningIndexSnapshot
    from saitenka.app.features.tooltip.popups import (
        HoverMetadata as HoverMetadataView,
    )
    from saitenka.app.features.tooltip.popups import (
        Panel,
        PopupView,
    )
    from saitenka.app.features.tooltip.prefetch import TipScale
    from saitenka.app.features.tooltip.preparation import TooltipPreparationController
    from saitenka.app.features.tooltip.tooltip_panel import PanelKey, PanelPorts, PanelStyle
    from saitenka.app.interaction.presentation import InteractionSurfaces
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.render_cache import LoadedView
    from saitenka.app.scoring import Coloring
    from saitenka.app.subtitle_presentation import CueRenderState
    from saitenka.render.layout_backend import LayoutBackend
    from saitenka.runtime import EffectFinished
    from saitenka.runtime.hover import HoverDelays
    from saitenka.runtime.hover_pause import PauseClaim
    from saitenka.runtime.jobs import JobSubmitter
    from saitenka.runtime.pulse import PulseState, Repaint
    from saitenka.subtitles import CueIndex

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
class TooltipControllerConfig:
    panel_cache_max: int
    pause_enabled: bool
    delays: HoverDelays
    flash_seconds: float
    key_context: TooltipKeyContext
    visual: TooltipVisualSettings


@dataclass(slots=True)
class TooltipVisualSettings:
    base_height_fraction: float
    nested_height_fraction: float
    stroke_order: bool
    band_limit: int
    raw_band_bytes: int
    crisp: bool
    scale_override: float
    backend: LayoutBackend
    backend_name: str

    def scale(self, osd_height: int) -> TipScale:
        return prefetch.tip_scale(
            osd_height,
            override=self.scale_override,
            max_frac=self.base_height_fraction,
        )

    @staticmethod
    def cap_for(fraction: float) -> int:
        return prefetch.cap_for(fraction)

    @classmethod
    def from_options(cls, options: TooltipOptions) -> TooltipVisualSettings:
        from saitenka.render.layout_backend import backend_label, resolve_backend

        backend = resolve_backend(options.layout_engine)
        return cls(
            options.tip_max_frac,
            options.nested_max_frac,
            options.kanji_stroke_order,
            options.band_cache_max,
            options.raw_band_ceiling_mb * 1024 * 1024,
            options.crisp_upscale,
            options.tip_scale,
            backend,
            backend_label(backend),
        )


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


@dataclass(frozen=True, slots=True)
class TooltipNavigationView:
    sub_index: CueIndex | None
    nav_idx: int


@dataclass(frozen=True, slots=True)
class TooltipSessionView:
    """Immutable peer facts sampled together for one tooltip operation."""

    cue: CueRenderState
    annotation: AnnotationView
    tokenizer: Tokenizer
    dictionary: DictionarySet | None
    scorer: Coloring | None
    mined: MiningIndexSnapshot
    mining_target_available: bool
    subtitle_language: str
    navigation: TooltipNavigationView
    playback_cue_text: str


@dataclass(frozen=True, slots=True)
class TooltipSessionActions:
    """The named cross-feature effects tooltip policy may request."""

    hide: Callable[[], None]
    set_annotation_hover: Callable[..., None]
    draw_cue: Callable[[], None]
    mine_token: Callable[..., None]
    preview_click: Callable[[float, float], bool]
    run_hover_command: Callable[[object], None]
    run_mine_command: Callable[[object], None]
    sync_translation: Callable[[], None]
    record_lookup: Callable[..., None]
    toast: Callable[..., None]
    tts_available: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class TooltipSessionContext:
    """Fresh observations, bounded effects, and the tooltip's physical surface."""

    observe: Callable[[], TooltipSessionView]
    read_playback: Callable[[str], object]
    query_playback: Callable[[str], object | None]
    actions: TooltipSessionActions
    surfaces: InteractionSurfaces


class TooltipController:
    """Own tooltip state, work admission, stale refusal, fallback, and publication."""

    def __init__(
        self,
        ipc,
        preparation: TooltipPreparationController,
        screen: ScreenState,
        timers: LifecycleTimers,
        keys: KeyOptions,
        help_controller: HelpController,
        *,
        config: TooltipControllerConfig,
        runtime_jobs: Callable[[TooltipRuntimeJobs], TooltipRuntimeJobs] | None = None,
    ) -> None:
        self._cache_lock = threading.Lock()
        self._screen = screen
        self._timers = timers
        self._ipc = ipc
        self._preparation = preparation
        self._keys = keys
        self._help = help_controller
        self._session_context: TooltipSessionContext | None = None
        self._mouse_engaged = False
        self._scrolled_this_turn = False
        self._state = TooltipState(
            panel_cache_max=config.panel_cache_max,
            cache_lock=self._cache_lock,
        )
        self._selected = -1
        self._pause_enabled = config.pause_enabled
        self._delays = config.delays
        self._flash_seconds = config.flash_seconds
        self._key_context = config.key_context
        self.visual = config.visual
        self._hover_store = HOVER_STATEFUL_BINDING.store(ipc)
        self._nav_store = TIP_NAV_STATEFUL_BINDING.store(ipc)
        self._pulse_store = PULSE_STATEFUL_BINDING.store(ipc)
        self._pause_store = HOVER_PAUSE_STATEFUL_BINDING.store(ipc)
        self._word_store = HOVERED_WORD_STATEFUL_BINDING.store(ipc)
        self._hover_store.dispatch(events.HoverConfigured(config.delays))
        self._metadata = hover_metadata.InteractionMetadataState()
        metadata_submitter = hover_metadata.configure_runtime_job(ipc)
        self._engaged = tooltip_engaged.EngagedState()
        self._engaged_backend = tooltip_engaged.PortsEngagedBackend(
            tooltip_engaged.EngagedBuildPorts(
                nested_max_frac=self.visual.nested_height_fraction,
                prepare_hover=self._preparation.prepare_engaged,
                cap_for=self.visual.cap_for,
                navigated_panel=self.navigated_panel,
                engaged_open_panel=self.engaged_open_panel,
            )
        )
        engaged_submitter = tooltip_engaged.configure_runtime_job(ipc, self._engaged_backend)
        self._raster = tooltip_raster.RenderAheadState()
        render_ahead_submitter = tooltip_raster.configure_runtime_job(ipc)
        jobs = TooltipRuntimeJobs(metadata_submitter, engaged_submitter, render_ahead_submitter)
        if runtime_jobs is not None:
            jobs = runtime_jobs(jobs)
        self._metadata_submitter = jobs.metadata
        self._engaged_submitter = jobs.engaged
        self._render_ahead_submitter = jobs.render_ahead

    def bind_session_context(self, context: TooltipSessionContext) -> None:
        """Complete the feature's cyclic session wiring exactly once before the session starts."""
        if self._session_context is not None:
            raise RuntimeError("tooltip session context already bound")
        self._session_context = context

    def _session(self) -> TooltipSessionContext:
        context = self._session_context
        if context is None:
            raise RuntimeError("tooltip session context is not bound")
        return context

    def begin_turn(self) -> None:
        self._scrolled_this_turn = False

    def hit(self, mx: float, my: float) -> int:
        context = self._session()
        view = context.observe()
        cue = view.cue
        return subtitles.token_at(
            cue.boxes,
            (mx, my),
            cue.origin,
            is_skippable=lambda index: view.tokenizer.is_skippable(cue.tokens[index]),
        )

    def set_hover(self, index: int) -> None:
        tooltip.set_hover(
            self.tip_ports,
            self.panel_ports,
            self.word_lookup,
            self.hover_inputs,
            self.show_actions,
            index,
        )

    def update_hover(self) -> None:
        """Apply the current pointer observation through the tooltip's hover policy."""
        tooltip.update_hover(self.tip_ports, self.hover_actions, self.hover_inputs)

    def retire_hover(self) -> None:
        tooltip.retire_hover(self.tip_ports, self.hover_inputs, self.show_actions)

    def prepare_hover_blocking(self, index: int) -> None:
        self.set_hover(index)

    def set_annotation_hover(self, *, revealed: bool) -> None:
        context = self._session()
        view = context.observe()
        target = bool(
            revealed
            and view.annotation.mode == "hover"
            and view.subtitle_language == MAIN_LANG
            and view.cue.tokens
        )
        if target == view.annotation.hover_revealed:
            return
        context.actions.set_annotation_hover(revealed=target)

    def copy_token(self, token) -> None:
        tooltip.copy_token(self._session().actions.toast, token)

    def copy_click(self) -> None:
        tooltip.copy_click(self.tip_ports, self.click_ports, self.hover_inputs)

    def _observed_mouse_position(self) -> dict | None:
        value = self._session().read_playback("mouse-pos")
        return value if isinstance(value, dict) else None

    def _current_mouse_position(self) -> dict | None:
        value = self._session().query_playback("mouse-pos")
        return value if isinstance(value, dict) else None

    @property
    def hover_actions(self) -> HoverActions:
        return HoverActions(
            arm=lambda kind, delay, intent: self.arm_deadline(
                kind,
                delay,
                lambda: tooltip._dwell_elapsed(self.tip_ports, self.hover_actions, intent),
            ),
            cancel=self.cancel_deadline,
            show_word=self.set_hover,
            retire_word=self.retire_hover,
            open_nested=lambda scan: nested_popup.show_nested(
                self.tip_ports, self.panel_ports, self.word_lookup, scan
            ),
            reveal_annotation=lambda revealed: self.set_annotation_hover(revealed=revealed),
            publish_engagement=lambda inside: setattr(self, "_mouse_engaged", inside),
        )

    @property
    def click_ports(self) -> ClickPorts:
        context = self._session()
        return ClickPorts(
            mine_token=context.actions.mine_token,
            mine_current=lambda: context.actions.run_mine_command(mine_intents.MineCommand.WORD),
            speak_hovered=lambda: context.actions.run_hover_command(
                hover_intents.HoverCommand.SPEAK
            ),
            click_preview=context.actions.preview_click,
            cursor=self._current_mouse_position,
            paused=lambda: context.read_playback("pause"),
        )

    @property
    def hover_inputs(self) -> HoverInputs:
        context = self._session()
        cue = context.observe().cue
        return HoverInputs(
            mouse_pos=self._observed_mouse_position,
            hit=self.hit,
            hover=lambda: self.observation().selected,
            cue_state=self.cue_state,
            tokens=cue.tokens,
            boxes=cue.boxes,
            sub_origin=cue.origin,
        )

    @property
    def word_lookup(self) -> WordLookup:
        view = self._session().observe()
        annotation = view.annotation
        return WordLookup(
            tokenizer=view.tokenizer,
            dict_set=view.dictionary,
            mined=view.mined,
            prefetch_gen=self._preparation.generation,
            dependency_gen=annotation.dependency_generation,
            cue_identity=annotation.identity,
            deferred=self.metadata_deferred,
            submit=self.request_interaction_metadata,
        )

    def apply_context(self) -> TooltipApply:
        return TooltipApply(
            ports=self.tip_ports,
            panel=self.panel_ports,
            lookup=self.word_lookup,
            hover=self.hover_inputs,
            show=self.show_actions,
            generation=self._preparation.generation,
        )

    @property
    def show_actions(self) -> ShowActions:
        context = self._session()
        return ShowActions(
            select=self.select,
            draw_cue=context.actions.draw_cue,
            teardown=self.teardown,
            bind_keys=self.bind_keybindings,
            seed_precomposed=lambda panel, key, cap: self._preparation.cache.seed_precomposed(
                self.preparation_inputs, panel, key, cap
            ),
            freeze=lambda *, already_paused: tooltip._freeze_frame(
                self._ipc,
                context.read_playback,
                enabled=self.observation().pause_enabled,
                already_paused=already_paused,
            ),
            inflected=self.inflected_surface,
            sync_translation=context.actions.sync_translation,
            record_lookup=context.actions.record_lookup,
        )

    def cue_state(self) -> str:
        view = self._session().observe()
        if not view.playback_cue_text.strip():
            return "empty"
        annotation = view.annotation
        if annotation.retired:
            return "retired"
        return "pending" if annotation.pending_text is not None else "ready"

    def teardown(self) -> None:
        context = self._session()
        self.cancel_jobs()
        context.actions.hide()
        self.hide_nested()
        self.unbind_keybindings()
        self.retire_state()
        self.resume_after_hover_pause()
        context.actions.sync_translation()

    def resume_after_hover_pause(self) -> None:
        if not self.release_pause_claim():
            return
        send_correlated(
            self._ipc,
            "hover-pause-resume",
            "set_property",
            "pause",
            False,  # noqa: FBT003  # mpv IPC wire value
            owner=Owner.PLAYBACK,
        )

    @property
    def can_go_back(self) -> bool:
        return self.observation().can_go_back

    def panel_key(
        self,
        token,
        inflected,
        *,
        mined: bool = False,
        phrase: tuple[str, ...] = (),
        group_mined: tuple[bool, ...] | None = None,
    ) -> PanelKey:
        return tooltip_panel.panel_key(
            self.panel_ports,
            token,
            inflected,
            mined=mined,
            phrase=phrase,
            group_mined=group_mined,
        )

    @property
    def panel_style(self) -> PanelStyle:
        context = self._session()
        view = context.observe()
        return tooltip_panel.PanelStyle(
            width=self.scale().width,
            band_cache_max=self.visual.band_limit,
            raw_band_ceiling=self.visual.raw_band_bytes,
            layout_backend=self.visual.backend,
            layout_engine=self.visual.backend_name,
            add_button=view.mining_target_available,
            speak_button=context.actions.tts_available(),
            dict_set=view.dictionary,
            scorer=view.scorer,
            tokenizer=view.tokenizer,
            kanji_stroke_order=self.visual.stroke_order,
        )

    @property
    def panel_ports(self) -> PanelPorts:
        view = self._session().observe()
        return self.build_panel_ports(
            style=self.panel_style,
            mined_set=view.mined,
            during_scroll=self._scrolled_this_turn,
            cap=self.scale().cap,
        )

    @property
    def preparation_inputs(self) -> TooltipPreparationInputs:
        view = self._session().observe()
        return TooltipPreparationInputs(
            panels=self.panel_ports,
            dictionary=view.dictionary,
        )

    def panel_for(
        self,
        token,
        inflected=None,
        min_h: int | None = None,
        *,
        mined: bool | None = None,
        nested: bool = False,
        extra_terms: tuple[str, ...] = (),
        group_mined: tuple[bool, ...] | None = None,
    ):
        return tooltip_panel.panel_for(
            self.panel_ports,
            token,
            inflected,
            min_h,
            mined=mined,
            nested=nested,
            extra_terms=extra_terms,
            group_mined=group_mined,
        )

    def is_mined(self, token) -> bool:
        return tooltip_panel.is_mined(token, self._session().observe().mined)

    @property
    def prefetch_ports(self) -> prefetch.PrefetchPorts:
        view = self._session().observe()
        navigation = view.navigation
        cue = view.cue
        return prefetch.PrefetchPorts(
            enabled=bool(self._preparation.config.enabled and view.dictionary is not None),
            engaged=bool(self._session().read_playback("pause")) or self._mouse_engaged,
            cues=prefetch.LookaheadCues(
                navigation.sub_index,
                view.playback_cue_text,
                navigation.nav_idx,
                self._preparation.config.cue_lookahead,
            ),
            tokens=cue.tokens,
            styles=cue.styles,
            tokenizer=view.tokenizer,
            inflected=self.inflected_surface,
            is_mined=self.is_mined,
        )

    @property
    def head_probe(self) -> prefetch.HeadProbe:
        view = self._session().observe()
        return prefetch.HeadProbe(
            scorer=view.scorer,
            panel_key=self.panel_key,
            panel_present=self.has_cached_panel,
            lookahead=self._preparation.config.head_lookahead,
            queue_max=self._preparation.config.head_queue_max,
        )

    def start_prefetch(self) -> int:
        view = self._session().observe()
        self._preparation.start(
            self._ipc,
            view.tokenizer,
            dictionary_available=view.dictionary is not None,
        )
        return self._preparation.worker_count

    def update_prefetch(self) -> None:
        if self._preparation.update(
            self.prefetch_ports,
            self.head_probe,
            self.preparation_inputs,
            self.finish_speculative_prefetch,
        ):
            self.cancel_current_work()

    def finish_speculative_prefetch(self, completion: EffectFinished) -> None:
        self._preparation.finish(completion, self.finish_speculative_prefetch)

    def inflected_surface(self, index: int) -> str:
        view = self._session().observe()
        return view.tokenizer.inflected_in(view.cue.tokens, index)

    @property
    def navigation_endpoint(self) -> TooltipNavigationEndpoint:
        return TooltipNavigationEndpoint(
            screen=self._screen,
            tip_scale_override=self.visual.scale_override,
            tip_max_frac=self.visual.base_height_fraction,
            observe_can_go_back=lambda: self.observation().can_go_back,
            navigate_back=lambda: tooltip.tip_back(self.tip_ports),
        )

    @property
    def tip_ports(self) -> TipPorts:
        context = self._session()
        return self.build_tip_ports(
            TooltipPresentation(
                scale=self.navigation_endpoint.scale(),
                surfaces=context.surfaces,
                request_render_ahead=self.submit_render_ahead,
                osd=self._screen.osd,
                nested_max_frac=self.visual.nested_height_fraction,
                peek_render_cache=lambda key: self._preparation.cache.peek(
                    self.preparation_inputs, key
                ),
                schedule_flash_expiry=self.schedule_flash_expiry,
                toast=context.actions.toast,
                request_engaged_tooltip=self.request_engaged_tooltip,
            )
        )

    def request_interaction_metadata(self, request) -> bool:
        return self.request_metadata(request, self.finish_interaction_metadata)

    def finish_interaction_metadata(self, completion: EffectFinished) -> None:
        self.finish_metadata(
            completion,
            self.apply_context,
            self.finish_interaction_metadata,
        )

    def submit_render_ahead(self, view: PopupView, direction: int) -> bool:
        return self.request_render_ahead(
            view,
            direction,
            generation=self._preparation.generation,
            scale=self.scale().raster,
            on_finished=self.finish_render_ahead_completion,
        )

    def request_engaged_tooltip(self, request: tooltip_engaged.EngagedRequest) -> bool:
        scale = self.scale()
        if isinstance(request, tooltip_engaged.HoverRequest):
            request = replace(
                request,
                panels=self.panel_ports if request.panels is None else request.panels,
                scale=scale if request.scale is None else request.scale,
            )
        elif (isinstance(request, tooltip_engaged.NavigateRequest) and request.scale is None) or (
            isinstance(request, tooltip_engaged.OpenRequest) and request.scale is None
        ):
            request = replace(request, scale=scale)
        return self.request_engaged(
            request,
            generation=self._preparation.generation,
            on_finished=self.finish_engaged_tooltip,
        )

    def finish_engaged_tooltip(self, completion: EffectFinished) -> None:
        self.finish_engaged(
            completion,
            generation=self._preparation.generation,
            apply_factory=self.apply_context,
            on_finished=self.finish_engaged_tooltip,
        )

    def finish_render_ahead_completion(self, completion: EffectFinished) -> None:
        self.finish_render_ahead(
            completion,
            generation=self._preparation.generation,
            ports=self.tip_ports,
            on_finished=self.finish_render_ahead_completion,
        )

    def schedule_flash_expiry(self) -> bool:
        return self._timers.schedule(
            LifecycleTimerKind.FLASH_EXPIRY,
            self.flash_seconds,
            self._flash_expired,
        )

    def _flash_expired(self) -> None:
        for decision in self.expire_pulse():
            if decision.overlay == OverlayId.NESTED:
                self.render_nested_view()
            elif decision.overlay == OverlayId.TIP:
                self.render_tip_view()

    def show_tooltip(self, index: int) -> None:
        tooltip.show_tooltip(
            self.tip_ports,
            self.panel_ports,
            self.hover_inputs,
            self.show_actions,
            index,
        )

    def render_tip_view(self) -> None:
        tooltip_panel.render_view(self.tip_ports, self._state.view)

    def render_nested_view(self) -> None:
        tooltip_panel.render_view(self.tip_ports, self._state.nest)

    def scroll_tip(self, delta: int) -> None:
        self._scrolled_this_turn = True
        with otel_metrics.instrumented_jank(
            otel_metrics.scroll_frame_duration_ms,
            otel_metrics.scroll_frame_jank,
            otel_metrics.SCROLL_JANK_THRESHOLD_MS,
            "scroll_frame",
            layout_backend=self.visual.backend_name,
        ) as span:
            tooltip.scroll_tip(self.tip_ports, self.hover_actions, delta)
            state = self._state.view.state
            if state is None:
                return
            span.set("bands", state.last_frame_rasters)
            span.set("full_h", state.full_height)
            view = self._state.view
            span.set("scroll", view.scroll)
            span.set("desired", view.desired_scroll)
            viewport_height = min(view.view_h, state.full_height)
            span.set("warm", state.viewport_warm(view.desired_scroll, viewport_height))
            span.set(
                "native_warm",
                state.native_viewport_warm(
                    view.desired_scroll,
                    viewport_height,
                    self.scale().raster,
                ),
            )
            span.set("scale", f"{self.scale().display:.4f}")
            span.set("crisp_miss", view.crisp_miss or "n/a")

    def navigated_panel(self, query: str):
        return tooltip._navigated_panel(self.panel_style, query)

    def engaged_open_panel(self, source: str, query: str, *, mined: bool | None = None):
        return nested_popup._engaged_open_panel(
            self.tip_ports,
            self.panel_ports,
            source,
            query,
            mined=mined,
        )

    def open_kanji(self, char: str, x: float, y: float, height: float) -> None:
        nested_popup.open_kanji(self.tip_ports, self.panel_ports, char, x, y, height)

    def kanji_current(self) -> None:
        self._session().actions.run_hover_command(hover_intents.HoverCommand.KANJI)

    def hide_nested(self) -> None:
        nested_popup.hide_nested(self.tip_ports)

    def scale(self) -> TipScale:
        return self.visual.scale(self._screen.osd[1])

    def cap_for(self, fraction: float) -> int:
        return self.visual.cap_for(fraction)

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

    @property
    def selected_index(self) -> int:
        """The currently selected cue token, without exposing the full hover snapshot."""
        return self._selected

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

    def arm_deadline(self, kind, seconds: float, due) -> bool:
        return self._timers.schedule(kind, seconds, due)

    def cancel_deadline(self, kind) -> None:
        self._timers.cancel(kind)

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

    def bind_keybindings(self) -> None:
        if not self.claim_keybindings() or self._help.state.open:
            return
        for binding in active_bindings(self._keys, "tooltip"):
            self._ipc.command_async(
                "keybind",
                binding.key,
                f"script-message {binding.spec.message}",
            )

    def unbind_keybindings(self) -> None:
        if not self.release_keybindings() or self._help.state.open:
            return
        for binding in active_bindings(self._keys, "tooltip"):
            self._ipc.command_async("keybind", binding.key, "ignore")

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

    def publish_pending(self) -> None:
        """Apply any viewport whose bands became warm during this turn."""
        for view in (self._state.view, self._state.nest):
            tooltip_panel.apply_pending_scroll(self.tip_ports, view)
            tooltip_panel.apply_pending_crisp(self.tip_ports, view)

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
