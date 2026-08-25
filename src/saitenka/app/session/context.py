"""SessionController state grouped by lifetime — the composition that shrinks the ``controller`` god-object (#30).

An mpv session outlives the file it plays; a hover outlives nothing. Grouping SessionController state by *when it
is born and dies* — session ⊃ episode ⊃ interaction — is what makes a re-slot (swap the episode on a
file change, #100) correct by construction: rebind the context and no prior-episode state can leak. This
module owns the **episode** tier (and its cohesive sub-clusters, e.g. ``SubtitleSource``) plus the
feature-owned collaborators whose state shares the interaction lifetime.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from saitenka.app.features.tooltip.popups import hovered_meta
from saitenka.app.subnav_settle import SettleWindow

# In-RAM ceiling for the tier-2 compressed-head cache. Independent of the disk ceiling
# (``render_cache_max_mb``, which can be GBs): compressed heads are ~10× smaller than the BGRA arrays,
# so 64 MB still covers hundreds of pathological heads — the working set a session actually hovers.
_MEM_TIER_MAX_BYTES = 64 * 1024 * 1024

if TYPE_CHECKING:
    from saitenka.app.backlog import BacklogStore
    from saitenka.app.features.tooltip.popups import HoverMetadata, TooltipState
    from saitenka.app.render_cache import CompressedHeadCache, RenderCache
    from saitenka.app.session_stats import SessionRecorder
    from saitenka.app.subtitle_modes import ProviderFetchFactory
    from saitenka.mask_atlas import MaskAtlas
    from saitenka.runtime.hover_pause import PauseClaim
    from saitenka.runtime.interaction_slice import (
        HoveredWordStore,
        HoverPauseStore,
        PulseStore,
        TipNavStore,
    )
    from saitenka.runtime.pulse import PulseState
    from saitenka.runtime.tipnav import TipNavState
    from saitenka.subtitles import Cue, CueIndex


class SubtitleSource:
    """The background provider-fetch and retry handshake for one file.

    The *selection* it used to hold moved to `Owner.SUBTITLE`'s slice. What is left is the part no
    reducer can take: a lock and the flag it guards."""

    def __init__(self) -> None:
        self.retry_factory: ProviderFetchFactory | None = None
        self.retry_active = False
        self.retry_lock = threading.Lock()


class TooltipStateOwner(Protocol):
    """The interaction context reads tooltip facts through their bounded owner."""

    @property
    def state(self) -> TooltipState: ...

    @property
    def nav_store(self) -> TipNavStore: ...

    @property
    def pulse_store(self) -> PulseStore: ...

    @property
    def pause_store(self) -> HoverPauseStore: ...

    @property
    def word_store(self) -> HoveredWordStore: ...


class EpisodeContext:
    """State scoped to one played file. Rebuilt on every file change (#100 re-slot) — nothing here may
    outlive the episode."""

    def __init__(self) -> None:
        self.subtitle = SubtitleSource()
        # external sub-index of the JP cue file → Alt+←/→/↓ render the target line INSTANTLY, decoupled
        # from mpv's slow video seek; the real sub-seek fires behind it and reconciles once it settles.
        self.sub_index: CueIndex | None = None
        self.nav_idx = -1  # last cue index jumped to (chaining hint; -1 = unknown)
        # While open, ignore mpv's mid-seek transient sub-text (app/subnav_settle.py).
        self.sub_settle = SettleWindow()
        self.nav_prev_text = ""  # cue text showing right before a nav render (reconcile)
        # The cue a nav jumped to, so a geometry decision uses the target line rather than whatever
        # mpv is still showing mid-seek. Episode-scoped: a hint left over from the previous file
        # would aim the first decision of the new one at a cue that is no longer anywhere.
        self.geometry_cue_hint: Cue | None = None
        self.nav_provisional_cue_counted = False
        # durable per-session recorder (app/session_stats.py); None until stats start on file load
        self.session_recorder: SessionRecorder | None = None


class InteractionContext:
    """State scoped to the current on-screen interaction (hover/tooltip).

    Holds the tooltip collaborator whose state shares this volatile interaction lifetime. Other OSD
    surfaces are owned directly by their feature controllers.
    """

    #: Assigned by `SessionController.__init__`; the owner needs runtime/build collaborators this lifetime
    #: container has no business constructing.
    tooltip: TooltipStateOwner

    @property
    def tip_nav(self) -> TipNavState:
        return self.tooltip.nav_store.current

    @property
    def tip(self) -> TooltipState:
        """Read-only surface projection of the tooltip feature's owned state."""
        return self.tooltip.state

    @property
    def nav_store(self) -> TipNavStore:
        return self.tooltip.nav_store

    @property
    def pulse_store(self) -> PulseStore:
        return self.tooltip.pulse_store

    @property
    def pause_store(self) -> HoverPauseStore:
        return self.tooltip.pause_store

    @property
    def word_store(self) -> HoveredWordStore:
        return self.tooltip.word_store

    @property
    def copy_pulse(self) -> PulseState:
        return self.tooltip.pulse_store.current

    @property
    def hover_pause(self) -> PauseClaim:
        return self.tooltip.pause_store.current

    @property
    def hovered_word_meta(self) -> HoverMetadata:
        return hovered_meta(self.tooltip.word_store)


class RenderCacheState:
    """The #149 persistent, cross-session rendering caches: the on-disk render cache (seeds a cold
    hover's first viewport) and the glyph mask atlas, plus the memoised config signature. Opened lazily
    / use-when-available (``saitenka prewarm`` is the builder), so a fresh install touches no disk; every
    handle is session-lifetime."""

    def __init__(
        self,
        *,
        cache_on: bool,
        cache_max_bytes: int,
        cache_min_height_px: int,
        mask_atlas_on: bool,
    ) -> None:
        self.cache_on = cache_on
        self.cache_max_bytes = cache_max_bytes
        self.cache_min_height_px = cache_min_height_px  # cost gate (px): only tall heads persist
        self.obj: RenderCache | None = None
        # Tier-2: an in-memory store of COMPRESSED first-view heads the prefetch worker hydrates from
        # disk, so a cold hover inflates from RAM and never opens SQLite on the main thread. Capped well
        # below the disk ceiling (compressed heads are ~10× smaller, so this still covers a wide set).
        self.mem: CompressedHeadCache | None = None
        if cache_on:
            from saitenka.app.render_cache import CompressedHeadCache

            self.mem = CompressedHeadCache(max_bytes=min(cache_max_bytes, _MEM_TIER_MAX_BYTES))
        self.built = False  # the lazy open ran once (obj stays None if no prebuilt cache exists)
        self.config_sig: str | None = None  # format+width+cap+dict-set signature, memoised…
        self.sig_key: tuple[int, int] | None = (
            None  # …per (width, cap) — a res change recomputes it
        )
        self.mask_atlas_on = mask_atlas_on
        self.mask_atlas: MaskAtlas | None = (
            None  # write-back handle (kept alive), or None off/absent
        )


class SessionContext:
    """Shared session state not already owned by a bounded feature controller."""

    def __init__(self, render_cache: RenderCacheState) -> None:
        self.render_cache = render_cache
        self.anki_cache: tuple[float, bool] = (0.0, False)
        self.backlog_store: BacklogStore | None = None  # lazy review-backlog DB handle
