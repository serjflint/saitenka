"""Durable per-card record of every mined Anki note ↔ its source episode + cue (#253).

The only prior link from a mined card back to its scene lived inside the Anki note (provenance string +
``saitenka::ep::<nn>`` tags) — nothing enumerable offline. This store stamps, at mine time, the note id
``add_note`` returns plus the episode match-key ``(title_match, episode)`` the backlog/continuity code
already agrees on (:func:`saitenka.app.continuity.episode_identity`) and the cue span, so the sidebar Mine
tab can list an episode's mined cards without Anki and round-trip a preview to the exact line.

Mirrors :class:`saitenka.app.backlog.BacklogStore`: one SQLite connection owned by the Reader thread.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka.app import paths
from saitenka.app.continuity import episode_identity

if TYPE_CHECKING:
    from collections.abc import Callable

_DB_PATH_OVERRIDE: Path | None = None


def db_path() -> Path:
    return _DB_PATH_OVERRIDE or paths.data_dir() / "mined.sqlite"


@dataclass(frozen=True)
class MinedCard:
    id: int
    note_id: int
    title: str
    title_match: str
    episode: int | None
    video_path: str
    cue_start: float
    cue_end: float
    expression: str
    reading: str
    deck: str
    created_at: float


class MinedCardStore:
    """One SQLite connection owned by the Reader thread; a card per mined note."""

    def __init__(self, path: str | Path | None = None, *, clock: Callable[[], float] = time.time):
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._con = sqlite3.connect(self.path)
        self._con.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._con.executescript(
            """
            CREATE TABLE IF NOT EXISTS mined_card (
                id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                title_match TEXT NOT NULL,
                episode INTEGER,
                video_path TEXT NOT NULL,
                cue_start REAL NOT NULL,
                cue_end REAL NOT NULL,
                expression TEXT NOT NULL,
                reading TEXT NOT NULL,
                deck TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS mined_card_episode ON mined_card(title_match, episode);
            CREATE UNIQUE INDEX IF NOT EXISTS mined_card_note ON mined_card(note_id);
            PRAGMA user_version = 1;
            """
        )
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> MinedCardStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _card(row: sqlite3.Row) -> MinedCard:
        return MinedCard(
            id=row["id"],
            note_id=row["note_id"],
            title=row["title"],
            title_match=row["title_match"],
            episode=row["episode"],
            video_path=row["video_path"],
            cue_start=row["cue_start"],
            cue_end=row["cue_end"],
            expression=row["expression"],
            reading=row["reading"],
            deck=row["deck"],
            created_at=row["created_at"],
        )

    def record(
        self,
        *,
        note_id: int,
        video_path: str | Path,
        cue_start: float,
        cue_end: float,
        expression: str,
        reading: str = "",
        deck: str = "",
    ) -> MinedCard:
        """Stamp one mined note. The episode match-key is derived from ``video_path`` so it agrees with
        the backlog/continuity code. Re-mining the same note id updates the existing row (the note is the
        durable identity; the newest scene/cue wins) rather than orphaning a stale link."""
        title, title_match, episode = episode_identity(video_path)
        now = self._clock()
        cur = self._con.execute(
            """
            INSERT INTO mined_card (
                note_id, title, title_match, episode, video_path, cue_start, cue_end,
                expression, reading, deck, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                title=excluded.title, title_match=excluded.title_match, episode=excluded.episode,
                video_path=excluded.video_path, cue_start=excluded.cue_start,
                cue_end=excluded.cue_end, expression=excluded.expression, reading=excluded.reading,
                deck=excluded.deck
            """,
            (
                note_id,
                title,
                title_match,
                episode,
                str(video_path),
                cue_start,
                cue_end,
                expression,
                reading,
                deck,
                now,
            ),
        )
        self._con.commit()
        row_id = cur.lastrowid
        assert row_id is not None
        return self.by_note_id(note_id) or self.card(row_id)

    def card(self, card_id: int) -> MinedCard:
        row = self._con.execute("SELECT * FROM mined_card WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise KeyError(card_id)
        return self._card(row)

    def by_note_id(self, note_id: int) -> MinedCard | None:
        row = self._con.execute("SELECT * FROM mined_card WHERE note_id = ?", (note_id,)).fetchone()
        return self._card(row) if row is not None else None

    def for_episode(self, title_match: str, episode: int | None) -> list[MinedCard]:
        """Every mined card for one ``(title_match, episode)`` key, ordered by cue then insert order.
        ``episode IS ?`` so a None (unparseable episode number) matches its own bucket, not everything."""
        rows = self._con.execute(
            """
            SELECT * FROM mined_card
            WHERE title_match = ? AND episode IS ?
            ORDER BY cue_start, id
            """,
            (title_match, episode),
        ).fetchall()
        return [self._card(row) for row in rows]

    def for_path(self, video_path: str | Path) -> list[MinedCard]:
        """Mined cards for the episode ``video_path`` names — episode-scoped, so a renamed file still
        lists its cards as long as the title/episode parse agrees (same key as the session store)."""
        _title, title_match, episode = episode_identity(video_path)
        return self.for_episode(title_match, episode)

    def all(self) -> list[MinedCard]:
        rows = self._con.execute("SELECT * FROM mined_card ORDER BY id").fetchall()
        return [self._card(row) for row in rows]


def ensure_store(reader) -> MinedCardStore:
    """Lazily open the Reader's session-scoped mined-card store (mirrors ``sidebar._ensure_store`` for
    the backlog). The single seam both the mine-time writer (:mod:`saitenka.app.miner`) and the Mine-tab
    reader (:mod:`saitenka.app.sidebar`) go through."""
    store = reader._mined_store
    if store is None:
        store = reader._mined_store = MinedCardStore()
    return store
