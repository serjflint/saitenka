"""The app's handle on the consolidated dictionary database — every imported Yomitan dictionary in ONE
SQLite file at ``data_dir()/dictionaries.sqlite``.

**`saitenka-dict` owns this file**: the schema is :mod:`saitenka_dict.schema` and every write goes
through :class:`saitenka_dict.DictionaryDatabase`. What lives here is the *application's* policy over
it — where the file is, how a reading connection is tuned, and the SVG rasterizer handed to an import.
It used to be a second client with a second schema, and the two disagreed about every shared table.

Unlike the old per-zip cache this file is **primary data, not a regenerable cache**: dictionaries are
built into it once, at explicit import time, the way Yomitan imports into its IndexedDB. After import
the source zip is never read again. At runtime the overlay only ever **opens** it read-only.

Every data row is tagged by ``dict_id``, so re-importing one dictionary is a delete-by-title + insert
in a single transaction and never disturbs the others. Definition dictionaries land in ``entries`` /
``keys`` / ``kanji`` / ``tags``; frequency and pitch dictionaries land in ``term_meta`` (mode-tagged).
The classification is by CONTENT (:func:`saitenka_dict.archive.classify_zip`).
"""

from __future__ import annotations

import functools
import gzip
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka_dict import DictionaryDatabase, ImportProgress, ImportRequest
from saitenka_dict.schema import ensure_schema

from saitenka.app import paths
from saitenka.app.config import DictDbOptions, resolve_dictdb
from saitenka.sqlite_pool import ThreadLocalConnections

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

log = logging.getLogger(__name__)

# SVG gaiji are rasterized to PNG at this base height — they are tiny, so 64px stays crisp at any
# tooltip scale.
_MEDIA_PX = 64

# Overridable default DB path — tests point this at a tmp file (mirrors the old CACHE_DIR override).
_DB_PATH_OVERRIDE: Path | None = None

_SVG_FONT_FILES: list[str] | None = None

#: resvg resolves a bundled face by family name, and with system fonts skipped a generic
#: ``font-family: sans-serif`` matches nothing at all — so every generic maps onto the bundled face
#: explicitly. Without this the badge glyph silently renders as the #283 tofu again.
_SVG_FONT_FAMILY = "Noto Sans JP"
_SVG_FONT_FAMILIES = {
    "font_family": _SVG_FONT_FAMILY,
    "serif_family": _SVG_FONT_FAMILY,
    "sans_serif_family": _SVG_FONT_FAMILY,
    "cursive_family": _SVG_FONT_FAMILY,
    "fantasy_family": _SVG_FONT_FAMILY,
    "monospace_family": _SVG_FONT_FAMILY,
}


def _svg_text_font_files() -> list[str]:
    """Font files handed to resvg-py so ``<text>`` gaiji render their glyph, not an empty box (#283).
    The bundled NotoSansJP covers the badge kanji (漢/呉/…) plus Latin; resolved once, then reused.
    Paths, not bytes — ``resvg_py`` loads faces by filename (``resources.asset`` is a real file)."""
    global _SVG_FONT_FILES
    if _SVG_FONT_FILES is None:
        from saitenka.resources import asset

        _SVG_FONT_FILES = [str(asset("fonts", "NotoSansJP.ttf"))]
    return _SVG_FONT_FILES


def _rasterize_svg(resvg_py, name: str, data: bytes) -> bytes | None:
    """One SVG gaiji → PNG bytes, or ``None`` if it failed to render (logged loudly, ▢ fallback kept).
    Only ``<text>`` SVGs get the font DB — loading a ~10 MB face for every path-only gaiji (大辞林 alone
    has thousands) would be pure waste, so gate on the cheap byte check (#283).

    ``skip_system_fonts`` keeps the output a function of the bundled face alone, so a host with (or
    without) a system Noto renders the same bytes."""
    try:
        if (
            data[:2] == b"\x1f\x8b"
        ):  # .svgz payload under a .svg name; usvg used to unwrap it for us
            data = gzip.decompress(data)
        # `log_information` surfaces usvg's "No match for '<family>' font-family" on stderr — the only
        # signal that a text gaiji rendered as a bare box, since that render SUCCEEDS. Its absence is
        # how #283 hid. Text SVGs only: it is the one diagnostic here that can be acted on.
        fonts = (
            {"font_files": _svg_text_font_files(), "log_information": True, **_SVG_FONT_FAMILIES}
            if b"<text" in data
            else {}
        )
        png = resvg_py.svg_to_bytes(
            svg_string=data.decode(),
            height=_MEDIA_PX,
            skip_system_fonts=True,
            **fonts,
        )
    except Exception as e:  # noqa: BLE001  # incl. a pyo3 panic — one bad glyph must not abort the whole import
        log.warning("resvg-py failed on %s: %s — leaving ▢ fallback", name, e)
        return None
    return png


