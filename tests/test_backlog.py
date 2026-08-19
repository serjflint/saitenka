from pathlib import Path
from types import SimpleNamespace

import util
from util import keybind_registry

from saitenka.app.backlog import BacklogStore, Capture, normalize_match_name
from saitenka.app.controller import Reader


def _capture(path: Path, *, start: float = 1.0, jp: str = "猫です") -> Capture:
    return Capture(
        video_path=str(path),
        cue_start=start,
        cue_end=start + 2.0,
        jp_text=jp,
        en_text="It is a cat.",
        subtitle_track={"jp_sid": 1, "en_sid": 2},
        hovered_surface="猫",
        hovered_lemma="猫",
    )


def test_backlog_persists_across_restarts_and_consecutive_anime(tmp_path):
    db = tmp_path / "backlog.sqlite"
    first = tmp_path / "Anime A - 01.mkv"
    second = tmp_path / "Anime B - 02.mkv"
    with BacklogStore(db, clock=lambda: 10.0) as store:
        first_entry = store.toggle_capture(_capture(first))
        second_entry = store.toggle_capture(_capture(second, start=4.0, jp="犬です"))

    with BacklogStore(db, clock=lambda: 20.0) as reopened:
        assert reopened.entry(first_entry.id).jp_text == "猫です"
        assert reopened.entry(second_entry.id).jp_text == "犬です"
        assert len(reopened.all_media()) == 2


def test_normalized_full_basename_uses_nfc_and_casefold():
    assert normalize_match_name("/one/CAFÉ.MKV") == normalize_match_name(
        "/two/cafe\N{COMBINING ACUTE ACCENT}.mkv"
    )


def test_directory_move_matches_by_name_and_preserves_original_identity(tmp_path):
    original = tmp_path / "old" / "Show - 01.mkv"
    moved = tmp_path / "new" / "Show - 01.mkv"
    original.parent.mkdir()
    original.write_bytes(b"video")
    with BacklogStore(tmp_path / "backlog.sqlite", clock=lambda: 10.0) as store:
        entry = store.toggle_capture(_capture(original))
        result = store.match(moved)

        assert result.confirmed
        assert result.source == "basename"
        assert result.media is not None
        assert result.media.original_path == str(original)
        assert result.media.original_basename == original.name
        assert result.media.last_known_path == str(moved)
        assert store.entries_for_path(moved) == [entry]


def test_file_metadata_is_retained_when_media_later_goes_missing(tmp_path):
    video = tmp_path / "Show - 03.mkv"
    video.write_bytes(b"12345")
    with BacklogStore(tmp_path / "backlog.sqlite") as store:
        media = store.ensure_media(video)
        video.unlink()

        saved = store.media(media.id)
        assert saved.file_size == 5
        assert saved.mtime is not None
        assert saved.original_stem == "Show - 03"
        assert saved.original_path == str(video)


def test_missing_media_keeps_text_export_available(tmp_path):
    video = tmp_path / "Show - 04.mkv"
    video.write_bytes(b"video")
    with BacklogStore(tmp_path / "backlog.sqlite") as store:
        entry = store.toggle_capture(_capture(video))
        video.unlink()

        assert store.text_export(entry.id) == "猫です\nIt is a cat."


def test_renamed_episode_is_candidate_until_explicit_relink(tmp_path):
    original = tmp_path / "[Group] Show - 01 [1080p].mkv"
    renamed = tmp_path / "Show S01E01 remux.mkv"
    with BacklogStore(tmp_path / "backlog.sqlite", clock=lambda: 10.0) as store:
        entry = store.toggle_capture(_capture(original))

        candidate = store.match(renamed)
        assert candidate.kind == "candidate"
        assert candidate.choices[0].id == entry.media_id
        assert store.entries_for_path(renamed) == []

        linked = store.relink(entry.media_id, renamed)
        assert linked.original_path == str(original)
        assert linked.original_basename == original.name
        assert linked.last_known_path == str(renamed)
        assert store.entries_for_path(renamed) == [entry]


