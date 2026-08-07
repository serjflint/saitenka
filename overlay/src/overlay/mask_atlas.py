"""Persistent glyph/run mask atlas (#149 Tier-1) — cross-session ``getmask2`` alpha bitmaps.

``getmask2`` (FreeType rasterisation) is ~half the render CPU and repeats massively across words (kana +
common kanji). ``fonts.glyph_mask`` already memoises it per thread, but that cache is cold every restart.
This persists the alpha bitmaps to SQLite keyed on ``(font_id, text, mode, subpixel-start)`` so a later
session — or a cache-MISS word, or a scroll, or the build that runs *after* a cold direct-paint — draws
from disk instead of re-rasterising.

Scope + cost (measured on the 9-dict set at a fixed width/theme): the top-32k popular words touch
~200–350k unique masks ≈ **60–140 MB zlib** — cheaper than the ~1 GB first-viewport cache, but it only
saves the *raster half* of a build (the first-viewport direct-paint already skips the WHOLE pipeline for a
cached word). It is **opt-out, USED WHEN AVAILABLE**: ``saitenka prewarm`` builds it; a session with a
prebuilt ``mask-atlas.sqlite`` reads it LAZILY, one glyph at a time (:func:`get_one`), and writes back
live misses; no prebuilt atlas → nothing loads. The per-thread ``fonts.glyph_mask`` LRU fronts the atlas,
so a PK lookup happens at most once per ``(glyph, phase, thread)`` — a session touches only a few thousand
unique glyphs, so lazy reads cost far less than deserializing all N masks, and startup no longer stalls on
a full ~GB load into RAM (the earlier design; :func:`load_into` is kept for prewarm/tests). The alpha bytes
round-trip byte-identically (``Image.frombytes(mode, size, data).im``), so a loaded mask draws
pixel-for-pixel identically to a fresh ``getmask2`` — proven in ``tests/test_mask_atlas``.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import zlib
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

# The 1× reference pass every prewarm scale builds first (native masks stack on top). Tracked in the
# ``done`` ledger under this scale so a run at ANY display scale can skip a reference another scale built.
REFERENCE_SCALE = 1.0

# The subpixel phase (draw.text passes frac(x), frac(y)) is quantised to this many steps per pixel for
# the key — CJK's fixed advance keeps the real phase set small, and quantising bounds the atlas without
# changing the stored bytes (the phase a live glyph_mask uses IS one of these, so it matches exactly).
_PHASE = 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS masks (
    font_id TEXT NOT NULL,
    text    TEXT NOT NULL,
    mode    TEXT NOT NULL,
    sx      INTEGER NOT NULL,
    sy      INTEGER NOT NULL,
    offx    INTEGER NOT NULL,
    offy    INTEGER NOT NULL,
    w       INTEGER NOT NULL,
    h       INTEGER NOT NULL,
    alpha   BLOB NOT NULL,
    PRIMARY KEY (font_id, text, mode, sx, sy)
);
CREATE TABLE IF NOT EXISTS done (
    scale REAL NOT NULL,
    word  TEXT NOT NULL,
    PRIMARY KEY (scale, word)
);
"""


def _phase(start: tuple[float, float]) -> tuple[int, int]:
    """Quantise a subpixel ``(frac_x, frac_y)`` start to the integer phase key."""
    return (int(start[0] * _PHASE) % _PHASE, int(start[1] * _PHASE) % _PHASE)


def serialize_core(core) -> tuple[int, int, bytes]:
    """A ``getmask2`` core → ``(w, h, raw-alpha-bytes)``. ``bytes(core)`` is the raw stencil; the
    reconstruction in :func:`deserialize_core` is byte-identical (the atlas round-trip contract)."""
    w, h = core.size
    return w, h, bytes(core)


def deserialize_core(w: int, h: int, mode: str, data: bytes):
    """Rebuild a draw-ready ``ImagingCore`` (in the glyph's render ``mode``, "L" for AA text) from
    :func:`serialize_core` output — byte-identical to the original ``getmask2`` core, so ``draw_bitmap``
    of it is pixel-for-pixel identical to a fresh raster."""
    return Image.frombytes(mode, (w, h), data).im


