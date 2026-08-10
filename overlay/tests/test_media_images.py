"""Inline SVG-image gaiji (#283): import-time rasterization + the render-side sprite / ▢ fallback.

Split by dependency: the render-side tests inject a media map directly (no resvglite needed, so they
run on the default gate env); the import/preload tests `importorskip("resvglite")` because a default
install never populates the media table — the renderer just falls back to ▢.
"""

from __future__ import annotations

from io import BytesIO

import dicthelp
import pytest
from overlay.model import Style
from overlay.render.flow import ImgBox
from overlay.sc.walk import collect_img_paths, walk
from PIL import Image

# A monochrome gaiji: one black square on transparent — the common 外字 shape. Stored as a PNG so the
# render-side tests don't need resvglite (the walker consumes decoded image bytes, not SVG).
_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    b'<rect x="10" y="10" width="80" height="80" fill="black"/></svg>'
)


def _png(fill: tuple[int, int, int, int] = (0, 0, 0, 255)) -> bytes:
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    img.paste(fill, (8, 8, 24, 24))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_collect_img_paths_walks_nested_content():
    node = {
        "tag": "div",
        "content": [{"tag": "img", "path": "a.svg"}, {"tag": "img", "path": "b.svg"}],
    }
    assert collect_img_paths(node) == ["a.svg", "b.svg"]


def test_img_with_media_becomes_sprite_box():
    node = {"tag": "img", "path": "x.png"}
    blocks = walk(node, Style(size=24), media={"x.png": _png()})
    boxes = [x for b in blocks for x in b.flow if isinstance(x, ImgBox)]
    assert len(boxes) == 1
    assert boxes[0].sprite is not None  # real glyph, not the placeholder


def test_monochrome_img_is_tinted_to_text_colour():
    node = {"tag": "img", "path": "x.png", "appearance": "monochrome"}  # opt-in recolour
    blocks = walk(node, Style(size=24, color=(200, 30, 30, 255)), media={"x.png": _png()})
    sprite = next(x.sprite for b in blocks for x in b.flow if isinstance(x, ImgBox))
    assert sprite is not None
    assert sprite.getpixel((16, 16)) == (200, 30, 30, 255)  # opaque ink recoloured to text colour


def test_default_appearance_keeps_colours_not_tinted():
    # Yomitan's img `appearance` defaults to "auto": an img with NO appearance field must keep its own
    # colours — tinting it (the inverted-default bug) would flatten a coloured diagram into a solid block.
    node = {"tag": "img", "path": "x.png"}  # no appearance → default auto
    blocks = walk(
        node, Style(size=24, color=(200, 30, 30, 255)), media={"x.png": _png((10, 160, 40, 255))}
    )
    sprite = next(x.sprite for b in blocks for x in b.flow if isinstance(x, ImgBox))
    assert sprite is not None
    assert sprite.getpixel((16, 16)) == (
        10,
        160,
        40,
        255,
    )  # green preserved, NOT overwritten with red


def test_appearance_auto_keeps_the_svg_colours():
    node = {"tag": "img", "path": "x.png", "appearance": "auto"}
    blocks = walk(
        node, Style(size=24, color=(200, 30, 30, 255)), media={"x.png": _png((10, 160, 40, 255))}
    )
    sprite = next(x.sprite for b in blocks for x in b.flow if isinstance(x, ImgBox))
    assert sprite is not None
    assert sprite.getpixel((16, 16)) == (10, 160, 40, 255)  # original green kept, NOT tinted red


def test_missing_media_falls_back_to_box():
    node = {"tag": "img", "path": "gone.svg"}
    blocks = walk(node, Style(size=24), media={})  # default install → empty map
    box = next(x for b in blocks for x in b.flow if isinstance(x, ImgBox))
    assert box.sprite is None and box.label == "▢"


# --- import + preload (needs the optional rasterizer) ------------------------------------------------


def test_import_rasterizes_svg_media_into_the_db(tmp_path):
    pytest.importorskip("resvglite")
    zp = dicthelp.term_zip(
        tmp_path / "d.zip",
        "MediaDict",
        [("語", "ご", [{"type": "image", "path": "m/x.svg"}])],
        media={"m/x.svg": _SVG},
    )
    d = dicthelp.load_dict(zp)
    got = d.db.media_for(d.dict_id, ["m/x.svg", "absent.svg"])
    assert set(got) == {"m/x.svg"}  # only the present path resolves
    assert Image.open(BytesIO(got["m/x.svg"])).format == "PNG"  # SVG was rasterized to PNG


def test_definition_carries_preloaded_media(tmp_path):
    pytest.importorskip("resvglite")
    zp = dicthelp.term_zip(
        tmp_path / "d.zip",
        "MediaDict",
        [("語", "ご", [{"type": "image", "path": "m/x.svg"}])],
        media={"m/x.svg": _SVG},
    )
    dset = dicthelp.load_set(dict_zips=[zp])
    defs, _headword, _reading = dset._dict_defs(("語",), {"語"}, "ご")
    media_maps = [dfn.media for dfn in defs if dfn.media]
    assert (
        media_maps and "m/x.svg" in media_maps[0]
    )  # the img path was preloaded onto the Definition


def test_one_bad_svg_does_not_abort_the_import(tmp_path):
    pytest.importorskip("resvglite")
    zp = dicthelp.term_zip(
        tmp_path / "d.zip",
        "MediaDict",
        [("語", "ご", [{"type": "image", "path": "m/ok.svg"}])],
        media={"m/ok.svg": _SVG, "m/bad.svg": b"not an svg at all"},
    )
    d = dicthelp.load_dict(zp)  # must not raise despite the malformed SVG
    got = d.db.media_for(d.dict_id, ["m/ok.svg", "m/bad.svg"])
    assert set(got) == {"m/ok.svg"}  # good glyph stored; bad one skipped → ▢ fallback
