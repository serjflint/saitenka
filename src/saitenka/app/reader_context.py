"""Reader state grouped by lifetime — the composition that shrinks the ``controller`` god-object (#30).

An mpv session outlives the file it plays; a hover outlives nothing. Grouping Reader state by *when it
is born and dies* — session ⊃ episode ⊃ interaction — is what makes a re-slot (swap the episode on a
file change, #100) correct by construction: rebind the context and no prior-episode state can leak. This
module owns the **episode** tier (and its cohesive sub-clusters, e.g. ``SubtitleSource``) plus the
``Delegated`` descriptor the Reader uses to expose a context's fields under their historical
``reader.<field>`` names while call sites migrate onto ``reader.episode.<field>``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, overload

from saitenka.app.languages import MAIN_LANG
from saitenka.app.mined_set import MinedSet
from saitenka.app.subnav_settle import SettleWindow

# In-RAM ceiling for the tier-2 compressed-head cache. Independent of the disk ceiling
# (``render_cache_max_mb``, which can be GBs): compressed heads are ~10× smaller than the BGRA arrays,
# so 64 MB still covers hundreds of pathological heads — the working set a session actually hovers.
_MEM_TIER_MAX_BYTES = 64 * 1024 * 1024

if TYPE_CHECKING:
    from saitenka.app.backlog import BacklogStore
    from saitenka.app.languages import Language
    from saitenka.app.mined_store import MinedCardStore
    from saitenka.app.render_cache import CompressedHeadCache, RenderCache
    from saitenka.app.session_stats import SessionRecorder
    from saitenka.app.subtitle_modes import ProviderFetchFactory
    from saitenka.mask_atlas import MaskAtlas
    from saitenka.subtitles import CueIndex


class Delegated[T]:
    """A typed attribute that reads/writes ``obj.<context>.<field>`` — ``<context>`` may be a dotted
    path (e.g. ``episode.subtitle``). The stable seam that lets the Reader own its state as lifetime
    contexts without breaking the ``reader.<field>`` call sites."""

    __slots__ = ("_field", "_parts")

    def __init__(self, context: str, field: str) -> None:
        self._parts = context.split(
            "."
        )  # precomputed once — the hot path walks this, never re-splits
        self._field = field

    def _owner(self, obj: object) -> object:
        for part in self._parts:
            obj = getattr(obj, part)
        return obj

    @overload
    def __get__(self, obj: None, _objtype: type | None = None) -> Delegated[T]: ...
    @overload
    def __get__(self, obj: object, _objtype: type | None = None) -> T: ...
    def __get__(self, obj: object | None, _objtype: type | None = None) -> Delegated[T] | T:
        if obj is None:
            return self  # class-level access (e.g. introspection) yields the descriptor
        return getattr(self._owner(obj), self._field)

    def __set__(self, obj: object, value: T) -> None:
        setattr(self._owner(obj), self._field, value)


class SubtitleSource:
    """Subtitle acquisition + selection for one file: the chosen JP/EN tracks plus the background
    provider-fetch and retry handshake. Reset whenever the episode changes."""

    def __init__(self) -> None:
        self.jp_sid: int | None = None
        self.en_sid: int | None = None
        self.language: Language = MAIN_LANG
        self.slang = "ja,jpn,jp"
        self.retry_factory: ProviderFetchFactory | None = None
        self.retry_active = False
        self.retry_lock = threading.Lock()
        self.translation_secondary_sid: int | None = None  # the mpv sid feeding the EN reveal


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
        self.nav_provisional_cue_counted = False
        # durable per-session recorder (app/session_stats.py); None until stats start on file load
        self.session_recorder: SessionRecorder | None = None


class InteractionContext:
    """State scoped to the current on-screen interaction (hover/tooltip/reveal). Grows with the tooltip
    cluster; for now it owns the EN-translation reveal toggle."""

    def __init__(self) -> None:
        self.translate_on = False
        self.trans_text: str | None = None


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
    """State scoped to the whole mpv session — durable across every episode re-slot (#100): the
    persistent render caches, the in-deck mined set, the Anki reachability cache, and the review backlog
    store. Nothing here is rebuilt on a file change (that is EpisodeContext); this is the tier an episode
    swap must leave untouched."""

    def __init__(self, render_cache: RenderCacheState) -> None:
        self.render_cache = render_cache
        self.mined = MinedSet()  # card expressions already in the deck → header ⊕ becomes ✓
        self.anki_cache: tuple[float, bool] = (0.0, False)  # (checked_at, reachable) — see _anki_ok
        self.backlog_store: BacklogStore | None = None  # lazy review-backlog DB handle
        self.mined_store: MinedCardStore | None = None  # lazy mined-card DB handle (#253)