class MaskAtlas:
    """A bounded, opt-in SQLite store of ``getmask2`` alpha bitmaps. Best-effort: any :class:`sqlite3.Error`
    degrades to live rasterisation. Per-thread connections (WAL). Runtime reads are LAZY and per-glyph
    (:meth:`get_one`, fronted by the per-thread ``glyph_mask`` LRU) — a session pays only for the glyphs it
    draws, with no bulk load into RAM. :meth:`load_into` (bulk one-pass) is retained for prewarm/tests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
        # Write-back accounting for the prewarm heartbeat: masks actually stored vs found already present
        # (``INSERT OR IGNORE`` no-ops). Lock-guarded — worker threads share one atlas; the critical
        # section is two int adds, dwarfed by the getmask2 that precedes every put.
        self._stats_lock = threading.Lock()
        self.inserted = 0
        self.ignored = 0
        self._ensure_schema()

    @classmethod
    def open(cls, path: str | Path) -> MaskAtlas | None:
        try:
            return cls(path)
        except sqlite3.Error:  # pragma: no cover — disk/permission; degrade to no atlas
            log.debug("mask atlas unavailable at %s", path, exc_info=True)
            return None

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def put(self, font_id: str, text: str, mode: str, start: tuple[float, float], mask) -> None:
        """Persist one ``getmask2`` result (``mask`` = ``(core, (offx, offy))``). Idempotent —
        ``INSERT OR IGNORE`` (not REPLACE): the mask is deterministic for its key, so an existing row is
        already correct and re-writing identical bytes on a re-run is pure WAL churn. A format/font change
        needs a rebuild anyway (bump the cache dir), not a silent per-write refresh."""
        try:
            core, (offx, offy) = mask
            w, h, data = serialize_core(core)
            sx, sy = _phase(start)
            conn = self._conn()
            cur = conn.execute(
                "INSERT OR IGNORE INTO masks (font_id, text, mode, sx, sy, offx, offy, w, h, alpha) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (font_id, text, mode, sx, sy, offx, offy, w, h, zlib.compress(data, 1)),
            )
            conn.commit()
            # rowcount 1 → a NEW mask; 0 → the row already existed (IGNORE'd). The ignore count is the
            # "re-rastered but already cached" signal the prewarm surfaces (e.g. a scale whose 1× masks
            # another scale's reference pass already built).
            with self._stats_lock:
                if cur.rowcount == 1:
                    self.inserted += 1
                else:
                    self.ignored += 1
        except sqlite3.Error:
            log.debug("mask atlas put failed", exc_info=True)

    def is_done(self, scale: float, word: str) -> bool:
        """True if ``word`` was already fully rastered at ``scale`` (the prewarm resume ledger). The atlas
        keys per GLYPH, so there's no cheap "is this whole word cached" probe — this per-word marker gives
        ``saitenka prewarm --atlas-only`` a resumable skip: a stopped run re-run skips finished words
        instead of re-rastering from the start. Scoped by scale (1.5 masks ≠ 2.0 masks)."""
        try:
            row = (
                self._conn()
                .execute("SELECT 1 FROM done WHERE scale=? AND word=?", (scale, word))
                .fetchone()
            )
            return row is not None
        except sqlite3.Error:  # pragma: no cover — degrade to "not done" → re-raster (safe)
            return False

    def mark_done(self, scale: float, word: str) -> None:
        """Record that ``word``'s masks at ``scale`` are all persisted, so a later run skips it."""
        try:
            conn = self._conn()
            conn.execute("INSERT OR IGNORE INTO done (scale, word) VALUES (?, ?)", (scale, word))
            conn.commit()
        except sqlite3.Error:  # pragma: no cover — a lost marker only costs a re-raster next run
            log.debug("mask atlas mark_done failed", exc_info=True)

    def done_words(self, scale: float) -> set[str]:
        """Every word already rastered at ``scale`` (the resume ledger) as a set — for the prewarm
        startup summary's already-done / remaining split (one query beats a per-term probe). Best-effort
        → empty set (→ the summary just shows the whole population as remaining)."""
        try:
            rows = self._conn().execute("SELECT word FROM done WHERE scale=?", (scale,))
            return {w for (w,) in rows}
        except sqlite3.Error:  # pragma: no cover
            return set()

    def backfill_reference_done(self) -> int:
        """One-time, idempotent: a native-scale done marker implies its 1× reference pass ran (the
        reference is always built first), so mark those words done at :data:`REFERENCE_SCALE` too. Lets a
        run at any scale skip a reference a DIFFERENT scale's run already built (the ledgers predate the
        split). Returns rows added. Best-effort → 0."""
        try:
            conn = self._conn()
            cur = conn.execute(
                "INSERT OR IGNORE INTO done (scale, word) SELECT ?, word FROM done WHERE scale != ?",
                (REFERENCE_SCALE, REFERENCE_SCALE),
            )
            conn.commit()
            return cur.rowcount
        except sqlite3.Error:  # pragma: no cover
            return 0

    def load_into(self, mem: dict, *, font_ids: Iterable[str] | None = None) -> int:
        """Bulk-reconstruct every stored mask into ``mem`` (a shared read-only dict keyed
        ``(font_id, text, mode, sx, sy)`` → ``(core, offset)``) in one pass — the only read path (a
        per-glyph query would be slower than re-rasterising). Optionally restrict to ``font_ids``.
        Returns the count loaded. Call once at startup; ``mem`` is then read-only (free-threading-safe)."""
        try:
            conn = self._conn()
            if font_ids is None:
                rows = conn.execute(
                    "SELECT font_id, text, mode, sx, sy, offx, offy, w, h, alpha FROM masks"
                )
            else:
                ids = list(font_ids)
                q = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"SELECT font_id, text, mode, sx, sy, offx, offy, w, h, alpha FROM masks "  # noqa: S608 — only ?-placeholder count interpolated
                    f"WHERE font_id IN ({q})",
                    ids,
                )
            n = 0
            for font_id, text, mode, sx, sy, offx, offy, w, h, alpha in rows:
                core = deserialize_core(w, h, mode, zlib.decompress(alpha))
                mem[(font_id, text, mode, sx, sy)] = (core, (offx, offy))
                n += 1
            return n
        except (sqlite3.Error, ValueError):  # pragma: no cover — garbled blob won't reconstruct
            log.debug("mask atlas load failed", exc_info=True)
            return 0

    # A batched `get_many((keys)) → one `... WHERE (font_id,text,mode,sx,sy) IN (…)` is the documented
    # escape hatch IF query dispatch ever dominates — but measured it does NOT: the cost is the first-touch
    # disk page-in (~125 µs/glyph on an 864 MB atlas), which a batched query faults in identically; dispatch
    # is ~6 µs and repeats are free (the glyph_mask LRU + OS page cache). Hiding the page-in needs a
    # PREDICTIVE background warm (per the tokenized-subs idea), not batching. Not built until it pays.
    def get_one(self, font_id: str, text: str, mode: str, start: tuple[float, float]):
        """Lazy single-glyph read: one primary-key ``SELECT`` + deserialize → ``(core, offset)``, or
        ``None`` on miss/error. The runtime alternative to :meth:`load_into` — the per-thread
        ``fonts.glyph_mask`` LRU fronts it, so the atlas is queried at most once per
        ``(glyph, phase, thread)`` and a session pays only for the glyphs it actually draws (a few
        thousand), never deserializing all N up front. Measured ~6 µs PK lookup + ~150 µs deserialize on
        an 864 MB / 587k-mask atlas — fine for this second-tier miss path, and it removes the ~9 s / GB
        bulk-load-into-RAM startup stall. Per-thread WAL connection (:meth:`_conn`) → free-threading-safe
        under concurrent prefetch-worker reads. Byte-identical to a fresh ``getmask2`` (same round-trip as
        :meth:`load_into`)."""
        try:
            sx, sy = _phase(start)
            row = (
                self._conn()
                .execute(
                    "SELECT offx, offy, w, h, alpha FROM masks "
                    "WHERE font_id=? AND text=? AND mode=? AND sx=? AND sy=?",
                    (font_id, text, mode, sx, sy),
                )
                .fetchone()
            )
            if row is None:
                return None
            offx, offy, w, h, alpha = row
            return deserialize_core(w, h, mode, zlib.decompress(alpha)), (offx, offy)
        except (sqlite3.Error, ValueError):  # pragma: no cover — degrade to live raster (safe)
            log.debug("mask atlas get_one failed", exc_info=True)
            return None

    def count(self) -> int:
        try:
            return int(self._conn().execute("SELECT COUNT(*) FROM masks").fetchone()[0])
        except sqlite3.Error:  # pragma: no cover
            return 0

    def disk_bytes(self) -> int:
        """On-disk size of the atlas DB (``page_count × page_size``). The prewarm heartbeat reports this
        as the real footprint because the atlas is UNBOUNDED — no ``max_bytes`` ceiling, unlike the render
        cache — so size is the number to watch on a ``--limit 0`` sweep. Best-effort → 0."""
        try:
            conn = self._conn()
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            return int(pages) * int(page_size)
        except sqlite3.Error:  # pragma: no cover
            return 0

    def checkpoint(self) -> None:
        """Merge + truncate the WAL — a parallel writer (``prewarm``) with concurrent readers otherwise
        lets it grow unbounded and slow scanning (see the render cache). Best-effort."""
        try:
            self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:  # pragma: no cover
            log.debug("mask atlas checkpoint failed", exc_info=True)

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None


def mem_key(font_id: str, text: str, mode: str, start: tuple[float, float]) -> tuple:
    """The in-memory atlas key ``fonts.glyph_mask`` looks up — the same quantisation :meth:`put` stores
    under, so a live glyph's exact subpixel phase matches its persisted mask."""
    sx, sy = _phase(start)
    return (font_id, text, mode, sx, sy)
