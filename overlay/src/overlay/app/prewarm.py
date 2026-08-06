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
from overlay.mask_atlas import REFERENCE_SCALE

if TYPE_CHECKING:
    from overlay.app.render_cache import RenderCache
    from overlay.mpvio.ipc import MpvIPC

log = logging.getLogger(__name__)

_CHECKPOINT_EVERY = 2000  # rastered words between heartbeats (WAL truncate + progress emit)
_PLATEAU_MIN_NEW = 64  # a checkpoint adding fewer new masks than this counts as "dry"


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
class PrewarmPlan:
    """Emitted once, before the fan-out, so the operator sees scope + starting footprint BEFORE a long
    run commits — a ``--limit 0`` atlas sweep is >1M words and the atlas is uncapped."""

    total: int  # words this run will consider
    already_done: int  # of those, already in the resume ledger / cache → will be skipped
    remaining: int  # total - already_done
    nbytes: int  # cache size on disk right now (the baseline the heartbeat grows from)
    capped: bool  # True if this mode has a byte ceiling (render cache); False = uncapped (atlas)


@dataclass(frozen=True, slots=True)
class PrewarmProgress:
    """One heartbeat. The counters a long prebuild needs to be legible: ``new_rows`` exposes a plateau
    (0 → the glyph population saturated), ``dup_masks`` exposes the OTHER dedup layer (masks re-rastered
    but found already cached — why a re-scale run adds nothing), ``skipped`` exposes resume,
    ``projected_bytes`` extrapolates the tail at the cumulative rate since the run's first raster."""

    measured: int  # words rastered so far (skipped words excluded)
    to_raster: int  # words this run set out to raster (population − skips) → progress denominator
    skipped: int  # words skipped via the resume LEDGER (per-word done marker)
    rows: int  # total rows / masks now in the cache
    new_rows: int  # rows added since the previous checkpoint (0 → plateau)
    dup_masks: int  # masks re-produced this checkpoint but already cached (INSERT OR IGNORE)
    nbytes: int  # REAL on-disk bytes (atlas mode now reports this; it used to send 0)
    projected_bytes: int  # final-size extrapolation at the cumulative rate (0 = unknown)


