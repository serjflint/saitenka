"""Stream a Yomitan **database export** (dexie-export-import JSON, multi-GB) → standard Yomitan ``.zip``
dictionaries the overlay already loads.

This is the *other* Yomitan input beside a plain dictionary ``.zip`` and the small settings export: the
whole-database backup Yomitan produces (``{"formatName":"dexie",…}``), which for a real collection is
5+ GB. A full ``json.load`` OOMs, so — exactly like Yomitan's own ``Dexie.import`` — we **stream** it
with :mod:`ijson` and reconstruct one ``.zip`` per dictionary (``index.json`` + chunked
``term_bank`` / ``term_meta_bank`` / ``kanji_bank`` / ``kanji_meta_bank`` / ``tag_bank``). The zips then
flow through the existing loader / ``classify_zip`` / config path unchanged.

Row shape (confirmed against a real export): ``inbound:true`` tables (``terms``, ``media``) store the
row value directly; the rest store ``{"$":[key, value]}`` (typeson). Media is skipped (v1). Rows arrive
grouped by table in the header's table order, so each row's table is recovered by its running index
against the header ``rowCount``s — no second pass, constant memory.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ijson

from overlay.app.config import resolve_dictdb


class YomitanDbImportError(RuntimeError):
    pass


# --- row value unwrap + per-table entry converters (pure, unit-tested) --------------------------


def _value(row: Any) -> Any:
    """The stored value for a dexie row: unwrap ``{"$":[key, value]}`` (non-inbound tables), else the
    row itself (inbound tables)."""
    if isinstance(row, dict) and "$" in row and set(row) <= {"$", "$types"}:
        pair = row["$"]
        if isinstance(pair, list) and len(pair) == 2:
            return pair[1]
    return row


def _term_bank_entry(v: dict) -> list:
    # Yomitan term_bank v3: [term, reading, defTags, rules, score, glossary, sequence, termTags]
    return [
        v.get("expression", ""),
        v.get("reading", ""),
        v.get("definitionTags") or "",
        v.get("rules") or "",
        v.get("score", 0),
        v.get("glossary") or [],
        v.get("sequence", 0),
        v.get("termTags") or "",
    ]


def _term_meta_entry(v: dict) -> list:
    return [v.get("expression", ""), v.get("mode", ""), v.get("data")]


def _kanji_bank_entry(v: dict) -> list:
    # kanji_bank v3: [char, onyomi, kunyomi, tags, meanings, stats]
    return [
        v.get("character", ""),
        v.get("onyomi") or "",
        v.get("kunyomi") or "",
        v.get("tags") or "",
        v.get("meanings") or [],
        v.get("stats") or {},
    ]


def _kanji_meta_entry(v: dict) -> list:
    return [v.get("character", ""), v.get("mode", ""), v.get("data")]


def _tag_bank_entry(v: dict) -> list:
    # tag_bank: [name, category, order, notes, score]
    return [
        v.get("name", ""),
        v.get("category") or "",
        v.get("order", 0),
        v.get("notes") or "",
        v.get("score", 0),
    ]


# table name -> (bank filename prefix, value->entry converter)
_ROUTES: dict[str, tuple[str, Callable[[dict], list]]] = {
    "terms": ("term_bank", _term_bank_entry),
    "termMeta": ("term_meta_bank", _term_meta_entry),
    "kanji": ("kanji_bank", _kanji_bank_entry),
    "kanjiMeta": ("kanji_meta_bank", _kanji_meta_entry),
    "tagMeta": ("tag_bank", _tag_bank_entry),
}

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _slug(title: str) -> str:
    return _UNSAFE.sub("_", title).strip() or "dictionary"


def _index_json(meta: dict) -> str:
    idx: dict[str, Any] = {
        "title": meta.get("title", ""),
        "format": 3,
        "revision": str(meta.get("revision") or "imported"),
    }
    if "sequenced" in meta:
        idx["sequenced"] = bool(meta["sequenced"])
    return json.dumps(idx, ensure_ascii=False)


# --- per-dictionary zip writer -----------------------------------------------------------------


class _DictWriter:
    """Accumulates a dictionary's bank entries and flushes chunked ``*_bank_N.json`` into its zip."""

    def __init__(self, out_dir: Path, meta: dict, chunk: int | None = None):
        self.title = meta.get("title", "")
        self.chunk = chunk if chunk is not None else resolve_dictdb().dexie_chunk_size
        self.path = out_dir / f"{_slug(self.title)}.zip"
        self._zf = zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED)
        self._zf.writestr("index.json", _index_json(meta))
        self._buffers: dict[str, list] = {}
        self._counts: dict[str, int] = {}

    def add(self, prefix: str, entry: list) -> None:
        buf = self._buffers.setdefault(prefix, [])
        buf.append(entry)
        if len(buf) >= self.chunk:
            self._flush(prefix)

    def _flush(self, prefix: str) -> None:
        buf = self._buffers.get(prefix)
        if not buf:
            return
        n = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = n
        self._zf.writestr(f"{prefix}_{n}.json", json.dumps(buf, ensure_ascii=False))
        buf.clear()

    def close(self) -> None:
        for prefix in list(self._buffers):
            self._flush(prefix)
        self._zf.close()


