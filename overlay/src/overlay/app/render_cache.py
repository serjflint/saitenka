"""Cross-session persistent render cache — the disk half of the tooltip precompose (#149).

1.3.0's in-process ``WindowedPanel.precompose`` moved a warm hover's first-viewport raster+BGRA-convert
off the main thread into the prefetch worker, so a hover with idle lead is a copy+upload. But a **cold**
hover — the first-ever look at a word, before any worker got to it — still pays the full head raster
(tens of ms; a pathological multi-dict / very tall entry blows the 16 ms budget, ~40–157 ms in the
cold-first-paint bench). This is the residual > 16 ms the release left.

This module persists the precomposed first-viewport premul-BGRA of **cost-gated (pathological) entries
only** to a small SQLite cache under ``cache_dir()``, so a cold hover in a *later* session (or after an
offline ``saitenka prewarm``) seeds ``_first_view`` from disk and skips the head raster+convert — the
copy+upload fast path, cold. It is:

- **opt-in** (``[tooltip] render_cache``, default off) — off it costs nothing;
- **cost-gated** (only entries whose composited head clears ``min_height`` px are stored) so the on-disk
  size stays bounded to the jank tail, not the whole ~32k population (a full-population pixel cache is
  ~1.15 GiB — the wrong shape, per #149);
- **bounded** (LRU-evicted to ``max_bytes``);
- **safe on any miss / error** — a mismatch or a corrupt/locked DB degrades to a live render, never a
  wrong pixel: the ``config_sig`` folds in width, cap, theme-format version and the dictionary-set
  identity, so a resolution / dict change simply misses and rebuilds (the rare-event contract in #149).

Writes happen off the main thread (the prefetch worker's precompose, or the offline builder); reads are
one indexed ``SELECT`` + ``zlib`` inflate on the cold-show path. WAL mode lets the worker write while the
main thread reads. Connections are per-thread (free-threading-safe), mirroring ``dictdb``.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

    from overlay.app.dictionary import DictionarySet

log = logging.getLogger(__name__)

# Bumped when the stored artifact's meaning changes (theme colours/margins/fonts, the premul-BGRA
# layout, the compositor's pixel output) — an old row then can't match a new config_sig, so a format
# change invalidates the whole cache instead of serving stale pixels. Theme is folded in here rather
# than hashed per-panel because it is a process-wide constant today (the default theme).
_FORMAT_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS heads (
    config_sig  TEXT NOT NULL,
    content_key TEXT NOT NULL,
    view_h      INTEGER NOT NULL,
    overscan    INTEGER NOT NULL,
    full_h      INTEGER NOT NULL,
    h           INTEGER NOT NULL,
    w           INTEGER NOT NULL,
    blob        BLOB NOT NULL,
    nbytes      INTEGER NOT NULL,
    used_seq    INTEGER NOT NULL,
    protected   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (config_sig, content_key)
);
CREATE INDEX IF NOT EXISTS idx_heads_evict ON heads(protected, used_seq);
"""


@dataclass(frozen=True, slots=True)
class LoadedView:
    """A precomposed first viewport read back from disk: the premul-BGRA plus the geometry a cold show
    needs to paint it WITHOUT building the panel — ``view_h``/``overscan`` (what it was composited for)
    and ``full_h`` (placement + scrollbar). :meth:`WindowedPanel.install_first_view` uses the array;
    the direct-paint show path uses ``full_h`` too."""

    view_h: int
    overscan: int
    full_h: int
    array: np.ndarray


def dict_set_signature(ds: DictionarySet) -> str:
    """Identity of the dictionary set + underlying DB — folded into ``config_sig`` so a re-import (which
    can change an entry's glossary, hence its rendered pixels) invalidates the cache. Titles capture the
    active set + order; the DB file's size+mtime capture its content without hashing multi-GB of data."""
    titles = "\x1f".join(
        [
            *(d.title for d in ds.dicts),
            "|freq|",
            *(f.title for f in ds.freqs),
            "|pitch|",
            *(p.title for p in ds.pitches),
        ]
    )
    stamp = ""
    if ds.dicts:
        try:
            st = Path(ds.dicts[0].db.path).stat()
            stamp = f"{st.st_size}:{st.st_mtime_ns}"
        except (
            OSError
        ):  # pragma: no cover — DB vanished mid-session; a bare title sig still keys safely
            stamp = ""
    return f"{titles}\x1e{stamp}"


