from __future__ import annotations

from dataclasses import replace

from saitenka.app.features.history.history_owner import HistoryOwner
from saitenka.app.session_stats import (
    AsyncSessionWriter,
    SessionRecorder,
    SessionSnapshot,
    SessionStore,
)


class FakeWriter:
    def __init__(self):
        self.snapshots = []
        self.closed = False

    def submit(self, snapshot):
        self.snapshots.append(snapshot)

    def close(self, _timeout=2.0):
        self.closed = True


def test_async_writer_persists_distinct_and_incomplete_sessions(tmp_path):
    path = tmp_path / "sessions.sqlite"
    first = SessionSnapshot("first", "/a/01.mkv", "01.mkv", 1.0)
    second = SessionSnapshot(
        "second",
        "/a/Show 12.mkv",
        "Show 12.mkv",
        2.0,
        title="Show",
        title_match="show",
        episode=12,
    )
    writer = AsyncSessionWriter(path)
    writer.submit(first)
    writer.submit(second)
    writer.submit(replace(second, ended_at=3.0, completed=True, cue_count=4))
    writer.close()

    store = SessionStore(path)
    rows = store.recent()
    store.close()

    assert [(row.session_id, row.completed, row.cue_count) for row in rows] == [
        ("second", True, 4),
        ("first", False, 0),
    ]
    assert (rows[0].title, rows[0].title_match, rows[0].episode) == ("Show", "show", 12)


def test_store_migrates_pre_episode_schema(tmp_path):
    # A v1 DB with the original 14-column table must gain the episode columns on open.
    import sqlite3

    path = tmp_path / "sessions.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE session (
            session_id TEXT PRIMARY KEY, media_path TEXT NOT NULL, media_name TEXT NOT NULL,
            started_at REAL NOT NULL, ended_at REAL, completed INTEGER NOT NULL,
            watch_seconds REAL NOT NULL, cue_count INTEGER NOT NULL, lookup_count INTEGER NOT NULL,
            capture_count INTEGER NOT NULL, mined_count INTEGER NOT NULL, jp_seconds REAL NOT NULL,
            en_seconds REAL NOT NULL, analysis_json TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO session VALUES ('old','/a/1.mkv','1.mkv',1.0,NULL,0,0,0,0,0,0,0,0,NULL)"
    )
    con.commit()
    con.close()

    store = SessionStore(path)
    (row,) = store.recent()
    # A fresh save must still land against the migrated columns.
    store.save(SessionSnapshot("new", "/a/Show 03.mkv", "Show 03.mkv", 2.0, episode=3))
    store.close()

    assert (row.session_id, row.title, row.episode) == ("old", "", None)


def test_history_owner_finish_retires_recorder_before_restart(monkeypatch):
    from saitenka.app import session_stats

    def recorder(path):
        return SessionRecorder(path, clock=lambda: 0.0, wall_clock=lambda: 0.0, writer=FakeWriter())

    monkeypatch.setattr(session_stats, "SessionRecorder", recorder)
    owner = HistoryOwner(enabled=True)
    owner.start(path=lambda: "/a/01.mkv", arm=lambda _seconds: None)
    old_recorder = owner.recorder
    result = owner.finish(None)
    assert owner.recorder is None
    owner.start(path=lambda: "/a/02.mkv", arm=lambda _seconds: None)

    assert old_recorder.writer.closed
    assert owner.recorder.snapshot.media_path == "/a/02.mkv"
    assert result == (
        "0.0 min watched · 0 cues · 0 lookups · 0 captures · 0 cards · JP 0.0 min / EN 0.0 min"
    )


def test_stats_command_lists_complete_and_incomplete_sessions(tmp_path, monkeypatch, capsys):
    from saitenka.app import session_stats
    from saitenka.app.commands.diagnostics import stats

    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    store.save(SessionSnapshot("open", "/a/01.mkv", "01.mkv", 1.0))
    store.save(SessionSnapshot("done", "/a/02.mkv", "02.mkv", 2.0, ended_at=3.0, completed=True))
    store.close()
    monkeypatch.setattr(session_stats, "_DB_PATH_OVERRIDE", path)

    assert stats(limit=5) == 0

    output = capsys.readouterr().out
    assert "02.mkv  [complete]" in output
    assert "01.mkv  [incomplete]" in output
