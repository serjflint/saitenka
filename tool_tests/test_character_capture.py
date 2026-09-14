from __future__ import annotations

import numpy as np
import pysubs2
import pytest
from compare_cached_characters import Captures, coordinate_result, request_for
from PIL import Image

from saitenka.app.subtitle_fonts import FontEnvironment


def test_unresponsive_player_aborts_before_font_configuration(tmp_path):
    class Player:
        def command(self, *_args, **_kwargs):
            return {"error": "timeout"}

    with pytest.raises(RuntimeError, match="mpv get_property: timeout"):
        Captures(tmp_path, Player(), (1280, 720))


def test_captures_reuse_one_paused_frame_and_reject_a_stale_screenshot(tmp_path):
    class Player:
        def __init__(self):
            self.commands = []
            self.write_screenshot = True

        def expand_path(self, path):
            return path

        def query(self, name):
            return False if name == "sid" else None

        def command(self, *args, **_kwargs):
            self.commands.append(args)
            if args[0] == "screenshot-to-file" and self.write_screenshot:
                Image.new("RGB", (8, 8)).save(args[1])
            return {"error": "success", "data": False}

    ipc = Player()
    capture = Captures(tmp_path, ipc, (8, 8))
    try:
        frames = [capture.frame("", sample) for sample in (1000, 2000)]
        assert np.array_equal(*frames)
        assert [command for command in ipc.commands if command[0] == "seek"] == [
            ("seek", 2, "absolute+exact")
        ]
        assert not any(command[0] == "sub-remove" for command in ipc.commands)
        ipc.write_screenshot = False
        with pytest.raises(FileNotFoundError):
            capture.frame("", 3000)
    finally:
        capture.overlay.close()


def test_whitespace_does_not_require_ink_or_remove_neighboring_token_identity():
    document = pysubs2.SSAFile()
    document.info.update(PlayResX="640", PlayResY="360")
    document.styles["Default"].fontname = "sans-serif"
    document.styles["Default"].fontsize = 40
    document.events.append(pysubs2.SSAEvent(start=1000, end=2000, text="猫　犬"))

    request = request_for(document.to_string("ass"), 1500, (640, 360), FontEnvironment())

    assert [token.surface for line in request.lines for token in line] == ["猫", "　", "犬"]
    assert {box.index for box in request.boxes} == {0, 2}


@pytest.mark.parametrize("suffix", [".ass", ".srt"])
@pytest.mark.parametrize("lose_mark", [False, True])
@pytest.mark.parametrize("ignore_controls", [False, True])
@pytest.mark.parametrize("alter_hint", [False, True])
def test_comparison_paints_over_native_source_and_calibrates_from_native_ink(
    suffix, *, lose_mark, ignore_controls, alter_hint
):
    document = pysubs2.SSAFile()
    document.events.append(pysubs2.SSAEvent(start=1000, end=2000, text="猫"))
    source = document.to_string(suffix[1:])

    class Capture:
        size = (640, 360)
        fonts = FontEnvironment()

        def __init__(self):
            self.painted = []
            self.references = []

        def frame(
            self,
            text,
            _sample,
            request=None,
            *,
            suffix=".ass",
            reference_coverage=None,
            reference_color=(0, 255, 0),
        ):
            image = np.zeros((360, 640, 3), dtype=np.uint8)
            color = (255, 255, 0)
            if request is not None:
                self.painted.append((text, suffix))
                color = (0, 255, 0)
            elif reference_coverage is not None:
                self.references.append((text, suffix, reference_coverage))
                color = (0, 255, 0)
            elif r"\1c&HFF0000&" in text:
                color = (0, 0, 255)
            elif r"\1c&H0000FF&" in text:
                color = (255, 0, 0)
            image[10:20, 10:20] = color
            image[5:7, 10:12] = color
            if alter_hint and color == (0, 0, 255):
                image[10, 10, 2] = 127
            if lose_mark and (request is not None or reference_coverage is not None):
                image[5:7, 10:12] = (255, 255, 0)
            if reference_coverage is not None and not lose_mark and not ignore_controls:
                image[:] = 0
                image[reference_coverage > 0] = reference_color
            return image

    capture = Capture()
    result, images = coordinate_result(
        capture,
        source,
        {"event": 0, "start": 0, "end": 1, "sample_ms": 1500, "reason": ""},
        suffix=suffix,
    )

    assert result["verdict"] == ("inconclusive" if lose_mark or ignore_controls else "passed")
    assert result["reason"] == (
        "calibration-does-not-preserve-native-ink"
        if lose_mark
        else "capture-controls-not-discriminating"
        if ignore_controls
        else ""
    )
    assert capture.painted == [(source, suffix)]
    assert capture.references[0][:2] == (source, suffix)
    assert np.array_equal(capture.references[0][2], images[0][:, :, 0])
    if not lose_mark and not ignore_controls:
        assert result["identity_probe_preserves_ink"] is not alter_hint
        assert result["controls"] == {
            "positive": "passed",
            "displaced": "failed",
            "wrong-color": "failed",
        }
