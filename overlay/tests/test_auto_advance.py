"""#100 auto-advance: the eof-reached edge hook (controller) + the in-process re-slot (cli_run)."""

from __future__ import annotations

from overlay.app import cli_run, session_stats
from overlay.app.controller import Reader


class FakeIPC:
    """Minimal mpv IPC stand-in; `props` feeds get_property, records all commands."""

    def __init__(self):
        self.events = []
        self.props: dict = {}
        self.commands: list[tuple] = []

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}

    def pump(self):
        pass

    def drain_events(self):
        return []


def test_maybe_advance_fires_once_per_eof_edge():
    ipc = FakeIPC()
    reader = Reader(ipc)
    calls: list[int] = []
    reader.advance_hook = lambda: bool(calls.append(1))

    ipc.props["eof-reached"] = True
    reader._maybe_advance()
    reader._maybe_advance()  # still at EOF → must NOT re-fire
    assert calls == [1]

    ipc.props["eof-reached"] = False  # a fresh file cleared eof → re-arm
    reader._maybe_advance()
    ipc.props["eof-reached"] = True
    reader._maybe_advance()
    assert calls == [1, 1]


def test_maybe_advance_is_a_noop_without_a_hook():
    ipc = FakeIPC()
    reader = Reader(ipc)
    ipc.props["eof-reached"] = True

    reader._maybe_advance()  # attach/SyncPlay never installs a hook → nothing happens, no crash


def test_reslot_loads_next_file_and_rebinds_the_episode(tmp_path, monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader.jp_sid = 5  # dirty episode state that the re-slot must reset
    episode_before = reader.episode
    nxt = tmp_path / "Show 04.mkv"
    ipc.props["path"] = str(nxt)  # _await_file_loaded sees the new file immediately

    started: list[str] = []
    monkeypatch.setattr(session_stats, "finish", lambda _reader: None)
    monkeypatch.setattr(session_stats, "start", lambda r: started.append(str(r._prop("path"))))

    ok = cli_run.reslot_episode(
        reader,
        {},
        nxt,
        tmp_path,
        0,
        sub_file=None,
        jimaku=False,
        jimaku_key=None,
        slang="ja",
        resync=False,
    )

    assert ok is True
    assert ("loadfile", str(nxt)) in ipc.commands
    assert reader.episode is not episode_before  # a fresh EpisodeContext…
    assert reader.jp_sid is None  # …so prior-episode state cannot leak
    assert started == [str(nxt)]  # a new stats row started against the new file


def test_reslot_gives_up_when_the_file_never_loads(tmp_path, monkeypatch):
    ipc = FakeIPC()  # props['path'] stays unset → the file never "loads"
    reader = Reader(ipc)
    monkeypatch.setattr(session_stats, "finish", lambda _reader: None)
    monkeypatch.setattr(session_stats, "start", lambda _reader: None)
    monkeypatch.setattr(cli_run, "_await_file_loaded", lambda *_a, **_k: False)

    ok = cli_run.reslot_episode(
        reader,
        {},
        tmp_path / "Show 04.mkv",
        tmp_path,
        0,
        sub_file=None,
        jimaku=False,
        jimaku_key=None,
        slang="ja",
        resync=False,
    )

    assert ok is False
