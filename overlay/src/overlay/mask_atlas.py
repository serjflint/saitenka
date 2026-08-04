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
prebuilt ``mask-atlas.sqlite`` bulk-loads it into a shared read dict (:func:`load_into` — ~2–3 s + ~150 MB
RAM) on a background thread and writes back live misses; no prebuilt atlas → nothing loads. A per-glyph
SQLite lookup would be too slow for the hot path, hence the one-pass in-memory load. The alpha bytes
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
    degrades to live rasterisation. Per-thread write connections (WAL); reads go through :meth:`load_into`
    in one pass, never per-glyph (too slow for the hot path)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
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
        """Persist one ``getmask2`` result (``mask`` = ``(core, (offx, offy))``). Idempotent."""
        try:
            core, (offx, offy) = mask
            w, h, data = serialize_core(core)
            sx, sy = _phase(start)
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO masks (font_id, text, mode, sx, sy, offx, offy, w, h, alpha) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (font_id, text, mode, sx, sy, offx, offy, w, h, zlib.compress(data, 1)),
            )
            conn.commit()
        except sqlite3.Error:
            log.debug("mask atlas put failed", exc_info=True)

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

    def count(self) -> int:
        try:
            return int(self._conn().execute("SELECT COUNT(*) FROM masks").fetchone()[0])
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
