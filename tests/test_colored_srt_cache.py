"""SRT copies stay identifiable across attach sessions and only claim their painted events."""

import json
import threading
from pathlib import Path

import pytest
from saitenka_subtitles import parse_cues
from saitenka_subtitles.subrip import dialogue_row
from test_colored_srt import srt
from test_colored_track_document import _per_character

from saitenka.app import colored_subs


def generated(tmp_path, body="猫<i>を見る</i>"):
    source = tmp_path / "source.srt"
    source.write_text(srt(body) + "\n" + srt('<font color="#FF0000">犬</font>'), encoding="utf-8")
    result = colored_subs.generate(
        colored_subs.ColorRequest(source, f"external:{source}", str(source), _per_character),
        threading.Event(),
    )
    assert isinstance(result, colored_subs.ColorResult)
    assert result.path is not None
    return source, result.path


def test_srt_copy_survives_fresh_origin_discovery(tmp_path):
    source, path = generated(tmp_path)
    tracks = [
        {"id": 2, "type": "sub", "external": True, "external-filename": str(source)},
        {"id": 9, "type": "sub", "external": True, "external-filename": str(path)},
    ]

    assert path.suffix == ".srt"
    assert colored_subs.copy_origin(tracks[1]) == f"external:{source}"
    assert colored_subs.hide_origins(tracks) == tracks[1:]


@pytest.mark.parametrize("mixed", [False, True])
def test_only_fully_colored_srt_frames_suppress_the_overlay(tmp_path, mixed):
    _source, path = generated(tmp_path)
    data = path.read_text(encoding="utf-8")
    bodies = data.split("\n\n")
    rows = [
        dialogue_row(parse_cues(block, "x.srt")[0], block.split("\n", 2)[2]) for block in bodies
    ]
    selected = {"id": 9, "type": "sub", "external": True, "external-filename": str(path)}
    observed = {
        "sid": 9,
        "track-list": [selected],
        "sub-text/ass-full": "\n".join(rows if mixed else rows[:1]),
    }

    assert colored_subs.track_paints(observed.get) is (not mixed)


@pytest.mark.parametrize(
    "metadata",
    [
        "broken",
        "[]",
        "{}",
        '{"version":1,"origin":12,"painted_rows":[]}',
        '{"version":1,"origin":"external:x","painted_rows":null}',
    ],
)
def test_invalid_srt_metadata_never_claims_a_copy(metadata):
    path = colored_subs.write_copy("x", srt("猫"), suffix=".srt")
    Path(f"{path}.json").write_text(metadata, encoding="utf-8")

    assert colored_subs.copy_origin({"external": True, "external-filename": str(path)}) is None


def test_evicting_old_srt_copy_removes_its_metadata(tmp_path):
    source, previous = generated(tmp_path)
    replacement = colored_subs.write_copy(str(source), srt("犬"), suffix=".srt")
    Path(f"{replacement}.json").write_text(json.dumps({"version": 1}), encoding="utf-8")

    colored_subs.evict_siblings(replacement)

    assert not previous.exists()
    assert not Path(f"{previous}.json").exists()
    assert replacement.exists()
    assert Path(f"{replacement}.json").exists()


def test_srt_copy_is_not_published_when_metadata_cannot_be_written(tmp_path, monkeypatch):
    source = tmp_path / "source.srt"
    source.write_text(srt("猫"), encoding="utf-8")

    def unavailable(*_args):
        raise OSError("read-only cache")

    monkeypatch.setattr("saitenka.app.paths.atomic_write_text", unavailable)
    result = colored_subs.generate(
        colored_subs.ColorRequest(source, f"external:{source}", str(source), _per_character),
        threading.Event(),
    )

    assert result == colored_subs.ColorResult(None, "write-failed", 1)


def test_srt_metadata_participates_in_the_copy_identity():
    first = colored_subs.write_copy("x", srt("猫"), suffix=".srt", metadata="first")
    second = colored_subs.write_copy("x", srt("猫"), suffix=".srt", metadata="second")

    assert first != second
    assert first.name.partition("-")[0] == second.name.partition("-")[0]