@dataclass(frozen=True, slots=True)
class PrewarmResult:
    candidates: int  # popular words rendered
    stored: int  # heads that cleared the cost gate and were persisted this run
    rows: int  # total rows now in the cache
    bytes: int  # total on-disk blob size
    skipped: int  # words skipped via the resume ledger (already rastered at this scale)
    stopped_at_ceiling: bool  # stopped before the whole set — render byte ceiling or atlas plateau


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
        reader.session.render_cache.obj = cache
        reader.session.render_cache.built = True
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
        total: int = 0,
        already_done: int = 0,
        start_rows: int = 0,
        start_nbytes: int = 0,
        plateau_stop: int = 0,
        checkpoint_every: int = _CHECKPOINT_EVERY,
        plateau_min: int = _PLATEAU_MIN_NEW,
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
        # Progress / early-stop bookkeeping. ``to_raster`` (population minus the resume-ledger skips) is
        # the denominator the tail projection extrapolates over; ``plateau_stop`` opts into stopping once
        # the glyph population saturates (N dry checkpoints) instead of churning the whole 1M-word tail.
        self.to_raster = max(0, total - already_done)
        self.plateau_stop = plateau_stop
        self.checkpoint_every = checkpoint_every
        self.plateau_min = plateau_min
        self._tls = threading.local()
        self._lock = threading.Lock()
        self.measured = 0
        self.skipped = 0
        self.stop = False
        self._last_rows = start_rows  # rows at the previous checkpoint → new-mask delta
        self._last_ignored = 0  # atlas.ignored at the previous checkpoint → already-cached delta
        self._start_nbytes = start_nbytes  # run-start footprint → cumulative-rate projection
        self._dry_streak = 0  # consecutive dry checkpoints, for --atlas-plateau

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
            self._render_atlas(r, tok, term)
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

    def _render_atlas(self, r, tok, term: str) -> None:
        """One atlas-only word, with a CHEAP LEDGER READ before any raster. Every scale builds the 1×
        REFERENCE masks (``precompose_head``) plus its N× native masks (``_raster_native``); the
        reference is scale-independent, tracked under :data:`REFERENCE_SCALE`, so a run at ANY scale
        skips a reference another scale already built. A word whose reference AND native are both done is
        fully skipped — no raster, no getmask2 (this is what makes a re-scale run near-instant)."""
        atlas = self.atlas
        ref_done = atlas is not None and atlas.is_done(REFERENCE_SCALE, term)
        native_needed = self.native_scale > REFERENCE_SCALE
        native_done = not native_needed or (
            atlas is not None and atlas.is_done(self.native_scale, term)
        )
        if ref_done and native_done:  # nothing left to raster for this word at this scale
            with self._lock:
                self.skipped += 1
            return
        if not ref_done:
            try:
                r._panel_for(tok, term, min_h=r._tip_cap(), mined=False).precompose_head(
                    r._tip_cap()
                )
            except Exception:  # a single pathological entry must never abort the whole prebuild
                log.debug("prewarm(atlas ref) failed for %r", term, exc_info=True)
            if atlas is not None:
                atlas.mark_done(REFERENCE_SCALE, term)  # 1× reference masks persisted
        if native_needed and not native_done:
            self._raster_native(r, tok, term)
            if atlas is not None:
                atlas.mark_done(self.native_scale, term)  # N× native masks persisted
        self._tick()

    def _raster_native(self, r, tok, term: str) -> None:
        """Raster the word's reference panel at the native scale so its size×scale glyph masks land in
        the atlas — no-op at scale ≤ 1 or without an atlas. The composited pixels are discarded; only the
        atlas write-back keeps. One-panel arch: the SAME reference panel, composited natively (no second
        panel)."""
        if self.native_scale <= 1.0 or self.atlas is None:
            return
        try:
            cap = r._tip_cap()
            st = r._panel_for(tok, term, min_h=cap, mined=False)
            st.viewport(
                0, cap, scale=self.native_scale
            )  # native compose → glyph masks to the atlas
        except Exception:  # never abort the prebuild over one pathological native raster
            log.debug("prewarm(native %.2f) failed for %r", self.native_scale, term, exc_info=True)

    def _tick(self) -> None:
        with self._lock:
            self.measured += 1
            m, skipped = self.measured, self.skipped
        if m % self.checkpoint_every == 0:
            self._report(m, skipped)

    def _report(self, m: int, skipped: int) -> None:
        """One heartbeat: checkpoint the WAL, read (rows, real bytes), emit the delta/projection, and
        re-evaluate the stop conditions. Split out of :meth:`_tick` so each stays under the complexity
        ratchet."""
        rows, nbytes = self._checkpoint_stats()
        new_rows = rows - self._last_rows
        ignored = self.atlas.ignored if self.atlas is not None else 0
        dup_masks = ignored - self._last_ignored
        projected = self._project(nbytes, m)
        self._update_stop(new_rows, nbytes)
        self._last_rows, self._last_ignored = rows, ignored
        log.info(
            "prewarm: measured %d, %d rows (+%d, %d cached), %.0f MB",
            m,
            rows,
            new_rows,
            dup_masks,
            nbytes / 1e6,
        )
        if self.on_progress is not None:
            self.on_progress(
                PrewarmProgress(
                    measured=m,
                    to_raster=self.to_raster,
                    skipped=skipped,
                    rows=rows,
                    new_rows=new_rows,
                    dup_masks=dup_masks,
                    nbytes=nbytes,
                    projected_bytes=projected,
                )
            )

    def _checkpoint_stats(self) -> tuple[int, int]:
        """Checkpoint the WAL and read ``(rows, on-disk bytes)``. Atlas-only reports the atlas's own mask
        count + REAL ``disk_bytes`` (it is uncapped, so size is the number to watch); render mode reads
        the cache stats. The atlas is checkpointed in both modes — its write-back fills it either way."""
        if self.atlas is not None:
            self.atlas.checkpoint()
        if self.cache is None:  # atlas-only
            if self.atlas is None:
                return 0, 0
            return self.atlas.count(), self.atlas.disk_bytes()
        self.cache.checkpoint()  # cap the WAL so a long parallel prebuild doesn't slow scanning
        return self.cache.stats()

    def _update_stop(self, new_rows: int, nbytes: int) -> None:
        """Two independent stop conditions: the render cache's byte ceiling (unchanged), and the opt-in
        atlas plateau — ``plateau_stop`` consecutive checkpoints adding < ``plateau_min`` masks means the
        glyph population saturated and the corpus tail is near-pure churn (the 1.25M-word tail case)."""
        if self.cache is not None and nbytes >= self.ceiling:
            self.stop = True
        if self.plateau_stop > 0:
            self._dry_streak = self._dry_streak + 1 if new_rows < self.plateau_min else 0
            if self._dry_streak >= self.plateau_stop:
                self.stop = True

    def _project(self, nbytes: int, m: int) -> int:
        """Extrapolate final on-disk bytes from the CUMULATIVE bytes/word since this run's first raster
        (total growth ÷ words rastered), × words-left. NOT a single checkpoint's Δ: that, times the
        ~1M-word horizon, amplifies SQLite's page-quantised (lumpy) growth into ±80 MB swings between
        heartbeats. The cumulative rate averages that noise out, so the estimate converges as the run
        progresses. 0 until at least one word has been rastered."""
        if m <= 0:
            return 0
        left = max(0, self.to_raster - m)
        rate = max(0.0, (nbytes - self._start_nbytes) / m)
        return int(nbytes + rate * left)


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
            cache_dir() / "render-cache.sqlite",
            max_bytes=template.session.render_cache.cache_max_bytes,
        )
        if cache is None:  # pragma: no cover — open() only fails on a broken cache dir
            raise RuntimeError("could not open the render cache")
    atlas = mask_atlas.MaskAtlas.open(cache_dir() / "mask-atlas.sqlite")
    fonts.set_mask_atlas(None, atlas)
    return cache, atlas


