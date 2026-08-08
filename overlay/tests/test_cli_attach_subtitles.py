from pathlib import Path

from overlay.app import cli, subselect
from overlay.app.subtitle_modes import SubtitleStartup, SubtitleTracks


class IPC:
    def __init__(self):
        self.commands: list[tuple] = []

    def command(self, *args):
        self.commands.append(args)
        if args[:2] == ("get_property", "path"):
            return {"data": "/videos/Show - 01.mkv"}
        return {"data": None}


class Reader:
    def __init__(self):
        self.fetch = None
        self.retry_factory = None
        self.picker_lister = None

    def fetch_japanese_subs_async(self, fetch):
        self.fetch = fetch

    def configure_subtitle_retry(self, factory):
        self.retry_factory = factory

    def configure_sub_picker(self, lister):
        self.picker_lister = lister


def test_attach_defers_ordered_provider_chain_without_touching_playback(monkeypatch):
    reader, ipc = Reader(), IPC()
    calls = []
    monkeypatch.setattr(cli, "build_sub_index_for_current_track", lambda _reader: None)

    def fetch(video, providers, **kwargs):
        calls.append((video, providers, kwargs["tsukihime_config"]))
        return Path("episode.ja.ass"), "tsukihime: added"

    monkeypatch.setattr(subselect, "fetch_provider_path", fetch)

    cli._finish_attach_subtitle_startup(
        reader,
        ipc,
        None,
        slang="ja,jpn,jp",
        fetch_in_background=("jimaku", "tsukihime"),
        enabled_providers=("jimaku", "tsukihime"),
        jimaku_key=None,
        jimaku_title=None,
        episode=None,
        resync=False,
        tsukihime_config={"result_cap": 5},
    )

    assert reader.fetch is not None
    assert reader.retry_factory is not None
    assert ipc.commands == [("get_property", "path")]
    assert reader.fetch() == (Path("episode.ja.ass"), "tsukihime: added")
    assert calls == [("/videos/Show - 01.mkv", ("jimaku", "tsukihime"), {"result_cap": 5})]
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)

    reader.retry_factory("/videos/Show - 02.mkv")()
    assert calls[-1][0] == "/videos/Show - 02.mkv"


def test_attach_configures_retry_even_when_startup_fetch_is_unneeded(monkeypatch):
    reader, ipc = Reader(), IPC()
    monkeypatch.setattr(cli, "build_sub_index_for_current_track", lambda _reader: None)

    cli._finish_attach_subtitle_startup(
        reader,
        ipc,
        None,
        slang="ja,jpn,jp",
        fetch_in_background=(),
        enabled_providers=("tsukihime",),
        jimaku_key=None,
        jimaku_title=None,
        episode=None,
        resync=False,
    )

    assert reader.fetch is None
    assert reader.retry_factory is not None
    assert ipc.commands == []


class _TrackIPC:
    """Models mpv's current path + a track-list carrying the previous episode's external sub."""

    def __init__(self):
        self.commands: list[tuple] = []
        self.props: dict = {
            "path": "/videos/Show - 03.mkv",
            "track-list": [
                {"type": "sub", "id": 1, "lang": "en", "selected": False},
                {
                    "type": "sub",
                    "id": 10,
                    "external": True,
                    "external-filename": "/c/Show - 02.ja.srt",
                    "selected": True,
                },
            ],
        }

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        if args[0] == "sub-remove":
            self.props["track-list"] = [
                t for t in self.props["track-list"] if t.get("id") != args[1]
            ]
        return {"data": None}


def test_attach_reslot_resets_episode_drops_carryover_and_continues_japanese(monkeypatch):
    """On an ATTACH episode advance: close+reopen stats, rebind a fresh EpisodeContext (no leak), drop
    the carried-over external, and — when the new file has no JP — defer a provider fetch so watching
    continues in Japanese. Reuses the run re-slot's contract (test_auto_advance) for attach."""
    from overlay.app import session_stats
    from overlay.app.controller import Reader as RealReader

    ipc = _TrackIPC()
    reader = RealReader(ipc)
    reader.jp_sid = 99  # stale prior-episode state the re-slot must clear
    episode_before = reader.episode

    started: list[str] = []
    monkeypatch.setattr(session_stats, "finish", lambda _r: None)
    monkeypatch.setattr(session_stats, "start", lambda r: started.append(str(r._prop("path"))))
    monkeypatch.setattr(cli, "build_sub_index_for_current_track", lambda _r: None)
    monkeypatch.setattr(reader, "start_prefetch", lambda: None)
    monkeypatch.setattr(reader, "_toast", lambda *_a, **_k: None)
    # new episode carries English only → prepare_attach_startup defers a jimaku fetch
    monkeypatch.setattr(
        subselect,
        "prepare_attach_startup",
        lambda _ipc, **_kw: (
            SubtitleStartup(SubtitleTracks(jp_sid=None, en_sid=1), "en"),
            "selected English fallback sid=1",
            ("jimaku",),
        ),
    )
    background: list = []
    monkeypatch.setattr(reader, "fetch_japanese_subs_async", lambda fetch: background.append(fetch))

    cli._attach_reslot(
        reader,
        ipc,
        Path("/videos/Show - 03.mkv"),
        slang="ja,jpn,jp",
        jimaku=True,
        jimaku_force=False,
        jimaku_key=None,
        tsukihime=False,
        resync=True,
        tsukihime_config={},
        enabled_providers=("jimaku",),
    )

    assert reader.episode is not episode_before  # fresh EpisodeContext…
    assert reader.jp_sid is None  # …so ep2's jp_sid=99 cannot leak into ep3
    assert ("sub-remove", 10) in ipc.commands  # carried-over ep2 external dropped
    assert started == ["/videos/Show - 03.mkv"]  # a new stats row on the current file
    assert reader._sub_picker_lister is not None  # Ctrl+J picker rewired for the new episode
    assert len(background) == 1  # JP absent → provider fetch deferred (continue in Japanese)
