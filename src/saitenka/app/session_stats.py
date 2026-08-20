"""Local immersion-session aggregation and asynchronous SQLite persistence."""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka.app import paths
from saitenka.app.continuity import episode_identity
from saitenka.app.languages import MAIN_LANG, SECOND_LANG

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.app.episode_analysis import EpisodeAnalysis

log = logging.getLogger(__name__)
_DB_PATH_OVERRIDE: Path | None = None
PERSIST_INTERVAL = 5.0


def db_path() -> Path:
    return _DB_PATH_OVERRIDE or paths.data_dir() / "sessions.sqlite"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    media_path: str
    media_name: str
    started_at: float
    ended_at: float | None = None
    completed: bool = False
    watch_seconds: float = 0.0
    cue_count: int = 0
    lookup_count: int = 0
    capture_count: int = 0
    mined_count: int = 0
    jp_seconds: float = 0.0
    en_seconds: float = 0.0
    analysis_json: str | None = None
    title: str = ""
    title_match: str = ""
    episode: int | None = None


def summary(snapshot: SessionSnapshot) -> str:
    """Stable, user-facing summary derived only from recorded session events."""
    minutes = snapshot.watch_seconds / 60
    return (
        f"{minutes:.1f} min watched · {snapshot.cue_count} cues · "
        f"{snapshot.lookup_count} lookups · {snapshot.capture_count} captures · "
        f"{snapshot.mined_count} cards · JP {snapshot.jp_seconds / 60:.1f} min / "
        f"EN {snapshot.en_seconds / 60:.1f} min"
    )