def _finalize_caches(cache, atlas) -> tuple[int, int]:
    """Checkpoint + close both caches; return the render cache's ``(rows, bytes)`` — or the atlas's
    ``(mask count, real disk bytes)`` in atlas-only mode — for the :class:`PrewarmResult`."""
    if atlas is not None:
        atlas.checkpoint()
    if cache is not None:
        rows, nbytes = cache.stats()
    elif atlas is not None:
        rows, nbytes = atlas.count(), atlas.disk_bytes()
    else:  # pragma: no cover — neither cache opened
        rows, nbytes = 0, 0
    if cache is not None:
        cache.checkpoint()
        cache.close()
    if atlas is not None:
        atlas.close()
    return rows, nbytes


def _startup_plan(terms, cache, atlas, *, atlas_only: bool, atlas_scale: float):
    """The pre-run summary: total words, how many are already done (resume ledger — atlas mode only; the
    render cache's per-word probe is too costly to run over the whole population up front), and the
    starting footprint. Returns ``(plan, already_done, start_rows)`` — the last two seed the job."""
    total = len(terms)
    if atlas_only and atlas is not None:
        # A word is fully done only if BOTH passes are done: the 1× reference AND (for scale > 1) the
        # native pass. So a scale-1.0 run counts reference-done words; a scale-N run their intersection.
        done = atlas.done_words(REFERENCE_SCALE)
        if atlas_scale > REFERENCE_SCALE:
            done = done & atlas.done_words(atlas_scale)
        already_done = sum(1 for term, _ in terms if term in done)
        start_rows, start_nbytes, capped = atlas.count(), atlas.disk_bytes(), False
    elif cache is not None:
        already_done = 0
        (start_rows, start_nbytes), capped = cache.stats(), True
    else:  # pragma: no cover — atlas open failed AND render cache off; degrade to a bare plan
        already_done, start_rows, start_nbytes, capped = 0, 0, 0, False
    plan = PrewarmPlan(
        total=total,
        already_done=already_done,
        remaining=max(0, total - already_done),
        nbytes=start_nbytes,
        capped=capped,
    )
    return plan, already_done, start_rows


def prewarm(
    width: int,
    height: int,
    limit: int,
    on_progress=None,
    workers: int = 0,
    *,
    atlas_only: bool = False,
    atlas_scale: float = 1.0,
    on_start=None,
    plateau_stop: int = 0,
) -> PrewarmResult:
    """Build the render cache for the top ``limit`` popular words at the given resolution, rendering in
    PARALLEL across ``workers`` threads (0 = auto) — the free-threaded build renders concurrently, so a
    full prebuild is minutes not tens of minutes. **Incremental**: an entry already in the cache is
    skipped (a cheap index probe), so a re-run after a resolution/dict change only fills the gaps, and an
    interrupted run resumes. Stops when the ``render_cache_max_mb`` ceiling is reached. ``on_start
    (PrewarmPlan)`` fires once with the scope + starting footprint; ``on_progress(PrewarmProgress)`` is a
    periodic heartbeat (real bytes, new-mask delta, skipped, tail projection). Raises if no dictionaries
    are configured.

    ``atlas_only`` fills ONLY the mask atlas (every word rasters → its glyphs/words land in the atlas) and
    NEVER touches the render cache — so ``--limit 0`` can saturate the atlas over the whole corpus without
    growing the byte-ceiling-bounded render cache. Resumable + idempotent: a per-word
    ``done(scale, word)`` ledger skips words rastered at this scale, so a stopped ``--limit 0``
    re-run resumes where it left off (scoped by scale — 1.5 masks ≠ 2.0 masks). ``plateau_stop`` > 0
    stops the sweep early after that many consecutive dry checkpoints — the CJK glyph set saturates in
    the first few thousand words, so the 1M-word tail is near-pure churn (uncapped, watch the size).

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
    if atlas is not None:
        atlas.backfill_reference_done()  # native-done ⇒ reference-done, so cross-scale runs can skip it
    plan, already_done, before = _startup_plan(
        terms, cache, atlas, atlas_only=atlas_only, atlas_scale=atlas_scale
    )
    if on_start is not None:
        on_start(plan)
    job = _PrewarmJob(
        reader_factory=lambda: _make_reader(
            width, height, dict_titles, freqs, pitches, cache, render_cache_on=not atlas_only
        ),
        cache=cache,
        atlas=atlas,
        gate=template._render_cache_min_height(),
        sig=template._render_cache_sig(),
        ceiling=template.session.render_cache.cache_max_bytes,
        on_progress=on_progress,
        atlas_only=atlas_only,
        native_scale=atlas_scale,
        total=plan.total,
        already_done=already_done,
        start_rows=before,
        start_nbytes=plan.nbytes,
        plateau_stop=plateau_stop,
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
        skipped=job.skipped,
        stopped_at_ceiling=job.stop,
    )
