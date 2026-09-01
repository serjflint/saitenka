r"""The one check on the leg the hit boxes actually depend on: mpv's own subtitle renderer.

Everything else in this repo measures the *OSD* renderer — `compute_bounds` answers through it, and
that is the leg the color is drawn on. The boxes are supposed to match the other leg: mpv's
`sd_ass`, its own `ASS_Library`, its own attachments. Nothing measured it. A box quietly twenty
pixels wide of its word looks exactly like a correct one until someone clicks.

This measures it, by the only means that cannot be argued with — pixels. mpv renders the cue and
encodes the frame; the ink is compared against the boxes our measuring renderer produced for the
same document at the same frame size. No display is needed: mpv's encode mode burns the subtitle in.

The last test is the negative control, and it is the reason to trust the others: it measures at a
frame size mpv did not use, and the differential has to catch it. An oracle that passes whatever it
is given proves nothing about what it passes.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest
from saitenka_subtitles import (
    GeometryPaletteEntry,
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
)
from saitenka_subtitles.ass_geometry import prepare_ass_hit_map_frame

from saitenka.mpvio.discover import find_mpv
from saitenka.mpvio.launch import NATIVE_GEOMETRY_MPV_MIN

pytestmark = [
    pytest.mark.live,
    # PARITY below IS the native-geometry profile, so this file's floor is that profile's floor.
    pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN),
    pytest.mark.skipif(
        not os.environ.get("SAITENKA_LIVE"),
        reason="live real-mpv test — set SAITENKA_LIVE=1; run `uv run poe smoke-live`",
    ),
]

WIDTH, HEIGHT = 640, 360
TEXT = "猫を見る犬"
TOKENS = (
    TokenAnnotation(0, 0, 1),
    TokenAnnotation(1, 1, 2),
    TokenAnnotation(2, 2, 4),
    TokenAnnotation(3, 4, 5),
)

#: Outline and shadow are zero on purpose. mpv draws them and our measurement does not, so leaving
#: them on would compare a glyph against a glyph plus its border and report the border as error.
HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, \
Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, \
Alignment, MarginL, MarginR, MarginV, Encoding
Style: D,sans-serif,40,&H000000FF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,\
{{alignment}},20,20,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

#: The profile `saitenka run` configures. Measuring against an mpv outside it would prove agreement
#: with a configuration the mode refuses.
PARITY = (
    "--sub-ass-override=no",
    "--sub-ass-scale-with-window=no",
    "--sub-scale=1",
    "--sub-pos=100",
    "--sub-use-margins=yes",
    "--sub-ass-force-margins=no",
    "--sub-ass-video-aspect-override=0",
    "--sub-ass-use-video-data=all",
    "--sub-ass-style-overrides=",
    "--blend-subtitles=no",
    "--sub-filter-sdh=no",
)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory(prefix="saitenka-pixel-diff-") as raw:
        yield Path(raw)


def clip_at(directory: Path) -> Path:
    path = directory / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={WIDTH}x{HEIGHT}:d=8:r=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def document(alignment: int, event_text: str) -> str:
    header = HEADER.format(alignment=alignment)
    return f"{header}Dialogue: 0,0:00:00.50,0:00:08.00,D,,0,0,0,,{event_text}\n"


def mpv_ink(directory: Path, ass: Path) -> tuple[int, int, int, int]:
    """Where mpv's own subtitle renderer put the glyphs, as the ink's bounding box.

    Encode mode rather than `screenshot-raw`: it burns the subtitle into the frame with no window,
    so this asks the same renderer without needing a display to ask it on.
    """
    shot = directory / "frame.png"
    # `check=True` alone reports an exit code and swallows the reason — a refused option or a missing
    # codec reads as a bare CalledProcessError, undiagnosable from a CI log. Raise with mpv's stderr,
    # the same way `test_live_ass_document._properties` does.
    result = subprocess.run(
        [
            find_mpv() or "mpv",
            "--no-config",
            f"--sub-file={ass}",
            "--start=2",
            "--frames=1",
            *PARITY,
            f"--o={shot}",
            "--of=image2",
            "--ovc=png",
            str(clip_at(directory)),
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"mpv failed to render (returncode={result.returncode}): "
            f"{result.stderr.decode(errors='replace')}"
        )
    return ink_bounds(np.array(_open(shot)).astype(int))


def _open(path: Path):
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGB").copy()


def ink_bounds(frame: np.ndarray) -> tuple[int, int, int, int]:
    lit = np.argwhere(frame.sum(axis=2) > 40)
    assert len(lit), "mpv drew no subtitle at all"
    return (
        int(lit[:, 1].min()),
        int(lit[:, 0].min()),
        int(lit[:, 1].max()) + 1,
        int(lit[:, 0].max()) + 1,
    )


def our_boxes(source: bytes, event_row: str, frame: tuple[int, int]) -> list[tuple[int, ...]]:
    """The hit boxes Saitenka would hand the interaction layer for this cue."""
    from saitenka_subtitles.libass_backend import LibassGeometryBackend

    track = SubtitleTrackId("pixel-differential")
    prepared = prepare_ass_hit_map_frame(
        source, track, active_rows=event_row, text=TEXT, tokens=TOKENS
    )
    scale = frame[1] / prepared.play_res_y
    palette = tuple(
        GeometryPaletteEntry(
            entry.event_id, entry.token_index, entry.rgb, entry.font_name, entry.font_size * scale
        )
        for entry in prepared.palette
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(
            GeometryRequest(
                1,
                track,
                prepared.frame_id,
                2_000,
                frame,
                frame,
                prepared.ass,
                palette=palette,
                reserved_rgb=prepared.reserved_rgb,
            )
        )
    finally:
        backend.close()
    return [
        (t.bounds.x, t.bounds.y, t.bounds.x + t.bounds.width, t.bounds.y + t.bounds.height)
        for t in snapshot.tokens
    ]


def union_of(boxes: list[tuple[int, ...]]) -> tuple[int, int, int, int]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def worst_edge(ours: tuple[int, ...], theirs: tuple[int, ...]) -> int:
    return max(abs(a - b) for a, b in zip(ours, theirs, strict=True))


#: One pixel per edge. Not a fudge: the ink bound is read off an anti-aliased raster at a fixed
#: threshold, so the outermost row of a glyph can fall either side of it. A substituted face or a
#: wrong font scale is tens of pixels out, which is the difference this is separating.
TOLERANCE = 1


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    ("name", "alignment", "event_text"),
    [
        # Positioned: isolates the layout itself — advances, face, size — from alignment.
        ("positioned", 7, r"{\pos(40,40)}" + TEXT),
        # Naturally placed: the margins, the alignment and the script-to-frame scaling are all in
        # the comparison. This is the shape a real cue has.
        ("aligned", 2, TEXT),
    ],
)
def test_our_boxes_land_where_mpv_drew_the_words(
    workspace: Path, name: str, alignment: int, event_text: str
) -> None:
    source = document(alignment, event_text)
    ass = workspace / f"{name}.ass"
    ass.write_text(source, encoding="utf-8")
    event_row = source.strip().splitlines()[-1]

    theirs = mpv_ink(workspace, ass)
    ours = union_of(our_boxes(source.encode(), event_row, (WIDTH, HEIGHT)))

    assert worst_edge(ours, theirs) <= TOLERANCE, f"{name}: ours {ours} vs mpv {theirs}"


@pytest.mark.timeout(120)
def test_every_token_has_a_box_over_its_own_glyphs(workspace: Path) -> None:
    """The union agreeing is necessary and not sufficient: four boxes could share the right outline
    and still be sliced in the wrong places, which is a click landing on the neighbouring word."""
    source = document(2, TEXT)
    ass = workspace / "tokens.ass"
    ass.write_text(source, encoding="utf-8")
    event_row = source.strip().splitlines()[-1]
    mpv_ink(workspace, ass)  # renders the frame this reads back
    frame = np.array(_open(workspace / "frame.png")).astype(int)
    lit = frame.sum(axis=2) > 40

    boxes = our_boxes(source.encode(), event_row, (WIDTH, HEIGHT))

    assert len(boxes) == len(TOKENS)
    covered = np.zeros_like(lit)
    for left, top, right, bottom in boxes:
        window = lit[top:bottom, left:right]
        assert window.any(), f"the box {(left, top, right, bottom)} covers no glyph mpv drew"
        covered[top:bottom, left:right] = True
    stray = int((lit & ~covered).sum())
    assert stray <= lit.sum() * 0.02, f"{stray} of mpv's ink pixels fall outside every hit box"


@pytest.mark.timeout(120)
def test_the_differential_catches_a_layout_that_does_not_match(workspace: Path) -> None:
    """The negative control. Measuring at a frame mpv did not render at is exactly the class of
    failure this exists to catch — a wrong font scale, uniformly applied, with every other meter in
    the repo reading green. If this passes, the two tests above prove nothing."""
    source = document(2, TEXT)
    ass = workspace / "control.ass"
    ass.write_text(source, encoding="utf-8")
    event_row = source.strip().splitlines()[-1]

    theirs = mpv_ink(workspace, ass)
    wrong = union_of(our_boxes(source.encode(), event_row, (WIDTH, HEIGHT * 2)))

    assert worst_edge(wrong, theirs) > TOLERANCE
