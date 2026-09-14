"""Observed cue to uploaded bytes, with a real native backend and pinned font."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import test_native_subtitles as native
from saitenka_subtitles.geometry import FontProvider, FontSetup, GeometryPaletteEntry
from saitenka_subtitles.libass_backend import LibassGeometryBackend

from saitenka.app import subtitle_fonts
from saitenka.app.overlay_ids import OverlayId


@pytest.mark.timeout(5)
@pytest.mark.parametrize("size", [(1280, 720), (3440, 1440)])
def test_observed_fractional_cue_upload_matches_original_native_alpha(
    monkeypatch, tmp_path, size, request
):
    source = native.ASS.decode().replace("Arial", "Noto Sans JP Thin")
    source = source.replace("1,2,1,2,10,10,30,1", "1,0,0,2,10,10,30,1")
    source = source.replace("猫を見る", r"{\an7\pos(101.25,101.75)}猫犬木")
    monkeypatch.setattr(native, "ASS", source.encode())

    class RecordingBackend(LibassGeometryBackend):
        def __init__(self):
            super().__init__()
            self.requests = []

        def render(self, request):
            self.requests.append(request)
            return super().render(request)

    monkeypatch.setattr(native, "FakeBackend", RecordingBackend)
    session, ipc, backend = native.reader(
        tmp_path,
        scorer=native.Coloring(
            native.Scorer(known=native.KnownWords.from_set(["猫", "犬", "木", "猫犬木"]))
        ),
    )
    request.addfinalizer(session.close)
    font = Path(__file__).parents[1] / "src/saitenka/assets/fonts/NotoSansJP.ttf"
    ipc.props["sub-text/ass-full"] = source.splitlines()[-1]
    ipc.props["osd-dimensions"] = {"w": size[0], "h": size[1]}
    session.graph.presentation.refresh_osd()
    geometry = session.graph.subtitle_presentation.native
    assert geometry is not None
    geometry.set_fonts(
        subtitle_fonts.FontEnvironment(
            FontSetup(font_provider=FontProvider.NONE),
            (("corpus.ttf", font.read_bytes()),),
            subtitle_fonts.option_snapshot(
                {name: ipc.query(f"options/{name}") for name in subtitle_fonts.FONT_OPTIONS}
            ),
            frozenset({"noto sans jp thin"}),
        )
    )
    try:
        session.graph.playback.observe("sub-text", "猫犬木")
        session.graph.cue.settle()
        native.settle_jobs(session, ipc)

        uploads = [
            command
            for command in ipc.commands
            if command[:2] == ("overlay-add", OverlayId.OVERPAINT)
        ]
        assert uploads
        upload = uploads[-1]
        width, height = int(upload[7]), int(upload[8])
        pixels = np.frombuffer(Path(upload[4]).read_bytes(), dtype=np.uint8).reshape(
            height, width, 4
        )
        actual = np.zeros((size[1], size[0]), dtype=np.uint8)
        x, y = int(upload[2]), int(upload[3])
        actual[y : y + height, x : x + width] = pixels[:, :, 3]
        inputs = backend.requests[-1]
        oracle = LibassGeometryBackend()
        try:
            reference = oracle.render(
                replace(
                    inputs,
                    ass=inputs.native_ass,
                    native_ass=b"",
                    reserved_rgb=(),
                    palette=(GeometryPaletteEntry(inputs.palette[0].event_id, 0, 0xFFFFFF),),
                )
            )
        finally:
            oracle.close()
        expected = np.zeros_like(actual)
        for token in reference.tokens:
            box = token.bounds
            expected[box.y : box.y + box.height, box.x : box.x + box.width] = np.frombuffer(
                token.coverage, dtype=np.uint8
            ).reshape(box.height, box.width)
        assert np.count_nonzero(expected) > 0
        assert np.array_equal(actual, expected)
        assert not np.array_equal(np.roll(actual, 1, axis=1), expected)
    finally:
        session.close()