def _svg_rasterizer() -> Callable[[str, bytes], bytes | None] | None:
    """The rasterizer handed to an import, or ``None`` when the optional ``images`` extra is absent —
    in which case a default install imports byte-identically to before and the renderer keeps drawing ▢."""
    try:
        import resvg_py  # noqa: TID251  # SVG-images chokepoint: this is the one sanctioned importer
    except ImportError:
        return None
    return functools.partial(_rasterize_svg, resvg_py)


def _bank_progress(
    on_bank: Callable[[int, int], None] | None,
) -> Callable[[ImportProgress], None] | None:
    """Adapt the app's ``(done, total)`` progress callback onto the package's richer record."""
    if on_bank is None:
        return None
    return lambda progress: on_bank(progress.completed, progress.total)


def default_db_path() -> Path:
    return paths.data_dir() / "dictionaries.sqlite"


def db_path() -> Path:
    """The consolidated DB path: the test/env override if set, else the platform data-dir default."""
    return _DB_PATH_OVERRIDE or default_db_path()


@dataclass(frozen=True)
class DictRow:
    """A row of the ``dictionaries`` table — one imported dictionary."""

    id: int
    title: str
    kind: str  # 'dict' | 'freq' | 'pitch'
    import_order: int
    source_name: str
    revision: str


@dataclass(frozen=True)
class DictStat:
    """One imported dictionary's content-free inventory: its :class:`DictRow` plus per-table row
    counts. A ``dict``-kind dictionary with ``entries`` but ``tags == 0`` is the tell of a
    sidecar-era import that predates tags-in-sqlite — re-import to populate the tag pills."""

    row: DictRow
    imported_at: str
    schema_version: int
    counts: dict[str, int]  # entries / keys / kanji / term_meta / tags


@dataclass(frozen=True)
class DbStats:
    """A content-free snapshot of the consolidated DB — safe to put in a diagnostics bundle: it names
    no term, reading, or gloss, only the schema, file size, and per-dictionary row counts."""

    path: Path
    exists: bool
    size_bytes: int
    schema: int | None
    dicts: list[DictStat]


def _open_read(path: Path, opts: DictDbOptions) -> sqlite3.Connection:
    """One thread's read-only lookup connection.

    mmap a MODEST window of the DB so cold lookups hit page-cache-backed memory instead of pread
    syscalls. Kept small (256 MiB, was 1 GiB) because the DB is multi-GB and this runs on EACH
    per-thread connection (main + prefetch workers); on Windows the mapped view counts toward the
    process working set, so a 1 GiB window × N threads was inflating RAM by gigabytes. A benchmark
    showed the mmap win over pread was mostly a page-cache artifact, so shrinking the window costs
    ~nothing. 32 MiB page cache per connection (negative = KiB) — both tunable via ``[dictdb]`` in
    overlay.toml (app/config.py: DictDbOptions).

    A free function, not a method: :class:`~saitenka.sqlite_pool.ThreadLocalConnections` must not
    close over its owner.
    """
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    c.execute(f"PRAGMA mmap_size={opts.mmap_size}")
    c.execute(f"PRAGMA cache_size=-{opts.cache_size_kib}")
    return c


