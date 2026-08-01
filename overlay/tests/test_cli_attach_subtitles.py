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

    def fetch_japanese_subs_async(self, fetch):
        self.fetch = fetch


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
        jimaku_key=None,
        jimaku_title=None,
        episode=None,
        resync=False,
        tsukihime_config={"result_cap": 5},
    )

    assert reader.fetch is not None
    assert ipc.commands == [("get_property", "path")]
    assert reader.fetch() == (Path("episode.ja.ass"), "tsukihime: added")
    assert calls == [("/videos/Show - 01.mkv", ("jimaku", "tsukihime"), {"result_cap": 5})]
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)
