"""Prefetch lookahead needs the episode's cue index regardless of subtitle source: external/jimaku
files already had a path, embedded (baked-in) tracks need extracting first. A FakeIPC serves
track-list/path like test_subselect.py's, and the loader is a list's `append` — the function takes
the four facts, so there is no host to stand in for."""

from __future__ import annotations

import util

from saitenka.app import embedded_subs as es


class FakeIPC(util.FakeIPC):
    def __init__(self, tracks=None, path=None):
        super().__init__()
        self.props["track-list"] = tracks or []
        self.props["path"] = path
        util.runtime_gateway(self)


def build(ipc) -> list:
    """Run the index build against `ipc`, returning the paths it asked the loader for."""
    loaded: list = []

    def get(prop):
        return ipc.command("get_property", prop).get("data")

    es.build_sub_index_for_current_track(ipc, get, loaded.append, None)
    return loaded


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
    "codec": "subrip",
}
EMBEDDED_JA_ASS = {**EMBEDDED_JA, "codec": "ass"}
EMBEDDED_EN_SECONDARY = {
    "id": 1,
    "type": "sub",
    "selected": True,
    "main-selection": 1,  # secondary selection — must NOT win over main-selection 0
    "external": False,
    "ff-index": 2,
}


def test_no_selected_track_leaves_index_unset():
    loaded = build(FakeIPC(tracks=[]))
    assert loaded == []


def test_external_track_loads_straight_from_track_list_path():
    loaded = build(FakeIPC(tracks=[EXTERNAL]))
    assert loaded == [es.Path("/tmp/fetched.ja.srt")]


def test_embedded_track_extracts_via_ffmpeg_then_loads_the_cache_path(tmp_path, monkeypatch):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: tmp_path / "cache")

    calls = []

    def fake_extract(video_arg, ff_index, dest, codec_args):
        calls.append((video_arg, ff_index, dest, codec_args))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        return True

    monkeypatch.setattr(es, "extract_embedded_track", fake_extract)

    loaded = build(FakeIPC(tracks=[EMBEDDED_JA], path=str(video)))

    assert len(calls) == 1
    assert calls[0][0] == video
    assert calls[0][1] == 10
    assert loaded == [calls[0][2]]


def test_an_embedded_ass_track_is_extracted_as_ass_not_transcoded(tmp_path, monkeypatch):
    """The extracted file is native geometry's authored source, so a transcode to .srt would leave
    the whole episode noninteractive — hover boxes and scanning gone, mpv still drawing."""
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: tmp_path / "cache")

    calls = []

    def fake_extract(video_arg, ff_index, dest, codec_args):
        calls.append((video_arg, ff_index, dest, codec_args))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("[Events]\n")
        return True

    monkeypatch.setattr(es, "extract_embedded_track", fake_extract)

    loaded = build(FakeIPC(tracks=[EMBEDDED_JA_ASS], path=str(video)))

    assert calls[0][3] == ("-c:s", "copy")
    assert loaded[0].suffix == ".ass"


def test_an_embedded_subrip_track_still_extracts_as_srt(tmp_path, monkeypatch):
    """The negative control: only ASS/SSA change format, and the cache key follows the format — a
    key that stayed `.srt` would serve an SRT-named ASS body, which `set_source` rejects."""
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: tmp_path / "cache")

    calls = []

    def fake_extract(video_arg, ff_index, dest, codec_args):
        calls.append((video_arg, ff_index, dest, codec_args))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        return True

    monkeypatch.setattr(es, "extract_embedded_track", fake_extract)

    loaded = build(FakeIPC(tracks=[EMBEDDED_JA], path=str(video)))

    assert calls[0][3] == ("-c:s", "copy")
    assert loaded[0].suffix == ".srt"


def test_embedded_track_reuses_cached_extraction_without_re_extracting(tmp_path, monkeypatch):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: cache_dir)
    dest = cache_dir / es.embedded_subs_cache_key(video, 10, ".srt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")

    def boom(*_a, **_kw):
        raise AssertionError("should not re-extract when the cache already has this track")

    monkeypatch.setattr(es, "extract_embedded_track", boom)

    loaded = build(FakeIPC(tracks=[EMBEDDED_JA], path=str(video)))
    assert loaded == [dest]


def test_embedded_extraction_failure_leaves_index_unset(tmp_path, monkeypatch):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(es, "extract_embedded_track", lambda *_a, **_kw: False)

    loaded = build(FakeIPC(tracks=[EMBEDDED_JA], path=str(video)))
    assert loaded == []


def test_primary_selection_wins_over_secondary():
    ipc = FakeIPC(tracks=[EMBEDDED_EN_SECONDARY, EMBEDDED_JA], path="/v/ep.mkv")
    assert es._selected_sub_track(ipc) is EMBEDDED_JA


# --- the ffmpeg extraction argv itself (the tests above monkeypatch extract_embedded_track away) ---


def test_extract_embedded_track_builds_ffmpeg_argv(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.subprocess.run",
        lambda cmd, **_kw: calls.__setitem__("cmd", cmd),
    )
    # pin the binary so the assertion doesn't depend on the host's ffmpeg path (find_tool resolves it)
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    dest = tmp_path / "out" / "track.srt"
    assert es.extract_embedded_track("/v/ep.mkv", 10, dest, ("-c:s", "srt")) is True
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg" and "/v/ep.mkv" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:10"  # mpv ff-index maps straight onto ffmpeg -map 0:<n>
    assert cmd[cmd.index("-c:s") + 1] == "srt"
    assert cmd[-1] == str(dest)
    assert dest.parent.is_dir()  # destination dir created before the run


def test_extract_embedded_track_failsoft_on_ffmpeg_error(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise es.subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr("saitenka.app.embedded_subs.subprocess.run", boom)
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    assert es.extract_embedded_track("/v/ep.mkv", 3, tmp_path / "o.srt", ("-c:s", "srt")) is False


def test_cache_key_changes_with_track_and_is_stable_for_same_file(tmp_path):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"hello")
    k1 = es.embedded_subs_cache_key(video, 10, ".srt")
    k2 = es.embedded_subs_cache_key(video, 10, ".srt")
    k3 = es.embedded_subs_cache_key(video, 2, ".srt")
    assert k1 == k2
    assert k1 != k3
    assert k1.endswith(".srt")
    # The format is part of the key: a re-extraction that changes format must not read back the
    # stale file under the old one.
    assert es.embedded_subs_cache_key(video, 10, ".ass") != k1