def test_same_name_aliases_require_explicit_selection(tmp_path):
    one = tmp_path / "One - 01.mkv"
    two = tmp_path / "Two - 02.mkv"
    common = tmp_path / "elsewhere" / "Episode.mkv"
    with BacklogStore(tmp_path / "backlog.sqlite") as store:
        first = store.ensure_media(one)
        second = store.ensure_media(two)
        store.relink(first.id, tmp_path / "a" / common.name)
        store.relink(second.id, tmp_path / "b" / common.name)

        result = store.match(common)
        assert result.kind == "ambiguous"
        assert {media.id for media in result.choices} == {first.id, second.id}
        assert store.entries_for_path(common) == []


def test_duplicate_capture_toggles_and_status_changes_retain_history(tmp_path):
    video = tmp_path / "Show - 01.mkv"
    ticks = iter((1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
    with BacklogStore(tmp_path / "backlog.sqlite", clock=lambda: next(ticks)) as store:
        saved = store.toggle_capture(_capture(video))
        archived = store.toggle_capture(_capture(video))
        reopened = store.toggle_capture(_capture(video))
        reviewed = store.set_status(saved.id, "reviewed")
        mined = store.set_status(saved.id, "mined")

        assert saved.id == archived.id == reopened.id == reviewed.id == mined.id
        assert [saved.status, archived.status, reopened.status, reviewed.status, mined.status] == [
            "open",
            "archived",
            "open",
            "reviewed",
            "mined",
        ]
        assert store.status_history(saved.id) == [
            "open",
            "archived",
            "open",
            "reviewed",
            "mined",
        ]


def test_global_summary_does_not_expose_cue_content(tmp_path):
    with BacklogStore(tmp_path / "backlog.sqlite") as store:
        store.toggle_capture(_capture(tmp_path / "Show - 01.mkv"))
        summary = store.summary()

    assert summary == [
        {"id": 1, "original_basename": "Show - 01.mkv", "status": "open", "count": 1}
    ]
    assert "jp_text" not in summary[0]


class _IPC(util.FakeIPC):
    def __init__(self, props):
        super().__init__()
        self.props.update(props)


def test_bookmark_hotkey_captures_metadata_without_playback_or_mining(tmp_path, monkeypatch):
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
    reader = Reader(ipc)
    reader.sub_text = "日本語"
    reader.jp_sid = 3
    reader.en_sid = 4
    reader.tokens = [SimpleNamespace(surface="日本", lemma="日本")]
    reader.hover = 0
    store = BacklogStore(tmp_path / "reader.sqlite", clock=lambda: 10.0)
    reader._backlog_store = store
    captures = []
    reader._session_recorder = SimpleNamespace(record_capture=lambda: captures.append(True))
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)

    reader.toggle_bookmark()

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
    reader.toggle_bookmark()
    assert captures == [True]
    forbidden = {"seek", "sub-seek", "set_property", "screenshot-to-file"}
    assert not any(command[0] in forbidden for command in ipc.commands)
    reader._session_recorder = None
    reader.close()


def test_english_mode_capture_keeps_japanese_and_english_fields_distinct(tmp_path, monkeypatch):
    video = tmp_path / "Show - 02.mkv"
    ipc = _IPC(
        {
            "path": str(video),
            "sub-start": 1.0,
            "sub-end": 2.0,
            "secondary-sub-text": "日本語",
            "track-list": [],
        }
    )
    reader = Reader(ipc)
    reader.subtitle_language = "en"
    reader.sub_text = "English line"
    store = BacklogStore(tmp_path / "reader.sqlite")
    reader._backlog_store = store
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)

    reader.toggle_bookmark()

    entry = store.entries_for_path(video)[0]
    assert (entry.jp_text, entry.en_text) == ("日本語", "English line")
    reader.close()


def test_bookmark_without_active_cue_does_not_open_store(monkeypatch):
    ipc = _IPC({"path": "/video.mkv", "sub-start": None, "sub-end": None})
    reader = Reader(ipc)
    shown = []
    monkeypatch.setattr(reader, "_toast", lambda *args: shown.append(args))

    reader.toggle_bookmark()

    assert reader._backlog_store is None
    assert shown == [("no active cue to bookmark", "warn")]


def test_bookmark_key_is_configurable():
    from saitenka.app.config import KeyOptions, ReaderOptions

    ipc = _IPC({})
    reader = Reader(ipc, options=ReaderOptions(keys=KeyOptions(bookmark_key="Alt+q")))
    reader._register_keybinds()
    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}
    assert binds["Alt+q"] == "script-message saitenka-toggle-bookmark"
