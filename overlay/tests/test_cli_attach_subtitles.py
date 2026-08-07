from pathlib import Path

from overlay.app import cli, subselect


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
