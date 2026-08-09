"""Word-pronunciation audio from a local yomichan/yomitan audio pack (#93, offline/grounded).

A pack directory holds an ``index.json`` (the format shared by yomichan/yomitan "local audio" packs —
e.g. NHK/Shinmeikai-derived collections) plus the audio files it indexes: a table mapping a TERM to its
READING(s) to one or more audio file paths (relative to the pack dir). No specific pack is hardcoded —
any directory whose ``index.json`` matches this shape resolves; discovery is by content, not by name.

Grounded/offline: the file is keyed on the reading mining ALREADY resolved (homograph-safe), never
synthesized — matches the project's "readings/pitch/audio come from local sources, never a model" rule.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

_INDEX_NAME = "index.json"
# Keys a pack's top-level JSON may nest the term->reading->files table under, checked in order. A pack
# whose JSON IS the table (no wrapper) falls through to the raw dict itself.
_TABLE_KEYS = ("index", "media", "words", "sources")


@dataclass(frozen=True)
class AudioHit:
    """One resolved word-audio file: absolute path on disk + the filename to store it under in Anki."""

    path: Path
    filename: str


def _entry_files(value: object) -> list[str]:
    """Normalize one reading's entry — a filename string, a ``{"path"/"file"/"name": ...}`` dict, or a
    list of either — to a flat list of filenames. Anything else (a number, ``None``, …) yields none."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        name = value.get("path") or value.get("file") or value.get("name")
        return [str(name)] if name else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_entry_files(item))
        return out
    return []


def _table(raw: object) -> dict:
    """The term->reading->entry table from a parsed index.json — unwrapped from a known container key,
    or the raw dict itself when the JSON has no wrapper."""
    if not isinstance(raw, dict):
        return {}
    for key in _TABLE_KEYS:
        table = raw.get(key)
        if isinstance(table, dict):
            return table
    return raw


def _normalize_readings(readings: object) -> dict[str, list[str]]:
    """One term's raw reading→entry map → ``{reading: [filenames]}``, dropping any malformed reading
    key or entry that yields no files."""
    if not isinstance(readings, dict):
        return {}
    out: dict[str, list[str]] = {}
    for reading, entry in readings.items():
        files = _entry_files(entry) if isinstance(reading, str) else []
        if files:
            out[reading] = files
    return out


def _parse_table(table: dict) -> dict[str, dict[str, list[str]]]:
    """The raw term->reading->entry table → the normalized index, dropping any malformed term."""
    out: dict[str, dict[str, list[str]]] = {}
    for term, readings in table.items():
        if not isinstance(term, str):
            continue
        normalized = _normalize_readings(readings)
        if normalized:
            out[term] = normalized
    return out


def load_index(pack_dir: Path) -> dict[str, dict[str, list[str]]]:
    """Parse ``pack_dir/index.json`` into ``{term: {reading: [filenames]}}``. Returns ``{}`` on any
    parse failure or a missing/malformed index — a bad or absent pack degrades to a clean miss, never
    a crash (offline packs are user-supplied, arbitrary directories)."""
    p = pack_dir / _INDEX_NAME
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning("word-audio pack %s: couldn't parse %s: %s", pack_dir, _INDEX_NAME, e)
        return {}
    return _parse_table(_table(raw))


def resolve(pack_dir: Path, term: str, reading: str) -> AudioHit | None:
    """The first on-disk audio file for ``(term, reading)`` in the pack at ``pack_dir``, or ``None`` on
    any miss: an unconfigured/missing pack dir, a missing/malformed index, the term/reading not indexed,
    or every indexed file for it absent on disk."""
    if not term or not reading or not pack_dir.is_dir():
        return None
    readings = load_index(pack_dir).get(term)
    if not readings:
        return None
    root = pack_dir.resolve()
    for fname in readings.get(reading, ()):
        candidate = (pack_dir / fname).resolve()
        # Containment gate: a shared/downloaded pack's index.json is untrusted input. Reject any entry
        # that escapes the pack dir — `../…` traversal OR an absolute path (pathlib's `/` discards the
        # base for an absolute rhs) — else store_media would read+upload an arbitrary local file into Anki.
        if not candidate.is_relative_to(root):
            log.warning(
                "word-audio pack %s: entry %r escapes the pack dir — skipped", pack_dir, fname
            )
            continue
        if candidate.is_file():
            return AudioHit(path=candidate, filename=candidate.name)
    return None