def analysis_snapshot(result: EpisodeAnalysis | None) -> str | None:
    """Persist #66 output for trends without re-tokenizing or re-analyzing the track."""
    if result is None:
        return None
    return json.dumps(
        {
            "content_tokens": result.content_token_count,
            "known_token_coverage": result.known_token_coverage,
            "known_type_coverage": result.known_type_coverage,
            "unique_kanji": len(result.unique_kanji),
            "unique_lemmas": len(result.unique_lemmas),
            "unknown_lemmas": len(result.unknown_lemmas),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class SessionStore:
    def __init__(self, path: Path | None = None):
        target = path or db_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(target)
        self._con.row_factory = sqlite3.Row
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS session (
                session_id TEXT PRIMARY KEY,
                media_path TEXT NOT NULL,
                media_name TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                completed INTEGER NOT NULL,
                watch_seconds REAL NOT NULL,
                cue_count INTEGER NOT NULL,
                lookup_count INTEGER NOT NULL,
                capture_count INTEGER NOT NULL,
                mined_count INTEGER NOT NULL,
                jp_seconds REAL NOT NULL,
                en_seconds REAL NOT NULL,
                analysis_json TEXT,
                title TEXT NOT NULL DEFAULT '',
                title_match TEXT NOT NULL DEFAULT '',
                episode INTEGER
            )
            """
        )
        self._migrate()
        self._con.execute(
            "CREATE INDEX IF NOT EXISTS session_series ON session(title_match, episode)"
        )
        self._con.commit()

    def _migrate(self) -> None:
        """Add #100 episode-identity columns to a pre-existing session table (no framework)."""
        have = {row["name"] for row in self._con.execute("PRAGMA table_info(session)")}
        for name, decl in (
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("title_match", "TEXT NOT NULL DEFAULT ''"),
            ("episode", "INTEGER"),
        ):
            if name not in have:
                self._con.execute(f"ALTER TABLE session ADD COLUMN {name} {decl}")

    def save(self, snapshot: SessionSnapshot) -> None:
        self._con.execute(
            """
            INSERT INTO session (
                session_id, media_path, media_name, started_at, ended_at, completed,
                watch_seconds, cue_count, lookup_count, capture_count, mined_count,
                jp_seconds, en_seconds, analysis_json, title, title_match, episode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                ended_at=excluded.ended_at, completed=excluded.completed,
                watch_seconds=excluded.watch_seconds, cue_count=excluded.cue_count,
                lookup_count=excluded.lookup_count, capture_count=excluded.capture_count,
                mined_count=excluded.mined_count, jp_seconds=excluded.jp_seconds,
                en_seconds=excluded.en_seconds, analysis_json=excluded.analysis_json
            """,
            (
                snapshot.session_id,
                snapshot.media_path,
                snapshot.media_name,
                snapshot.started_at,
                snapshot.ended_at,
                int(snapshot.completed),
                snapshot.watch_seconds,
                snapshot.cue_count,
                snapshot.lookup_count,
                snapshot.capture_count,
                snapshot.mined_count,
                snapshot.jp_seconds,
                snapshot.en_seconds,
                snapshot.analysis_json,
                snapshot.title,
                snapshot.title_match,
                snapshot.episode,
            ),
        )
        self._con.commit()

    def recent(self, limit: int = 20) -> tuple[SessionSnapshot, ...]:
        rows = self._con.execute(
            "SELECT * FROM session ORDER BY started_at DESC LIMIT ?", (max(1, limit),)
        ).fetchall()
        return tuple(
            SessionSnapshot(
                session_id=str(row["session_id"]),
                media_path=str(row["media_path"]),
                media_name=str(row["media_name"]),
                started_at=float(row["started_at"]),
                ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
                completed=bool(row["completed"]),
                watch_seconds=float(row["watch_seconds"]),
                cue_count=int(row["cue_count"]),
                lookup_count=int(row["lookup_count"]),
                capture_count=int(row["capture_count"]),
                mined_count=int(row["mined_count"]),
                jp_seconds=float(row["jp_seconds"]),
                en_seconds=float(row["en_seconds"]),
                analysis_json=str(row["analysis_json"])
                if row["analysis_json"] is not None
                else None,
                title=str(row["title"]),
                title_match=str(row["title_match"]),
                episode=int(row["episode"]) if row["episode"] is not None else None,
            )
            for row in rows
        )

    def close(self) -> None:
        self._con.close()


class AsyncSessionWriter:
    """One SQLite owner thread; producers only enqueue immutable snapshots."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._queue: queue.SimpleQueue[SessionSnapshot | None] = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._run, name="saitenka-session-history", daemon=True
        )
        self._thread.start()

    def submit(self, snapshot: SessionSnapshot) -> None:
        self._queue.put(snapshot)

    def _run(self) -> None:
        store = None
        try:
            store = SessionStore(self._path)
            while (item := self._queue.get()) is not None:
                store.save(item)
        except (OSError, sqlite3.Error):
            log.warning("session history unavailable", exc_info=True)
        finally:
            if store is not None:
                store.close()

    def close(self, timeout: float = 2.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout=timeout)


class SessionRecorder:
    def __init__(
        self,
        media_path: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        writer: AsyncSessionWriter | None = None,
    ):
        now = wall_clock()
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_tick = clock()
        self._last_persist = self._last_tick
        self._last_paused = True
        self._last_language: str = (
            MAIN_LANG  # raw language string from tick(); not the Language role
        )
        self._last_cue: tuple[object, ...] | None = None
        self.writer = writer or AsyncSessionWriter()
        title, title_match, episode = episode_identity(media_path)
        self.snapshot = SessionSnapshot(
            session_id=uuid.uuid4().hex,
            media_path=media_path,
            media_name=Path(media_path).name,
            started_at=now,
            title=title,
            title_match=title_match,
            episode=episode,
        )
        self.writer.submit(self.snapshot)  # crash leaves a durable incomplete session

    def _publish(self, **changes) -> None:
        self.snapshot = replace(self.snapshot, **changes)
        self.writer.submit(self.snapshot)

    def accrue(self, *, paused: bool, language: str) -> None:
        """Close the segment that just ended and open the next.

        Called on a transition — pause, language, close — not on a tick. Elapsed time is measured
        from the previous call either way, so what changes is only *when* the measurement is taken:
        a runtime with no ticks still accrues correctly, and an idle one does no work at all.
        """
        now = self._clock()
        elapsed = max(0.0, now - self._last_tick)
        watch = self.snapshot.watch_seconds
        jp = self.snapshot.jp_seconds
        en = self.snapshot.en_seconds
        was_playing = not self._last_paused
        if was_playing:
            watch += elapsed
            if self._last_language == SECOND_LANG:
                en += elapsed
            else:
                jp += elapsed
        self._last_tick = now
        self._last_paused = paused
        self._last_language = language
        if was_playing and elapsed:
            self.snapshot = replace(
                self.snapshot, watch_seconds=watch, jp_seconds=jp, en_seconds=en
            )
            if now - self._last_persist >= PERSIST_INTERVAL:
                self.writer.submit(self.snapshot)
                self._last_persist = now

    def record_cue(self, identity: tuple[object, ...]) -> bool:
        if identity == self._last_cue:
            return False
        self._last_cue = identity
        self._publish(cue_count=self.snapshot.cue_count + 1)
        return True

    def revise_cue(self, identity: tuple[object, ...]) -> None:
        """Replace the provisional identity of the most recently counted cue."""
        self._last_cue = identity

    def record_lookup(self) -> None:
        self._publish(lookup_count=self.snapshot.lookup_count + 1)

    def record_capture(self) -> None:
        self._publish(capture_count=self.snapshot.capture_count + 1)

    def record_mined(self, count: int = 1) -> None:
        if count > 0:
            self._publish(mined_count=self.snapshot.mined_count + count)

    def finish(self, *, analysis: EpisodeAnalysis | None = None) -> SessionSnapshot:
        self.accrue(paused=True, language=self._last_language)
        self._publish(
            ended_at=self._wall_clock(),
            completed=True,
            analysis_json=analysis_snapshot(analysis),
        )
        self.writer.close()
        return self.snapshot


def start(reader: Reader) -> None:
    if reader.episode.session_recorder is not None or not reader.options.stats.enabled:
        return
    try:
        reader.episode.session_recorder = SessionRecorder(str(reader._prop("path") or ""))
    except (OSError, sqlite3.Error):
        log.warning("session history unavailable", exc_info=True)
        return
    reader.arm_session_persist(PERSIST_INTERVAL)


def accrue(recorder: SessionRecorder | None, *, paused: bool, language: str) -> None:
    """Accrue the watch-time segment that just ended, at a transition."""
    if recorder is not None:
        recorder.accrue(paused=paused, language=language)


def finish(recorder: SessionRecorder | None, analysis: EpisodeAnalysis | None = None) -> str | None:
    """Close a recorder's row and render its summary, or None when nothing was recording.

    Takes the recorder rather than the host: retiring the host's field is the host's business, and
    a close participant that reaches into a `Reader` cannot be driven by the session runtime.
    """
    if recorder is None:
        return None
    return summary(recorder.finish(analysis=analysis))
