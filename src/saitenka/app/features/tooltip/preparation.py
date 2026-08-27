"""Persistent and speculative preparation owned by the tooltip feature."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from saitenka.app import mask_atlas_startup
from saitenka.app.features.tooltip import prefetch, tooltip_panel
from saitenka.app.paths import cache_dir

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.features.tooltip.popups import Panel
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.features.tooltip.tooltip_engaged import HoverRequest
    from saitenka.app.features.tooltip.tooltip_panel import PanelPorts
    from saitenka.app.render_cache import CompressedHeadCache, LoadedView, RenderCache
    from saitenka.app.tokenizer import Tokenizer
    from saitenka.mask_atlas import MaskAtlas
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime import EffectFinished

log = logging.getLogger(__name__)

_MEM_TIER_MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TooltipPreparationConfig:
    enabled: bool
    workers: int
    cue_lookahead: int
    head_lookahead: int
    head_queue_max: int
    cache_enabled: bool
    cache_max_bytes: int
    cache_min_height: int
    mask_atlas_enabled: bool


@dataclass(frozen=True, slots=True)
class TooltipPreparationInputs:
    """Immutable facts one admitted preparation job may read."""

    panels: PanelPorts
    dictionary: DictionarySet | None


class PersistentHeadCache:
    """Own the tooltip's persistent head cache and mask-atlas handle."""

    def __init__(self, config: TooltipPreparationConfig) -> None:
        self.enabled = config.cache_enabled
        self.max_bytes = config.cache_max_bytes
        self.min_height = config.cache_min_height
        self.mask_atlas_enabled = config.mask_atlas_enabled
        self._cache: RenderCache | None = None
        self._memory: CompressedHeadCache | None = None
        if self.enabled:
            from saitenka.app.render_cache import CompressedHeadCache

            self._memory = CompressedHeadCache(max_bytes=min(self.max_bytes, _MEM_TIER_MAX_BYTES))
        self._opened = False
        self._signature: str | None = None
        self._signature_key: tuple[int, int] | None = None
        self._signature_dictionary: object | None = None
        self._mask_atlas: MaskAtlas | None = None
        self._state_lock = threading.Lock()

    @property
    def mask_atlas(self) -> MaskAtlas | None:
        return self._mask_atlas

    @property
    def memory_entries(self) -> int:
        return len(self._memory) if self._memory is not None else 0

    def install_build_cache(self, cache: RenderCache | None) -> None:
        """Install the cache explicitly built by the headless prewarm path."""
        with self._state_lock:
            self._cache = cache
            self._opened = True

    def set_min_height(self, value: int) -> None:
        """Configure an isolated preparation run without mutating session state."""
        self.min_height = value

    def invalidate_signature(self) -> None:
        with self._state_lock:
            self._signature = None
            self._signature_key = None
            self._signature_dictionary = None

    def _open(self, inputs: TooltipPreparationInputs) -> RenderCache | None:
        if not self.enabled or inputs.dictionary is None:
            return None
        with self._state_lock:
            if not self._opened:
                self._opened = True
                from saitenka.app.render_cache import RenderCache

                path = cache_dir() / "render-cache.sqlite"
                if path.exists():
                    self._cache = RenderCache.open(path, max_bytes=self.max_bytes)
            return self._cache

    def signature(self, inputs: TooltipPreparationInputs) -> str:
        panels = inputs.panels
        dictionary = inputs.dictionary
        if dictionary is None:
            raise ValueError("a dictionary is required for a render-cache signature")
        key = (panels.style.width, panels.cap)
        with self._state_lock:
            if (
                self._signature is None
                or self._signature_dictionary is not dictionary
                or self._signature_key != key
            ):
                from saitenka.app.render_cache import config_signature, dict_set_signature

                self._signature = config_signature(
                    width=panels.style.width,
                    cap=panels.cap,
                    dict_sig=dict_set_signature(dictionary),
                )
                self._signature_key = key
                self._signature_dictionary = dictionary
            return self._signature

    def peek(self, inputs: TooltipPreparationInputs, key: object) -> LoadedView | None:
        memory = self._memory
        if memory is None or len(memory) == 0:
            return None
        from saitenka.app.render_cache import content_key

        cached = memory.get((self.signature(inputs), content_key(cast("Sequence[object]", key))))
        if cached is None:
            return None
        try:
            return cached.inflate()
        except ValueError:
            return None

    def hydrate(self, inputs: TooltipPreparationInputs, key: object) -> bool:
        """Mirror one stored compressed head into the owner-thread memory tier."""
        cache = self._open(inputs)
        memory = self._memory
        if cache is None or memory is None:
            return False
        from saitenka.app.render_cache import content_key

        signature = self.signature(inputs)
        content = content_key(cast("Sequence[object]", key))
        cached = cache.peek_compressed(signature, content)
        if cached is None:
            return False
        memory.put((signature, content), cached)
        return True

    def seed_precomposed(
        self,
        inputs: TooltipPreparationInputs,
        panel: Panel,
        key: object,
        cap: int,
    ) -> bool:
        loaded = self.peek(inputs, key)
        if loaded is None:
            return False
        view_h = min(panel.full_height, cap)
        if view_h <= 0 or loaded.view_h != view_h or loaded.overscan != view_h:
            return False
        panel.windowed.install_first_view(loaded.view_h, loaded.overscan, loaded.array)
        return True

    def worker_seed_head(
        self,
        inputs: TooltipPreparationInputs,
        panel: Panel,
        token,
        inflected,
        *,
        mined: bool,
        cap: int,
    ) -> bool:
        cache = self._open(inputs)
        memory = self._memory
        if cache is None or memory is None:
            return False
        from saitenka.app.render_cache import content_key

        signature = self.signature(inputs)
        panel_key = tooltip_panel.panel_key(inputs.panels, token, inflected, mined=mined)
        content = content_key(panel_key)
        cached = cache.peek_compressed(signature, content)
        if cached is None:
            return False
        view_h = min(panel.full_height, cap)
        if view_h <= 0 or cached.view_h != view_h or cached.overscan != view_h:
            return False
        panel.windowed.install_first_view(cached.view_h, cached.overscan, cached.inflate().array)
        memory.put((signature, content), cached)
        return True

    def fill_memory(
        self,
        inputs: TooltipPreparationInputs,
        token,
        inflected,
        *,
        mined: bool,
    ) -> None:
        cache = self._open(inputs)
        memory = self._memory
        if cache is None or memory is None:
            return
        from saitenka.app.render_cache import content_key

        signature = self.signature(inputs)
        panel_key = tooltip_panel.panel_key(inputs.panels, token, inflected, mined=mined)
        content = content_key(panel_key)
        cached = cache.peek_compressed(signature, content)
        if cached is not None:
            memory.put((signature, content), cached)

    def precompose_head(
        self,
        inputs: TooltipPreparationInputs,
        panel: Panel,
        token,
        inflected,
        *,
        mined: bool,
        cap: int,
        protected: bool = False,
    ) -> None:
        cache = self._open(inputs)
        if cache is None:
            panel.precompose_head(cap)
            return
        from saitenka.app.render_cache import content_key

        panel_key = tooltip_panel.panel_key(inputs.panels, token, inflected, mined=mined)
        panel.precompose_head(
            cap,
            cache=cache,
            config_sig=self.signature(inputs),
            content_key=content_key(panel_key),
            min_height=self.min_height,
            protected=protected,
        )

    def install_mask_atlas(self, opened: mask_atlas_startup.OpenedMaskAtlas) -> bool:
        if self._mask_atlas is not None:
            opened.atlas.close()
            return False
        from saitenka import fonts

        self._mask_atlas = opened.atlas
        fonts.set_mask_atlas(None, opened.atlas)
        log.info(
            "mask atlas: ready - lazy per-glyph reads (%d MB on disk)",
            opened.atlas.disk_bytes() // 1_000_000,
        )
        return True

    def uninstall_mask_atlas(self) -> None:
        atlas = self._mask_atlas
        if atlas is None:
            return
        from saitenka import fonts

        fonts.set_mask_atlas(None, None)
        self._mask_atlas = None
        atlas.close()


