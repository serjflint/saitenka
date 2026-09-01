from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

KnownRows = dict[str, dict[int, tuple[int, list[list[str]]]]]
log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes(
  deck TEXT NOT NULL, note_id INTEGER NOT NULL, mod INTEGER NOT NULL, words TEXT NOT NULL,
  PRIMARY KEY(deck, note_id));
"""
_READ = "SELECT deck, note_id, mod, words FROM notes WHERE deck IN (SELECT value FROM json_each(?))"


@dataclass(frozen=True, slots=True)
class KnownCacheUpdate:
    deck: str
    upserts: tuple[tuple[int, int, list[list[str]]], ...] = ()
    deleted_ids: tuple[int, ...] = ()


class KnownWordCache:
    """Persistent Anki-known forms, independent of the dictionary database lifecycle."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @classmethod
    def open(cls, path: str | Path, *, legacy_path: str | Path | None = None) -> KnownWordCache:
        """``path`` is required: where the cache lives is the composition root's decision, not this
        module's. Both callers already pass one — the XDG default here was reachable only from a test."""
        cache = cls(path)
        cache.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(cache.path)
        try:
            connection.executescript(_SCHEMA)
            if legacy_path is not None:
                cls._migrate_legacy(connection, Path(legacy_path))
            connection.commit()
        finally:
            connection.close()
        return cache

    @staticmethod
    def _migrate_legacy(connection: sqlite3.Connection, legacy_path: Path) -> None:
        if not legacy_path.exists():
            return
        if connection.execute("SELECT 1 FROM metadata WHERE key='legacy-migrated'").fetchone():
            return
        legacy = sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True)
        try:
            has_rows = legacy.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='anki_known_cache'"
            ).fetchone()
            if has_rows:
                rows = legacy.execute(
                    "SELECT deck, note_id, mod, words FROM anki_known_cache"
                ).fetchall()
                connection.executemany(
                    "INSERT OR IGNORE INTO notes(deck, note_id, mod, words) VALUES(?, ?, ?, ?)",
                    rows,
                )
            has_meta = legacy.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if has_meta:
                signature = legacy.execute("SELECT v FROM meta WHERE k='anki_known_sig'").fetchone()
                if signature:
                    connection.execute(
                        "INSERT OR IGNORE INTO metadata(key, value) VALUES('anki_known_sig', ?)",
                        (signature[0],),
                    )
            connection.execute("INSERT INTO metadata(key, value) VALUES('legacy-migrated', '1')")
        except sqlite3.DatabaseError:
            log.warning("could not migrate legacy known-word cache", exc_info=True)
        finally:
            legacy.close()

    def read(self, decks: Sequence[str]) -> KnownRows:
        result: KnownRows = {deck: {} for deck in decks}
        if not decks:
            return result
        connection = sqlite3.connect(self.path)
        try:
            rows = connection.execute(_READ, (json.dumps(decks, ensure_ascii=False),))
            for deck, note_id, mod, words in rows:
                result.setdefault(deck, {})[note_id] = (mod, json.loads(words))
        finally:
            connection.close()
        return result

    def write(self, update: KnownCacheUpdate) -> None:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.executemany(
                    "DELETE FROM notes WHERE deck=? AND note_id=?",
                    ((update.deck, note_id) for note_id in update.deleted_ids),
                )
                connection.executemany(
                    "INSERT OR REPLACE INTO notes(deck, note_id, mod, words) VALUES(?, ?, ?, ?)",
                    (
                        (update.deck, note_id, mod, json.dumps(words, ensure_ascii=False))
                        for note_id, mod, words in update.upserts
                    ),
                )
        finally:
            connection.close()

    def metadata(self, key: str) -> str | None:
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            connection.close()

    def set_metadata(self, key: str, value: str) -> None:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES(?, ?)", (key, value))
            connection.commit()
        finally:
            connection.close()


def known_cache_for(store: object) -> KnownWordCache:
    if isinstance(store, KnownWordCache):
        return store
    path = getattr(store, "path", None)
    if path is None:
        raise TypeError("known-word cache must be a KnownWordCache or expose a database path")
    dictionary_path = Path(path)
    return KnownWordCache.open(dictionary_path.with_name("anki-known.sqlite"), legacy_path=path)
