"""libass lays the copy out exactly where it lays out the original.

Bounds, not masks: a `\\c` boundary starts a new bitmap run and resets raster rounding, so edge
coverage moves by a few levels while no glyph moves at all.
"""

from __future__ import annotations

import pytest
from saitenka_subtitles import TokenAnnotation
from saitenka_subtitles.colored_track import color_document
from util import PINNED_FAMILY, pinned_ass_renderer, requires_libass

HEAD = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\nWrapStyle: 0\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
    "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    f"Style: D,{PINNED_FAMILY},36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
    "1,2,1,2,20,20,20,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
    "MarginV, Effect, Text\n"
)
TEXTS = (
    "私は本を読む",
    r"{\b1}今日は{\b0}いい天気、\N散歩に行こう。",
    r"{\pos(320,60)\fs24}看板の文字",
    "長い台詞が画面の幅を越えて折り返されるかどうかを確かめるための、とても長い一行です。",
)


def _per_character(text: str):
    tokens = tuple(
        TokenAnnotation(index, index, index + 1)
        for index, char in enumerate(text)
        if not char.isspace()
    )
    return tokens, {token.token_index: 0x3060C0 + 7 * token.token_index for token in tokens}


def _bounds(libasslite, document: str) -> dict[int, tuple[int, int, int, int]]:
    renderer = pinned_ass_renderer(libasslite, document.encode())
    try:
        result = renderer.render(2_000, (640, 360), (640, 360), pixel_aspect=1.0)
    finally:
        renderer.close()
    bounds: dict[int, tuple[int, int, int, int]] = {}
    for layer in result.layers:
        if not layer.width or not layer.height:
            continue
        box = (layer.dst_x, layer.dst_y, layer.dst_x + layer.width, layer.dst_y + layer.height)
        seen = bounds.get(layer.image_type, box)
        bounds[layer.image_type] = (
            min(seen[0], box[0]),
            min(seen[1], box[1]),
            max(seen[2], box[2]),
            max(seen[3], box[3]),
        )
    return bounds


@pytest.mark.parametrize("text", TEXTS)
def test_the_copy_keeps_every_glyph_where_the_original_put_it(text: str) -> None:
    libasslite = requires_libass()
    source = HEAD + f"Dialogue: 0,0:00:01.00,0:00:03.00,D,,0,0,0,,{text}\n"
    copy = color_document(source, "external:/x.ass", _per_character)
    assert copy.colored == 1

    original = _bounds(libasslite, source)
    colored = _bounds(libasslite, copy.text)

    assert original, "the pinned face rendered nothing"
    assert colored == original