class TooltipPreparationController:
    """Compose persistent caching, speculative warming, and atlas activation."""

    def __init__(self, ipc: MpvIPC, config: TooltipPreparationConfig) -> None:
        self.config = config
        self.cache = PersistentHeadCache(config)
        self._prefetch = prefetch.PrefetchState(config.head_queue_max)
        self._prefetch_backend = _PreparationBackend(self.cache)
        self._mask_activation = mask_atlas_startup.ActivationState()
        self._mask_submit = mask_atlas_startup.configure_runtime_job(ipc)

    @property
    def generation(self) -> int:
        return self._prefetch.gen

    @property
    def snapshot(self) -> prefetch.PrefetchSnapshot:
        return self._prefetch.snapshot

    @property
    def worker_count(self) -> int:
        return self._prefetch.workers

    def cancel(self) -> int:
        """Invalidate all work admitted against an earlier interaction fact."""
        return self._prefetch.cancel()

    def invalidate_dependencies(self, tooltip: TooltipController) -> None:
        """Retire every tooltip artifact derived from replaced collaborators."""
        tooltip.invalidate_dependencies()
        self.cancel()
        self._prefetch.key = None
        self.cache.invalidate_signature()

    def start(self, ipc: MpvIPC, tokenizer: Tokenizer, *, dictionary_available: bool) -> None:
        prefetch.start_prefetch(
            ipc,
            self._prefetch,
            self._prefetch_backend,
            tokenizer,
            self.config.workers,
            enabled=self.config.enabled and dictionary_available,
        )

    def update(
        self,
        ports: prefetch.PrefetchPorts,
        head: prefetch.HeadProbe,
        inputs: TooltipPreparationInputs,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool:
        before = self._prefetch.gen
        prefetch.update_prefetch(
            self._prefetch,
            ports,
            head,
            inputs,
            on_finished,
        )
        return self._prefetch.gen != before

    def finish(
        self, completion: EffectFinished, on_finished: Callable[[EffectFinished], None]
    ) -> None:
        prefetch.finish(self._prefetch, completion, on_finished)

    def close_prefetch(self) -> None:
        prefetch.close(self._prefetch)

    def request_mask_atlas(self, on_finished: Callable[[EffectFinished], None]) -> None:
        mask_atlas_startup.request(
            self._mask_activation,
            mask_atlas_startup.MaskAtlasRequest(
                enabled=self.cache.mask_atlas_enabled,
                path=cache_dir() / "mask-atlas.sqlite",
            ),
            self._mask_submit,
            on_finished,
        )

    def finish_mask_atlas(self, completion: EffectFinished) -> None:
        opened = mask_atlas_startup.finish(self._mask_activation, completion)
        if opened is not None:
            self.cache.install_mask_atlas(opened)

    def close_mask_activation(self) -> None:
        mask_atlas_startup.close(self._mask_activation)

    def prepare_hover(
        self,
        inputs: TooltipPreparationInputs,
        panel: Panel,
        token,
        inflected,
        *,
        mined: bool,
        cap: int,
        should_cancel: Callable[[], bool],
    ) -> None:
        if self.cache.worker_seed_head(inputs, panel, token, inflected, mined=mined, cap=cap):
            return
        self.cache.precompose_head(inputs, panel, token, inflected, mined=mined, cap=cap)
        if not should_cancel():
            self.cache.fill_memory(inputs, token, inflected, mined=mined)

    def prepare_engaged(
        self,
        request: HoverRequest,
        should_cancel: Callable[[], bool],
    ) -> None:
        panels = request.panels
        if panels is None:
            raise ValueError("engaged hover requires captured panel inputs")
        inputs = TooltipPreparationInputs(
            panels,
            cast("DictionarySet | None", panels.style.dict_set),
        )
        if should_cancel():
            return
        panel = tooltip_panel.panel_for(
            panels,
            request.token,
            request.inflected,
            min_h=request.cap,
            mined=request.mined,
            extra_terms=request.phrase,
        )
        if should_cancel():
            return
        self.prepare_hover(
            inputs,
            panel,
            request.token,
            request.inflected,
            mined=request.mined,
            cap=request.cap,
            should_cancel=should_cancel,
        )


class _PreparationBackend:
    def __init__(self, cache: PersistentHeadCache) -> None:
        self._cache = cache

    def run(
        self,
        item: prefetch.PrefetchItem | prefetch.HeadPrefetchItem,
        context: object,
        should_cancel: Callable[[], bool],
    ) -> bool:
        if not isinstance(context, TooltipPreparationInputs):
            raise TypeError("invalid tooltip-preparation inputs")
        if should_cancel():
            return False
        if isinstance(item, prefetch.HeadPrefetchItem) or item.full:
            panel = tooltip_panel.panel_for(
                context.panels,
                item.token,
                item.inflected,
                min_h=context.panels.cap,
                mined=item.mined,
            )
            if should_cancel():
                return False
            if not self._cache.worker_seed_head(
                context,
                panel,
                item.token,
                item.inflected,
                mined=item.mined,
                cap=context.panels.cap,
            ):
                self._cache.precompose_head(
                    context,
                    panel,
                    item.token,
                    item.inflected,
                    mined=item.mined,
                    cap=context.panels.cap,
                )
                if not should_cancel():
                    self._cache.fill_memory(context, item.token, item.inflected, mined=item.mined)
            return not should_cancel()
        if context.dictionary is not None and not should_cancel():
            context.dictionary.entry_for(item.token, item.inflected)
        return not should_cancel()
