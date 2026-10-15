from types import SimpleNamespace

import pytest
import util
from util import keybind_registry

from saitenka.app import bindings as app_bindings
from saitenka.app.backlog import BacklogStore
from saitenka.runtime.events import SubtitleLanguageChanged, SubtitleTracksDiscovered


class _IPC(util.FakeIPC):
    def __init__(self, props):
        super().__init__()
        self.props.update(props)


def test_bookmark_hotkey_captures_metadata_without_playback_or_mining(
    tmp_path, monkeypatch, make_session
):
    video = tmp_path / "Show - 01.mkv"
    ipc = _IPC(
        {
            "path": str(video),
            "sub-start": 4.0,
            "sub-end": 6.5,
            "secondary-sub-text": "English line",
            "track-list": [
                {"id": 3, "lang": "jpn", "title": "JP", "codec": "ass"},
                {
                    "id": 4,
                    "lang": "eng",
                    "title": "Signs",
                    "codec": "subrip",
                    "external-filename": "/subs/show.en.srt",
                },
            ],
        }
    )
    reader = make_session(ipc)
    reader.graph.playback.install_seed({"sub-text": "日本語"})
    reader.graph.track_commands.declare(SubtitleTracksDiscovered(3, 4))
    reader.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[SimpleNamespace(surface="日本", lemma="日本")]
    )
    reader.graph.tooltip.select(0)
    store = BacklogStore(tmp_path / "reader.sqlite", clock=lambda: 10.0)
    reader.graph.history.replace_backlog(store)
    captures = []
    reader.graph.history.replace_recorder(
        SimpleNamespace(record_capture=lambda: captures.append(True))
    )
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)

    reader.command(app_bindings.BOOKMARK_MSG)

    entry = store.entries_for_path(video)[0]
    assert (entry.cue_start, entry.cue_end, entry.jp_text, entry.en_text) == (
        4.0,
        6.5,
        "日本語",
        "English line",
    )
    assert entry.subtitle_track == {
        "en_sid": 4,
        "en_track": {
            "codec": "subrip",
            "external-filename": "/subs/show.en.srt",
            "id": 4,
            "lang": "eng",
            "title": "Signs",
        },
        "jp_sid": 3,
        "jp_track": {"codec": "ass", "id": 3, "lang": "jpn", "title": "JP"},
        "language": "jp",
        "primary_sid": 3,
        "secondary_sid": 4,
    }
    assert (entry.hovered_surface, entry.hovered_lemma) == ("日本", "日本")
    assert captures == [True]
    reader.command(app_bindings.BOOKMARK_MSG)
    assert captures == [True]
    forbidden = {"seek", "sub-seek", "set_property", "screenshot-to-file"}
    assert not any(command[0] in forbidden for command in ipc.commands)
    reader.graph.history.replace_recorder(None)
    reader.close()


@pytest.mark.parametrize(
    ("language", "primary", "secondary", "expected"),
    [
        ("jp", "日本語", "english", ("日本語", "english")),
        ("en", "English line", "日本語", ("日本語", "English line")),
        ("jp", "日本語", "", ("日本語", "")),
    ],
)
def test_a_bookmark_stores_the_cue_by_language_not_by_position(
    tmp_path, monkeypatch, make_session, language, primary, secondary, expected
):
    video = tmp_path / "Show - 02.mkv"
    ipc = _IPC(
        {
            "path": str(video),
            "sub-start": 1.0,
            "sub-end": 2.0,
            "secondary-sub-text": secondary,
            "track-list": [],
        }
    )
    reader = make_session(ipc)
    reader.graph.track_commands.declare(SubtitleLanguageChanged(language))
    reader.graph.playback.install_seed({"sub-text": primary})
    store = BacklogStore(tmp_path / "reader.sqlite")
    reader.graph.history.replace_backlog(store)
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)

    reader.command(app_bindings.BOOKMARK_MSG)

    entry = store.entries_for_path(video)[0]
    assert (entry.jp_text, entry.en_text) == expected
    reader.close()


def test_bookmark_without_active_cue_does_not_open_store(monkeypatch, make_session):
    ipc = _IPC({"path": "/video.mkv", "sub-start": None, "sub-end": None})
    reader = make_session(ipc)
    reader.graph.playback.install_seed({"sub-text": "日本語"})
    shown = []
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *args: shown.append(args))

    reader.command(app_bindings.BOOKMARK_MSG)

    assert reader.graph.history.backlog is None
    assert shown == [("no active cue to bookmark", "warn")]


def test_bookmark_key_is_configurable(make_session):
    from saitenka.app.config import KeyOptions, ReaderOptions

    ipc = _IPC({})
    reader = make_session(ipc, options=ReaderOptions(keys=KeyOptions(bookmark_key="Alt+q")))
    reader.graph.commands.install_input()
    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}
    assert binds["Alt+q"] == "script-message saitenka-toggle-bookmark"