class DictionaryDb:
    """Read/write handle to the consolidated dictionary DB.

    Read connections are per-thread and read-only (safe parallel lookups); the single write connection
    is used only by :meth:`import_zip`. WAL mode lets readers proceed during an import.
    """

    def __init__(self, path: str | Path, db_opts: DictDbOptions | None = None):
        self.path = Path(path)
        self._opts = db_opts if db_opts is not None else resolve_dictdb()
        self._reads = ThreadLocalConnections(
            self, functools.partial(_open_read, self.path, self._opts)
        )
        self._media_present: bool | None = None  # cached once: is the media table non-empty? (#283)

    # --- lifecycle ----------------------------------------------------------------------------

    @classmethod
    def open(
        cls, path: str | Path | None = None, db_opts: DictDbOptions | None = None
    ) -> DictionaryDb:
        """Open (creating + schema-initialising if needed) the consolidated DB."""
        db = cls(path or db_path(), db_opts)
        db.path.parent.mkdir(parents=True, exist_ok=True)
        db._ensure_schema()
        return db

    def _ensure_schema(self) -> None:
        conn = sqlite3.connect(self.path)
        try:
            ensure_schema(conn)
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        """A per-thread read-only connection (mmap'd, roomy page cache) for lookups."""
        return self._reads.get()

    def meta_get(self, key: str) -> str | None:
        row = self._conn().execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, value))
            conn.commit()
        finally:
            conn.close()

    # --- import (delegated: `saitenka-dict` owns every write to this file) --------------------

    def import_zip(
        self,
        zip_path: str | Path,
        *,
        imported_at: str,
        import_order: int = 0,
        on_bank: Callable[[int, int], None] | None = None,
    ) -> DictRow:
        """Import one Yomitan dictionary zip, replacing any prior import of the same title.

        The write itself belongs to :class:`saitenka_dict.DictionaryDatabase` — this is the app's
        adapter onto it: it supplies the SVG rasterizer (which needs the bundled font and the optional
        ``images`` extra, both application concerns), maps ``on_bank`` onto the package's richer
        progress record, and returns the :class:`DictRow` the app's callers expect.
        """
        request = ImportRequest(
            Path(zip_path),
            import_order=import_order,
            imported_at=imported_at,
            on_progress=_bank_progress(on_bank),
            rasterize_svg=_svg_rasterizer(),
            persist_seq=self._opts.persist_seq,
        )
        info = DictionaryDatabase(self.path).import_dictionary(request)
        found, _missing = self.resolve([info.title])
        return found[0]

    def media_for(self, dict_id: int, paths: Iterable[str]) -> dict[str, bytes]:
        """Preloaded ``{path: image_bytes}`` for the given img paths — called at Entry-build (lookup
        thread) so the render/prefetch thread never touches SQLite. Empty when the extra never populated
        the table (default install) or none of the paths resolved → renderer falls back to ▢.

        Reuses the per-thread read-only ``_conn()`` (no per-call connect), and on a default install the
        cached emptiness check makes this a no-op after the first lookup — so the no-extra path stays free."""
        wanted = list(dict.fromkeys(paths))
        if not wanted or not self._has_media():
            return {}
        qs = ",".join("?" * len(wanted))
        cur = self._conn().execute(
            f"SELECT path, png FROM media WHERE dict_id=? AND path IN ({qs})",  # noqa: S608  # qs is only ? placeholders; paths are parameterized
            (dict_id, *wanted),
        )
        return dict(cur.fetchall())

    def _has_media(self) -> bool:
        """Whether the media table holds any row — cached for the session. On a default install it stays
        empty, so this short-circuits ``media_for`` to zero per-lookup queries."""
        if self._media_present is None:
            row = self._conn().execute("SELECT EXISTS(SELECT 1 FROM media)").fetchone()
            self._media_present = bool(row[0])
        return self._media_present

    # --- queries -------------------------------------------------------------------------------

    def drop(self, title: str) -> bool:
        """Remove an imported dictionary by title. Returns True if it existed."""
        return DictionaryDatabase(self.path).remove_dictionary(title)

    @staticmethod
    def _row_from(r: Sequence) -> DictRow:
        return DictRow(int(r[0]), r[1], r[2], int(r[3] or 0), r[4] or "", r[5] or "")

    def list_dictionaries(self) -> list[DictRow]:
        """Every imported dictionary, ordered by import_order then id."""
        rows = self._conn().execute(
            "SELECT id, title, kind, import_order, source_name, revision FROM dictionaries "
            "ORDER BY import_order, id"
        )
        return [self._row_from(r) for r in rows]

    def resolve(self, titles: Sequence[str]) -> tuple[list[DictRow], list[str]]:
        """Map an ordered list of titles to their imported :class:`DictRow`s, preserving order.

        Returns ``(found, missing)`` — ``missing`` lists titles with no imported dictionary (the
        caller warns the user to run ``import``). Duplicate/unknown titles never raise."""
        by_title = {r.title: r for r in self.list_dictionaries()}
        found = [by_title[t] for t in titles if t in by_title]
        missing = [t for t in titles if t not in by_title]
        return found, missing

    def dict_counts(self, dict_id: int) -> dict[str, int]:
        """Row counts per table for one dictionary — for tests and doctor."""
        c = self._conn()
        return {
            t: c.execute(
                f"SELECT COUNT(*) FROM {t} WHERE dict_id=?",  # noqa: S608  # table name is an internal constant; the value is parameterized with ?
                (dict_id,),
            ).fetchone()[0]
            for t in ("entries", "keys", "kanji", "term_meta", "tags")
        }

    def stats(self) -> DbStats:
        """Content-free inventory (schema, file size, per-dictionary row counts) for report/doctor —
        no term/reading/gloss text, so it's safe in a shared diagnostics bundle. One grouped read of
        the ``dictionaries`` table plus :meth:`dict_counts` per dict (a handful, off any hot path)."""
        if not self.path.exists():
            return DbStats(self.path, exists=False, size_bytes=0, schema=None, dicts=[])
        raw = self.meta_get("schema")
        meta = {
            int(r[0]): (r[1] or "", int(r[2] or 0))
            for r in self._conn().execute(
                "SELECT id, imported_at, schema_version FROM dictionaries"
            )
        }
        dicts = [
            DictStat(
                row,
                meta.get(row.id, ("", 0))[0],
                meta.get(row.id, ("", 0))[1],
                self.dict_counts(row.id),
            )
            for row in self.list_dictionaries()
        ]
        return DbStats(
            self.path,
            exists=True,
            size_bytes=self.path.stat().st_size,
            schema=int(raw) if raw is not None and raw.isdigit() else None,
            dicts=dicts,
        )
