"""Prefetch lookahead needs reader._sub_index regardless of subtitle source: external/jimaku files
already had a path, embedded (baked-in) tracks need extracting first. A FakeIPC serves track-list/
path like test_subselect.py's; a FakeReader records what load_sub_index was called with instead of
building a real Reader (this module only touches .ipc, ._get and .load_sub_index)."""

from __future__ import annotations

from overlay.app import embedded_subs as es


class FakeIPC:
    def __init__(self, tracks=None, path=None):
        self._tracks = tracks or []
        self._path = path

    def command(self, *args):
        if args[:2] == ("get_property", "track-list"):
            return {"data": self._tracks}
        if args[:2] == ("get_property", "path"):
            return {"data": self._path}
        return {"data": None}


class FakeReader:
    def __init__(self, ipc):
        self.ipc = ipc
        self.loaded_paths: list = []

    def _get(self, prop):
        return self.ipc.command("get_property", prop).get("data")

    def load_sub_index(self, path):
        self.loaded_paths.append(path)


EXTERNAL = {
    "id": 1,
    "type": "sub",
    "selected": True,
    "main-selection": 0,
    "external": True,
    "external-filename": "/tmp/fetched.ja.srt",
}
EMBEDDED_JA = {
    "id": 9,
    "type": "sub",
    "selected": True,
    "main-selection": 0,
    "external": False,
    "ff-index": 10,
}
EMBEDDED_EN_SECONDARY = {
    "id": 1,
    "type": "sub",
    "selected": True,
    "main-selection": 1,  # secondary selection — must NOT win over main-selection 0
    "external": False,
    "ff-index": 2,
}


def test_no_selected_track_leaves_index_unset():
    reader = FakeReader(FakeIPC(tracks=[]))
    es.build_sub_index_for_current_track(reader)
    assert reader.loaded_paths == []


def test_external_track_loads_straight_from_track_list_path():
    reader = FakeReader(FakeIPC(tracks=[EXTERNAL]))
    es.build_sub_index_for_current_track(reader)
    assert reader.loaded_paths == [es.Path("/tmp/fetched.ja.srt")]


def test_embedded_track_extracts_via_ffmpeg_then_loads_the_cache_path(tmp_path, monkeypatch):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: tmp_path / "cache")

    calls = []

    def fake_extract(video_arg, ff_index, dest):
        calls.append((video_arg, ff_index, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        return True

    monkeypatch.setattr(es, "extract_embedded_track", fake_extract)

    reader = FakeReader(FakeIPC(tracks=[EMBEDDED_JA], path=str(video)))
    es.build_sub_index_for_current_track(reader)

    assert len(calls) == 1
    assert calls[0][0] == video
    assert calls[0][1] == 10
    assert reader.loaded_paths == [calls[0][2]]


def test_embedded_track_reuses_cached_extraction_without_re_extracting(tmp_path, monkeypatch):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: cache_dir)
    dest = cache_dir / es.embedded_subs_cache_key(video, 10)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")

    def boom(*_a, **_kw):
        raise AssertionError("should not re-extract when the cache already has this track")

    monkeypatch.setattr(es, "extract_embedded_track", boom)

    reader = FakeReader(FakeIPC(tracks=[EMBEDDED_JA], path=str(video)))
    es.build_sub_index_for_current_track(reader)
    assert reader.loaded_paths == [dest]


def test_embedded_extraction_failure_leaves_index_unset(tmp_path, monkeypatch):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(es, "extract_embedded_track", lambda *_a, **_kw: False)

    reader = FakeReader(FakeIPC(tracks=[EMBEDDED_JA], path=str(video)))
    es.build_sub_index_for_current_track(reader)
    assert reader.loaded_paths == []


def test_primary_selection_wins_over_secondary():
    reader = FakeReader(FakeIPC(tracks=[EMBEDDED_EN_SECONDARY, EMBEDDED_JA], path="/v/ep.mkv"))
    track = es._selected_sub_track(reader.ipc)
    assert track is EMBEDDED_JA


# --- the ffmpeg extraction argv itself (the tests above monkeypatch extract_embedded_track away) ---


def test_extract_embedded_track_builds_ffmpeg_argv(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "overlay.app.embedded_subs.subprocess.run", lambda cmd, **_kw: calls.__setitem__("cmd", cmd)
    )
    # pin the binary so the assertion doesn't depend on the host's ffmpeg path (find_tool resolves it)
    monkeypatch.setattr("overlay.mpvio.discover.find_tool", lambda name: name)
    dest = tmp_path / "out" / "track.srt"
    assert es.extract_embedded_track("/v/ep.mkv", 10, dest) is True
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg" and "/v/ep.mkv" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:10"  # mpv ff-index maps straight onto ffmpeg -map 0:<n>
    assert cmd[cmd.index("-c:s") + 1] == "srt"  # transcode any codec to .srt
    assert cmd[-1] == str(dest)
    assert dest.parent.is_dir()  # destination dir created before the run


def test_extract_embedded_track_failsoft_on_ffmpeg_error(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise es.subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr("overlay.app.embedded_subs.subprocess.run", boom)
    monkeypatch.setattr("overlay.mpvio.discover.find_tool", lambda name: name)
    assert es.extract_embedded_track("/v/ep.mkv", 3, tmp_path / "o.srt") is False


def test_cache_key_changes_with_track_and_is_stable_for_same_file(tmp_path):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"hello")
    k1 = es.embedded_subs_cache_key(video, 10)
    k2 = es.embedded_subs_cache_key(video, 10)
    k3 = es.embedded_subs_cache_key(video, 2)
    assert k1 == k2
    assert k1 != k3
    assert k1.endswith(".srt")
