"""Device 2: the reading-state color as a raster, for a face mpv's OSD library cannot load.

The oracle is not "does an image come out" but "is the color on the same pixels the measurement
said the token covered". The last test asks that of a real libass: render a cue, tint what came
back, and check the ink lands where the glyphs did.
"""

from __future__ import annotations

import numpy as np
import pytest
from util import requires_libass

from saitenka.subtitles import overpaint


def solid(width: int, height: int, value: int = 255) -> bytes:
    return bytes([value]) * (width * height)


def test_a_tinted_mask_carries_its_color_at_its_own_alpha() -> None:
    """Coverage is anti-aliased, so it has to survive as alpha rather than being thresholded — a
    binary mask prints a jagged edge over glyphs mpv drew smooth."""
    mask = overpaint.TokenMask(0, 0, 2, 1, bytes([255, 64]), 0x3366FF)

    result = overpaint.compose([mask])

    assert result is not None
    assert result.rgba[0, 0].tolist() == [0x33, 0x66, 0xFF, 255]
    assert result.rgba[0, 1].tolist() == [0x33, 0x66, 0xFF, 64]


def test_the_image_is_cropped_to_the_tokens_not_to_the_frame() -> None:
    """A cue is a strip near one edge. Sizing the upload to the screen would push most of a megabyte
    of transparent pixels per cue through mpv's overlay path."""
    result = overpaint.compose(
        [
            overpaint.TokenMask(100, 600, 10, 20, solid(10, 20), 0xFF0000),
            overpaint.TokenMask(130, 600, 10, 20, solid(10, 20), 0x00FF00),
        ]
    )

    assert result is not None
    assert (result.x, result.y) == (100, 600)
    assert result.rgba.shape == (20, 40, 4)


def test_each_token_keeps_its_own_color() -> None:
    result = overpaint.compose(
        [
            overpaint.TokenMask(0, 0, 4, 1, solid(4, 1), 0xFF0000),
            overpaint.TokenMask(4, 0, 4, 1, solid(4, 1), 0x0000FF),
        ]
    )

    assert result is not None
    assert result.rgba[0, 0, 0] == 0xFF
    assert result.rgba[0, 4, 2] == 0xFF


def test_a_shared_edge_pixel_does_not_print_a_seam() -> None:
    """Neighbouring words can both cover one anti-aliased pixel. Adding there saturates it and draws
    a bright line down the gap between every pair of words."""
    result = overpaint.compose(
        [
            overpaint.TokenMask(0, 0, 2, 1, bytes([255, 120]), 0x808080),
            overpaint.TokenMask(1, 0, 2, 1, bytes([120, 255]), 0x808080),
        ]
    )

    assert result is not None
    assert result.rgba[0, 1, 3] == 120  # not 240


@pytest.mark.parametrize(
    "mask",
    [
        overpaint.TokenMask(0, 0, 0, 10, b"", 0xFFFFFF),  # no extent
        overpaint.TokenMask(0, 0, 4, 4, solid(2, 2), 0xFFFFFF),  # coverage does not fit the extent
        overpaint.TokenMask(0, 0, 4, 4, solid(4, 4), -1),  # not a color
    ],
)
def test_a_mask_that_does_not_describe_itself_is_dropped(mask: overpaint.TokenMask) -> None:
    """These numbers arrive from a render. A mismatched one must be refused, not reshaped: reshaping
    would paint a token's color across the wrong pixels, which reads as a rendering bug."""
    assert mask.usable is False
    assert overpaint.compose([mask]) is None


def test_nothing_to_paint_is_a_real_answer() -> None:
    """The caller reads `None` as "take the previous raster down", so it must not be an error."""
    assert overpaint.compose([]) is None


def test_an_implausible_extent_is_refused_rather_than_allocated() -> None:
    result = overpaint.compose(
        [
            overpaint.TokenMask(0, 0, 1, 1, solid(1, 1), 0xFFFFFF),
            overpaint.TokenMask(10**6, 10**6, 1, 1, solid(1, 1), 0xFFFFFF),
        ]
    )

    assert result is None


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_the_color_lands_on_the_pixels_the_measurement_measured() -> None:
    """The oracle that matters, end to end through a real libass: render a cue as the hit map does,
    keep the coverage, tint it, and check the color covers the glyphs and only the glyphs.

    This is what device 2 claims and device 1 cannot: no face is consulted anywhere in the chain, so
    a family only the subtitle renderer holds is colored exactly as well as any other.
    """
    requires_libass()
    from saitenka.subtitles import (
        GeometryPaletteEntry,
        GeometryRequest,
        SubtitleEventId,
        SubtitleFrameId,
        SubtitleTrackId,
    )
    from saitenka.subtitles.libass_backend import LibassGeometryBackend

    track = SubtitleTrackId("overpaint")
    event = SubtitleEventId(str(track), 1000, 3000, 0, 0)
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: D,sans-serif,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
        "1,0,0,7,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
        # Two tokens, each in its own reserved color — the same trick the hit map uses to read a
        # token back out of a render that knows nothing about tokens.
        r"Dialogue: 0,0:00:01.00,0:00:03.00,D,,0,0,0,,{\pos(40,40)\1c&H0000FF&}猫"
        r"{\1c&H00FF00&}犬" + "\n"
    )
    backend = LibassGeometryBackend()
    request = GeometryRequest(
        1,
        track,
        SubtitleFrameId(track, (event,)),
        2000,
        (640, 360),
        (640, 360),
        header.encode(),
        palette=(
            GeometryPaletteEntry(event, 0, 0xFF0000),
            GeometryPaletteEntry(event, 1, 0x00FF00),
        ),
        keep_coverage=True,
    )
    try:
        snapshot = backend.render(request)
    finally:
        backend.close()

    assert len(snapshot.tokens) == 2
    assert all(token.coverage for token in snapshot.tokens), "the masks were not kept"

    painted = overpaint.compose(
        [
            overpaint.TokenMask(
                token.bounds.x,
                token.bounds.y,
                token.bounds.width,
                token.bounds.height,
                token.coverage,
                color,
            )
            for token, color in zip(snapshot.tokens, (0xFF00FF, 0x00FFFF), strict=True)
        ]
    )

    assert painted is not None
    first = snapshot.tokens[0]
    # Every pixel the first token covered carries its color, and nothing outside its rect does.
    inside = painted.rgba[
        first.bounds.y - painted.y : first.bounds.y - painted.y + first.bounds.height,
        first.bounds.x - painted.x : first.bounds.x - painted.x + first.bounds.width,
    ]
    lit = inside[..., 3] > 0
    assert lit.any(), "the first token's mask is empty"
    assert np.all(inside[lit][:, 0] == 0xFF) and np.all(inside[lit][:, 2] == 0xFF)
    # And the alpha is the render's own, not a threshold: an anti-aliased glyph edge is partly
    # covered, so among the pixels the mask lit at all, some must be neither absent nor opaque.
    # Read over the whole rect this would be trivially true — a bounding box is mostly background.
    assert ((inside[lit][:, 3] > 0) & (inside[lit][:, 3] < 255)).any()
