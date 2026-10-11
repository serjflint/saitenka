"""Authored subtitle extraction and indexing through the track-preparation worker."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest
from util import FakeIPC

from saitenka.app import embedded_subs as es
from saitenka.app.track_preparation import TrackRequest, prepare_track


def test_no_selected_track_leaves_index_unset():
    request = TrackRequest(FakeIPC(), {"track-list": [], "_geometry": False}, 0, threading.Event())
    assert prepare_track(request, threading.Event()) is None


@pytest.mark.parametrize(
    ("codec", "cached"),
    [
        ("external", False),
        *[(c, warm) for c in ("ass", "ssa", "subrip", "webvtt") for warm in (False, True)],
    ],
)
def test_preparation_preserves_the_authored_format_and_reuses_cached_extraction(
    tmp_path, monkeypatch, codec, cached
):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    cache = tmp_path / "cache"
    monkeypatch.setattr(es, "embedded_subs_cache_dir", lambda: cache)
    suffix = ".ass" if codec in {"ass", "ssa"} else ".srt"
    content = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,本を読む\n"
        if suffix == ".ass"
        else "1\n00:00:01,000 --> 00:00:02,000\n本を読む\n"
    )
    path = (
        tmp_path / "external.srt"
        if codec == "external"
        else cache / es.embedded_subs_cache_key(video, 10, suffix)
    )
    if codec == "external" or cached:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def extract(video_arg, ff_index, dest, codec_args, **_kwargs):
        assert codec != "external" and not cached
        assert (Path(video_arg), ff_index, dest) == (video, 10, path)
        assert codec_args == ("-c:s", "srt" if codec == "webvtt" else "copy")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return True

    monkeypatch.setattr(es, "extract_embedded_track", extract)
    primary = {"id": 9, "type": "sub", "selected": True, "main-selection": 0}
    primary.update(
        {"external": True, "external-filename": str(path)}
        if codec == "external"
        else {"ff-index": 10, "codec": codec}
    )
    secondary = {"id": 1, "type": "sub", "selected": True, "main-selection": 1}
    request = TrackRequest(
        FakeIPC(),
        {"path": str(video), "track-list": [secondary, primary], "_geometry": False},
        0,
        threading.Event(),
    )

    result = prepare_track(request, threading.Event())

    assert result is not None
    assert result.path == path
    assert result.sid == 9
    assert result.index.cues[0].text == "本を読む"
    assert result.source.raw == content.encode("utf-8")


def test_failed_extraction_does_not_publish_a_partial_cache(tmp_path, monkeypatch):
    dest = tmp_path / "cache" / "track.ass"

    def partial(command, **_kwargs):
        Path(command[-1]).write_bytes(b"partial document")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(es.subprocess, "run", partial)

    assert es.extract_embedded_track("video.mkv", 1, dest, ("-c:s", "copy")) is False
    assert list(dest.parent.iterdir()) == []


def test_extract_embedded_track_builds_ffmpeg_argv(tmp_path, monkeypatch):
    calls = {}

    def extract(cmd, **_kw):
        calls["cmd"] = cmd
        Path(cmd[-1]).write_text("partial subtitle", encoding="utf-8")
        assert not dest.exists()
        Path(cmd[-1]).write_text("complete subtitle", encoding="utf-8")

    monkeypatch.setattr(es.subprocess, "run", extract)
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    dest = tmp_path / "out" / "track.srt"
    assert es.extract_embedded_track("/v/ep.mkv", 10, dest, ("-c:s", "srt")) is True
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg" and "/v/ep.mkv" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:10"
    assert cmd[cmd.index("-c:s") + 1] == "srt"
    assert dest.read_text() == "complete subtitle"


def test_cache_key_changes_with_track_and_is_stable_for_same_file(tmp_path):
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"hello")
    k1 = es.embedded_subs_cache_key(video, 10, ".srt")
    k2 = es.embedded_subs_cache_key(video, 10, ".srt")
    k3 = es.embedded_subs_cache_key(video, 2, ".srt")
    assert k1 == k2
    assert k1 != k3
    assert k1.endswith(".srt")
    assert es.embedded_subs_cache_key(video, 10, ".ass") != k1