def config_signature(*, width: int, cap: int, dict_sig: str) -> str:
    """The cache partition key: format version + tooltip width + viewport cap + dict-set identity. Any
    change (a different video resolution → different width/cap, a re-import → different ``dict_sig``)
    lands in a different partition, so stale-config rows never match — they age out by LRU."""
    return f"v{_FORMAT_VERSION}|w{width}|c{cap}|{dict_sig}"


class RenderCache:
    """A small, bounded, cross-session SQLite store of precomposed tooltip first-viewports.

    Every public method is best-effort: any :class:`sqlite3.Error` is swallowed and logged at DEBUG, so
    a cache problem degrades to live rendering and never surfaces to the user. Connections are per-thread
    (the main thread reads, the prefetch workers / offline builder write) with WAL enabled for
    concurrent read-while-write."""

    def __init__(self, path: str | Path, *, max_bytes: int):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self._local = threading.local()
        self._seq_lock = threading.Lock()
        self._seq = 0
        self._ensure_schema()

    @classmethod
    def open(cls, path: str | Path, *, max_bytes: int) -> RenderCache | None:
        """Open (creating if needed) the cache, or ``None`` if it can't be initialised — the caller then
        runs with no persistent cache rather than failing to start."""
        try:
            return cls(path, max_bytes=max_bytes)
        except sqlite3.Error:  # pragma: no cover — disk full / permissions; degrade to no cache
            log.debug("render cache unavailable at %s", path, exc_info=True)
            return None

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT MAX(used_seq) FROM heads").fetchone()
            self._seq = int(row[0]) + 1 if row and row[0] is not None else 0
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(
                "PRAGMA synchronous=NORMAL"
            )  # WAL + NORMAL: durable enough for a rebuildable cache
            self._local.conn = c
        return c

    def _next_seq(self) -> int:
        with self._seq_lock:
            s = self._seq
            self._seq += 1
            return s

    def get(
        self, config_sig: str, content_key: str, view_h: int, overscan: int
    ) -> LoadedView | None:
        """The stored first-viewport for ``(config_sig, content_key)`` **iff** its ``view_h``/``overscan``
        match the show about to happen (a differing full-height → differing view_h simply misses). For
        seeding a BUILT panel's ``_first_view``. Bumps LRU recency on a hit. ``None`` on miss / any error."""
        loaded = self.peek(config_sig, content_key)
        if loaded is None or loaded.view_h != view_h or loaded.overscan != overscan:
            return None  # geometry moved (content/height changed) — safe miss, live-render instead
        return loaded

    def peek(self, config_sig: str, content_key: str) -> LoadedView | None:
        """The stored first-viewport for ``(config_sig, content_key)`` regardless of geometry — the
        direct-paint path, which paints the pixels + places by ``full_h`` WITHOUT building a panel first,
        then reconciles against the panel it builds afterward (same content → same geometry). Bumps LRU
        recency. ``None`` on miss / any error."""
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT view_h, overscan, full_h, h, w, blob FROM heads "
                "WHERE config_sig=? AND content_key=?",
                (config_sig, content_key),
            ).fetchone()
            if row is None:
                return None
            view_h, overscan, full_h, h, w, blob = row
            arr = np.frombuffer(zlib.decompress(blob), dtype=np.uint8).reshape(h, w, 4)
            conn.execute(
                "UPDATE heads SET used_seq=? WHERE config_sig=? AND content_key=?",
                (self._next_seq(), config_sig, content_key),
            )
            conn.commit()
            return LoadedView(view_h, overscan, full_h, arr)
        except (sqlite3.Error, ValueError):  # ValueError: a truncated/garbled blob won't reshape
            log.debug("render cache peek failed", exc_info=True)
            return None

    def has(self, config_sig: str, content_key: str) -> bool:
        """Is ``(config_sig, content_key)`` already stored? A cheap index probe (no blob read / LRU
        bump) so the offline prewarm can SKIP an entry already present — resumable, incremental re-runs.
        ``False`` on any error (→ the caller renders it, at worst redoing one row)."""
        try:
            return (
                self._conn()
                .execute(
                    "SELECT 1 FROM heads WHERE config_sig=? AND content_key=? LIMIT 1",
                    (config_sig, content_key),
                )
                .fetchone()
                is not None
            )
        except sqlite3.Error:  # pragma: no cover
            return False

    def put(
        self,
        config_sig: str,
        content_key: str,
        view_h: int,
        overscan: int,
        full_h: int,
        array: np.ndarray,
        *,
        protected: bool = False,
    ) -> None:
        """Store ``array`` (a premul-BGRA first viewport) + its ``full_h`` for ``(config_sig,
        content_key)``, then trim to ``max_bytes``. ``protected`` (the offline ``prewarm``'s popular set)
        is evicted LAST — a live write-back (``protected=False``) of a rarer hovered word can only evict
        another unprotected entry, never a prewarmed popular one, so the capped cache never thrashes its
        stable core. Best-effort: any error is swallowed. Idempotent (``INSERT OR REPLACE``)."""
        try:
            arr = np.ascontiguousarray(array, dtype=np.uint8)
            h, w = arr.shape[0], arr.shape[1]
            blob = zlib.compress(arr.tobytes(), 1)
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO heads (config_sig, content_key, view_h, overscan, full_h, "
                "h, w, blob, nbytes, used_seq, protected) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    config_sig,
                    content_key,
                    view_h,
                    overscan,
                    full_h,
                    h,
                    w,
                    blob,
                    len(blob),
                    self._next_seq(),
                    1 if protected else 0,
                ),
            )
            conn.commit()
            self._trim(conn)
        except sqlite3.Error:
            log.debug("render cache put failed", exc_info=True)

    def _trim(self, conn: sqlite3.Connection) -> None:
        """Evict until within ``max_bytes``, UNPROTECTED-and-oldest first (protected prewarm rows go
        last) — so live write-back fills headroom + churns only itself, never the prewarmed popular set."""
        total = conn.execute("SELECT COALESCE(SUM(nbytes), 0) FROM heads").fetchone()[0]
        if total <= self.max_bytes:
            return
        over = total - self.max_bytes
        freed = 0
        victims: list[tuple[str, str]] = []
        for cs, ck, nbytes in conn.execute(
            "SELECT config_sig, content_key, nbytes FROM heads ORDER BY protected ASC, used_seq ASC"
        ):
            victims.append((cs, ck))
            freed += nbytes
            if freed >= over:
                break
        conn.executemany("DELETE FROM heads WHERE config_sig=? AND content_key=?", victims)
        conn.commit()
        from overlay import otel_metrics

        if victims and otel_metrics.render_cache_evictions is not None:
            otel_metrics.render_cache_evictions.add(len(victims))

    def checkpoint(self) -> None:
        """Merge the WAL back into the main DB and truncate it. A long parallel writer (``prewarm``) with
        many concurrent readers keeps the passive autocheckpoint from ever advancing past the oldest
        reader, so the WAL grows unbounded (measured 900 MB+ mid-prebuild) and every op slows scanning it.
        Calling this periodically caps the WAL. Best-effort: a reader-held ``TRUNCATE`` just reclaims less."""
        try:
            self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:  # pragma: no cover — busy/locked; the next checkpoint retries
            log.debug("render cache checkpoint failed", exc_info=True)

    def stats(self) -> tuple[int, int]:
        """``(row_count, total_blob_bytes)`` — for the doctor/report surface and the prewarm summary."""
        try:
            conn = self._conn()
            n = conn.execute("SELECT COUNT(*) FROM heads").fetchone()[0]
            b = conn.execute("SELECT COALESCE(SUM(nbytes), 0) FROM heads").fetchone()[0]
            return int(n), int(b)
        except sqlite3.Error:  # pragma: no cover
            return 0, 0

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None


def content_key(key: Sequence[object]) -> str:
    """Stable string form of a :class:`~overlay.app.tooltip.PanelKey` (or any tuple) for the cache. The
    key already carries everything that changes the head pixels — lemma/surface/reading/inflected, the
    ⊕-vs-✓ mined + anki-reachable header state, per-group mined flags, and stacked-phrase terms — so two
    renders sharing a content_key are byte-identical (width/cap/theme live in ``config_sig``)."""
    return "\x1f".join(repr(x) for x in key)