# --- streaming import --------------------------------------------------------------------------


def read_header(src: str | Path) -> tuple[list[dict], int]:
    """Return ``(tables, total_rows)`` from the export header, validating the dexie format. Reads only
    the first ~kilobytes (``formatName`` and ``data.tables`` are at the front)."""
    with open(src, "rb") as f:
        try:
            fmt = next(ijson.items(f, "formatName"))
        except (StopIteration, ijson.JSONError) as e:
            raise YomitanDbImportError(
                f"{src}: not a Yomitan database export (no formatName)"
            ) from e
    if fmt != "dexie":
        raise YomitanDbImportError(f"{src}: unsupported export format {fmt!r} (expected 'dexie')")
    with open(src, "rb") as f:
        try:
            tables = next(ijson.items(f, "data.tables"))
        except (StopIteration, ijson.JSONError) as e:
            raise YomitanDbImportError(f"{src}: dexie export has no data.tables") from e
    total = sum(int(t.get("rowCount", 0) or 0) for t in tables)
    return tables, total


def import_database(
    src: str | Path,
    out_dir: str | Path,
    *,
    chunk: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Stream ``src`` (a dexie export) → per-dictionary ``.zip``s in ``out_dir``. Returns the written
    paths. ``progress(done, total)`` is called as rows are consumed (``total`` from the header)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tables, total = read_header(src)

    # Running index → table name, using the header's per-table rowCounts (rows arrive in this order).
    bounds: list[tuple[int, str]] = []
    at = 0
    for t in tables:
        bounds.append((at, str(t.get("name", ""))))
        at += int(t.get("rowCount", 0) or 0)

    writers: dict[str, _DictWriter] = {}

    def _writer_for(title: str, meta: dict | None = None) -> _DictWriter:
        w = writers.get(title)
        if w is None:
            w = writers[title] = _DictWriter(out, meta or {"title": title}, chunk)
        return w

    try:
        with open(src, "rb") as f:
            ptr = 0
            for idx, row in enumerate(ijson.items(f, "data.data.item.rows.item")):
                while ptr + 1 < len(bounds) and idx >= bounds[ptr + 1][0]:
                    ptr += 1
                table = bounds[ptr][1] if bounds else ""
                v = _value(row)
                if not isinstance(v, dict):
                    continue
                if table == "dictionaries":
                    title = v.get("title") or ""
                    if title:
                        _writer_for(title, v)  # creates the zip + index.json
                elif table in _ROUTES:
                    title = v.get("dictionary") or ""
                    if title:
                        prefix, conv = _ROUTES[table]
                        _writer_for(title).add(prefix, conv(v))
                # media / unknown tables: skipped
                if progress is not None:
                    progress(idx + 1, total)
    finally:
        for w in writers.values():
            w.close()
    return sorted(w.path for w in writers.values())
