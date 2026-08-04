"""Offline, episode-agnostic render-cache prebuild (#149) — `saitenka prewarm`.

The persistent render cache fills itself as you hover (the prefetch worker's precompose writes cost-gated
heads to disk), so a *later* session's cold hover is a copy+upload. This builds that cache up front for
the pathological tail — the biggest-glossary entries per dictionary, the ones whose cold first paint
blows the frame budget — so even the FIRST session is warm on them.

It is **population-aware, not episode-specific**: it renders the largest entries in your configured
dictionaries at your configured tooltip width/height, keyed by the same ``config_sig``/``content_key`` a
live hover computes. A different resolution or a dictionary re-import simply misses and can be rebuilt —
the rare-event contract in #149. Cost-gated the same way the live write-back is, so the on-disk cache
stays bounded to the jank tail.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from overlay.app.tokenize import Token

if TYPE_CHECKING:
    from overlay.app.render_cache import RenderCache
    from overlay.mpvio.ipc import MpvIPC

log = logging.getLogger(__name__)


class _PrewarmIPC:
    """A no-socket mpv stand-in: a fixed OSD and inert commands, so a headless Reader can build panels
    without a running mpv (mirrors the benchmark's FakeIPC)."""

    def __init__(self, width: int, height: int):
        self._osd = {"w": width, "h": height}

    def command(self, *args):
        if args and args[0] == "get_property" and len(args) > 1 and args[1] == "osd-dimensions":
            return {"data": self._osd}
        return {"data": None}

    def drain_events(self):
        return []


@dataclass(frozen=True, slots=True)
class PrewarmResult:
    candidates: int  # popular words rendered
    stored: int  # heads that cleared the cost gate and were persisted this run
    rows: int  # total rows now in the cache
    bytes: int  # total on-disk blob size
    stopped_at_ceiling: bool  # True if the byte ceiling was reached before the whole popular set


def _popular_terms(ds, limit: int) -> list[tuple[str, str]]:
    """The top ``limit`` ``(term, reading)`` by FREQUENCY rank across the configured freq dictionaries —
    the words a learner actually meets — de-duplicated by term, MOST-POPULAR FIRST. ``limit <= 0`` = ALL
    freq-ranked terms (``--limit 0``), for a full-coverage mask atlas (the render cache stays bounded by
    its byte ceiling). Empty when no frequency dictionaries are configured (nothing to rank by)."""
    sql_limit = limit if limit > 0 else -1  # SQLite LIMIT -1 = no limit
    best: dict[str, tuple[str, int]] = {}
    for fs in ds.freqs:
        rows = fs.db._conn().execute(
            "SELECT term, reading, rank FROM term_meta "
            "WHERE dict_id=? AND mode='freq' AND rank>0 ORDER BY rank LIMIT ?",
            (fs.dict_id, sql_limit),
        )
        for term, reading, rank in rows:
            if term and (term not in best or rank < best[term][1]):
                best[term] = (reading or "", rank)
    ranked = sorted(best.items(), key=lambda kv: kv[1][1])
    return [
        (term, reading) for term, (reading, _rank) in (ranked if limit <= 0 else ranked[:limit])
    ]


def _make_reader(
    width: int,
    height: int,
    dict_titles,
    freqs,
    pitches,
    cache: RenderCache | None,
    *,
    render_cache_on: bool = True,
):
    """A fresh headless Reader with its OWN dict set (own SQLite conns + entry caches → no cross-thread
    race) but the SHARED render cache injected, so N worker threads render in parallel into one cache.
    ``render_cache_on=False`` (atlas-only fill) keeps the reader off the render cache entirely, so it
    always BUILDS+RASTERS a panel (never seeds pixels from disk) — required to feed the mask atlas."""
    from overlay.app.config import ReaderOptions, TooltipOptions
    from overlay.app.controller import Reader
    from overlay.app.dictdb import DictionaryDb
    from overlay.app.dictionary import DictionarySet

    ds = DictionarySet.from_db(DictionaryDb.open(), dict_titles, freqs, pitches)
    reader = Reader(
        cast("MpvIPC", _PrewarmIPC(width, height)),  # headless stand-in — no socket, fixed OSD
        dict_set=ds,
        options=ReaderOptions(tooltip=TooltipOptions(render_cache=render_cache_on), prefetch=False),
    )
    reader.osd = (width, height)
    if (
        cache is not None
    ):  # share ONE cache across threads (its conns are per-thread); None → lazy open
        reader._render_cache_obj = cache
        reader._render_cache_built = True
    return reader


class _PrewarmJob:
    """The parallel render loop's shared state + per-word work, as methods so each stays simple (the
    complexity ratchet). Thread-local Readers (own dict conns) share one injected cache; a lock guards
    the counters. A per-word render is independent, so it fans out cleanly across the free-threaded pool."""

    def __init__(
        self,
        reader_factory,
        cache: RenderCache | None,
        atlas,
        gate: int,
        sig: str,
        ceiling: int,
        on_progress,
        *,
        atlas_only: bool = False,
        native_scale: float = 1.0,
    ):
        self._make = reader_factory  # () -> a fresh headless Reader with the shared cache
        self.cache = cache  # None in atlas-only mode (the render cache is left untouched)
        self.atlas = atlas  # mask atlas (getmask2 write-back builds it too); None if unavailable
        self.atlas_only = atlas_only
        # >1.0 → ALSO raster each word's NATIVE-scale panel so its size×scale glyph masks land in the
        # atlas (the atlas keys on file:size:weight, so native masks are distinct entries). This makes
        # the hi-dpi crisp upgrade load from disk instead of paying getmask2 on the first native raster.
        self.native_scale = native_scale
        # If the atlas is empty it still needs building — then a word already in the render cache is NOT
        # skipped (we raster it to fill the atlas, just don't re-store the head). Both fresh → normal.
        # atlas_only forces the raster path for EVERY word (no render-cache side).
        self.fill_atlas = atlas_only or (atlas is not None and atlas.count() == 0)
        self.gate = gate
        self.sig = sig
        self.ceiling = ceiling
        self.on_progress = on_progress
        self._tls = threading.local()
        self._lock = threading.Lock()
        self.measured = 0
        self.skipped = 0
        self.stop = False

    def _reader(self):
        r = getattr(self._tls, "reader", None)
        if r is None:
            r = self._make()
            self._tls.reader = r
        return r

    def render(self, item: tuple[str, str]) -> None:
        """One popular word: skip if already cached, else build + (cost-gated) precompose + persist."""
        from overlay.app.render_cache import content_key

        if self.stop:
            return
        term, reading = item
        r = self._reader()
        tok = Token(term, term, reading, "名詞", 0, len(term))
        # atlas-only: no render cache at all — build + raster every word to feed the mask atlas.
        if self.atlas_only:
            try:
                r._panel_for(tok, term, min_h=r._tip_cap(), mined=False).precompose_head(
                    r._tip_cap()
                )
            except Exception:  # a single pathological entry must never abort the whole prebuild
                log.debug("prewarm(atlas) failed for %r", term, exc_info=True)
            self._raster_native(r, tok, term)
            self._tick()
            return
        assert self.cache is not None  # non-atlas_only always has a render cache
        already = self.cache.has(self.sig, content_key(r._panel_key(tok, term, mined=False)))
        if already and not self.fill_atlas:
            with self._lock:
                self.skipped += 1
            return  # both caches already have it (incremental / resumable) — skip the expensive build
        try:
            cap = r._tip_cap()
            st = r._panel_for(tok, term, min_h=cap, mined=False)
            if not already and st.full_height >= self.gate:
                # Render CACHE: only NON-TRIVIAL (≥ gate) not-yet-stored heads — the big cache stays
                # capped to the pathological/non-trivial tail. protected=True → live write-back of a rarer
                # word can never evict this prewarmed popular head (the capped-cache anti-thrash).
                r._precompose_head(
                    tok=tok, inflected=term, st=st, mined=False, cap=cap, protected=True
                )
            elif self.fill_atlas:
                # ATLAS: raster EVERY other word (trivial, or already in the render cache) so its glyphs
                # land in the per-glyph mask atlas — full population coverage — without a render-cache row.
                st.precompose_head(cap)
        except Exception:  # a single pathological entry must never abort the whole prebuild
            log.debug("prewarm failed for %r", term, exc_info=True)
        self._raster_native(r, tok, term)
        self._tick()

    def _raster_native(self, r, tok, term: str) -> None:
        """Raster the word's NATIVE-scale panel head so its size×scale glyph masks land in the atlas —
        no-op at scale ≤ 1 or without an atlas. The panel/pixels are discarded; only the atlas keeps."""
        if self.native_scale <= 1.0 or self.atlas is None:
            return
        from overlay.app import tooltip

        try:
            key = r._panel_key(tok, term, mined=False)
            tooltip.build_native_panel(
                r, tok, term, key, r._tip_cap(), self.native_scale, mined=False, anki=False
            )
        except Exception:  # never abort the prebuild over one pathological native raster
            log.debug("prewarm(native %.2f) failed for %r", self.native_scale, term, exc_info=True)

    def _tick(self) -> None:
        with self._lock:
            self.measured += 1
            m = self.measured
        if m % 2000 == 0:
            if self.atlas is not None:
                self.atlas.checkpoint()
            if self.cache is None:  # atlas-only: report atlas mask count, no render-cache ceiling
                rows = self.atlas.count() if self.atlas is not None else 0
                nbytes = 0
            else:
                self.cache.checkpoint()  # cap the WAL so a long parallel prebuild doesn't slow scanning
                rows, nbytes = self.cache.stats()
                if nbytes >= self.ceiling:
                    self.stop = True
            log.info("prewarm: measured %d, %d rows (%.0f MB)", m, rows, nbytes / 1e6)
            if self.on_progress is not None:
                self.on_progress(m, rows, nbytes)


def _open_build_caches(template, *, atlas_only: bool):
    """Open the caches prewarm BUILDS (creating them, unlike a live session which only uses them when
    they already exist): the render cache (unless ``atlas_only`` — then it's left closed/untouched) plus
    the mask atlas, wiring the atlas write-back so every render's getmask2 miss fills it. Returns
    ``(cache, atlas)`` (``cache`` is None in atlas-only mode)."""
    from overlay import fonts, mask_atlas
    from overlay.app.paths import cache_dir
    from overlay.app.render_cache import RenderCache

    cache = None
    if not atlas_only:
        cache = RenderCache.open(
            cache_dir() / "render-cache.sqlite", max_bytes=template._render_cache_max_bytes
        )
        if cache is None:  # pragma: no cover — open() only fails on a broken cache dir
            raise RuntimeError("could not open the render cache")
    atlas = mask_atlas.MaskAtlas.open(cache_dir() / "mask-atlas.sqlite")
    fonts.set_mask_atlas(None, atlas)
    return cache, atlas


def _finalize_caches(cache, atlas) -> tuple[int, int]:
    """Checkpoint + close both caches; return the render cache's ``(rows, bytes)`` — or the atlas mask
    count in atlas-only mode — for the :class:`PrewarmResult`."""
    if atlas is not None:
        atlas.checkpoint()
    rows, nbytes = cache.stats() if cache is not None else (atlas.count() if atlas else 0, 0)
    if cache is not None:
        cache.checkpoint()
        cache.close()
    if atlas is not None:
        atlas.close()
    return rows, nbytes


def prewarm(
    width: int,
    height: int,
    limit: int,
    on_progress=None,
    workers: int = 0,
    *,
    atlas_only: bool = False,
    atlas_scale: float = 1.0,
) -> PrewarmResult:
    """Build the render cache for the top ``limit`` popular words at the given resolution, rendering in
    PARALLEL across ``workers`` threads (0 = auto) — the free-threaded build renders concurrently, so a
    full prebuild is minutes not tens of minutes. **Incremental**: an entry already in the cache is
    skipped (a cheap index probe), so a re-run after a resolution/dict change only fills the gaps, and an
    interrupted run resumes. Stops when the ``render_cache_max_mb`` ceiling is reached. ``on_progress
    (measured, rows, nbytes)`` is a periodic heartbeat. Raises if no dictionaries are configured.

    ``atlas_only`` fills ONLY the mask atlas (every word rasters → its glyphs/words land in the atlas) and
    NEVER touches the render cache — so ``--limit 0`` can saturate the atlas over the whole corpus without
    growing the byte-ceiling-bounded render cache. Not incremental (the atlas has no per-word probe), but
    idempotent.

    ``atlas_scale`` > 1.0 ALSO rasters each word's native-scale panel so its ``size×scale`` glyph masks
    land in the MASK ATLAS (CJK/Latin glyphs) — match it to ``[tooltip] tip_scale`` so the hi-dpi crisp
    upgrade loads from disk. The RENDER cache is unaffected: it stays 1×-reference-only (the #149 size
    decision — per-resolution blobs would ~4× its storage and wall-time), keyed on the fixed tip_width."""
    from overlay.app.config import load_config

    cfg = load_config()
    dict_titles = list(cfg.get("dicts") or [])
    if not dict_titles:
        raise RuntimeError(
            "no dictionaries configured in overlay.toml — run `saitenka import` first"
        )
    freqs, pitches = list(cfg.get("freq") or []), list(cfg.get("pitch") or [])

    from overlay import fonts

    template = _make_reader(width, height, dict_titles, freqs, pitches, None)
    cache, atlas = _open_build_caches(template, atlas_only=atlas_only)

    terms = _popular_terms(template.dict_set, limit)
    before = cache.stats()[0] if cache is not None else 0
    job = _PrewarmJob(
        reader_factory=lambda: _make_reader(
            width, height, dict_titles, freqs, pitches, cache, render_cache_on=not atlas_only
        ),
        cache=cache,
        atlas=atlas,
        gate=template._render_cache_min_height(),
        sig=template._render_cache_sig(),
        ceiling=template._render_cache_max_bytes,
        on_progress=on_progress,
        atlas_only=atlas_only,
        native_scale=atlas_scale,
    )
    n_workers = workers if workers > 0 else min(8, (os.cpu_count() or 4))
    try:
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            list(ex.map(job.render, terms))
    finally:
        fonts.set_mask_atlas(None, None)  # clear the process-global write-back handle

    rows, nbytes = _finalize_caches(cache, atlas)
    return PrewarmResult(
        candidates=job.measured,
        stored=rows - before,
        rows=rows,
        bytes=nbytes,
        stopped_at_ceiling=job.stop,
    )
