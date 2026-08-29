from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from saitenka.app.session_stats import (
    AsyncSessionWriter,
    SessionRecorder,
    SessionSnapshot,
    SessionStore,
    analysis_snapshot,
    start,
    summary,
)


class FakeWriter:
    def __init__(self):
        self.snapshots = []
        self.closed = False

    def submit(self, snapshot):
        self.snapshots.append(snapshot)

    def close(self, _timeout=2.0):
        self.closed = True


def test_recorder_aggregates_events_and_language_time():
    monotonic = iter((0.0, 2.0, 7.0, 10.0))
    wall = iter((100.0, 110.0))
    writer = FakeWriter()
    recorder = SessionRecorder(
        "/anime/Show 01.mkv",
        clock=lambda: next(monotonic),
        wall_clock=lambda: next(wall),
        writer=writer,
    )

    recorder.accrue(paused=False, language="jp")
    recorder.accrue(paused=False, language="en")
    recorder.record_cue(("jp", 1.0, 2.0, "字幕"))
    recorder.record_cue(("jp", 1.0, 2.0, "字幕"))
    recorder.record_lookup()
    recorder.record_capture()
    recorder.record_mined(2)
    result = recorder.finish()

    assert result == replace(
        result,
        media_name="Show 01.mkv",
        ended_at=110.0,
        completed=True,
        watch_seconds=8.0,
        jp_seconds=5.0,
        en_seconds=3.0,
        cue_count=1,
        lookup_count=1,
        capture_count=1,
        mined_count=2,
    )
    assert writer.closed
    assert summary(result) == (
        "0.1 min watched · 1 cues · 1 lookups · 1 captures · 2 cards · JP 0.1 min / EN 0.1 min"
    )


def test_async_writer_persists_distinct_and_incomplete_sessions(tmp_path):
    path = tmp_path / "sessions.sqlite"
    first = SessionSnapshot("first", "/a/01.mkv", "01.mkv", 1.0)
    second = SessionSnapshot("second", "/a/02.mkv", "02.mkv", 2.0)
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


def test_recorder_records_episode_identity():
    writer = FakeWriter()
    recorder = SessionRecorder(
        "/anime/[Grp] Nippon Sangoku - 07 [1080p].mkv",
        clock=lambda: 0.0,
        wall_clock=lambda: 0.0,
        writer=writer,
    )

    assert (recorder.snapshot.title, recorder.snapshot.episode) == ("Nippon Sangoku", 7)
    assert recorder.snapshot.title_match == "nippon sangoku"


def test_recorder_revision_updates_dedup_identity_without_counting_another_cue():
    recorder = SessionRecorder(
        "/anime/Show 01.mkv",
        clock=lambda: 0.0,
        wall_clock=lambda: 0.0,
        writer=FakeWriter(),
    )
    recorder.record_cue(("jp", 1.0, 2.0, "字幕"))

    recorder.revise_cue(("jp", 3.0, 4.0, "字幕"))

    assert recorder.snapshot.cue_count == 1


def test_episode_identity_round_trips_through_store(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    store.save(
        SessionSnapshot(
            "s1", "/a/Show 12.mkv", "Show 12.mkv", 1.0, title="Show", title_match="show", episode=12
        )
    )
    store.close()

    reopened = SessionStore(path)
    (row,) = reopened.recent()
    reopened.close()

    assert (row.title, row.title_match, row.episode) == ("Show", "show", 12)


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


def test_analysis_snapshot_reuses_shared_result_without_cues():
    result = SimpleNamespace(
        content_token_count=10,
        known_token_coverage=0.8,
        known_type_coverage=0.6,
        unique_kanji=frozenset("日本"),
        unique_lemmas=frozenset(("日本", "行く")),
        unknown_lemmas=frozenset(("行く",)),
    )

    assert analysis_snapshot(result) == (
        '{"content_tokens":10,"known_token_coverage":0.8,"known_type_coverage":0.6,'
        '"unique_kanji":2,"unique_lemmas":2,"unknown_lemmas":1}'
    )


def test_disabled_stats_does_not_touch_ipc():
    recorder = start(
        current=None,
        enabled=False,
        path=lambda: (_ for _ in ()).throw(AssertionError("IPC touched")),
        arm=lambda _seconds: None,
    )

    assert recorder is None


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
