"""Inline SVG-image gaiji (#283): import-time rasterization + the render-side sprite / ▢ fallback.

Split by dependency: the render-side tests inject a media map directly (no rasterizer needed, so they
run on the default gate env); the import/preload tests `importorskip("resvg_py")` because a default
install never populates the media table — the renderer just falls back to ▢.
"""

from __future__ import annotations

import gzip
import logging
from io import BytesIO

import dicthelp
import pytest
from PIL import Image

from saitenka.model import Style
from saitenka.render.flow import ImgBox
from saitenka.render.sc_adapter import collect_img_paths, walk

# A monochrome gaiji: one black square on transparent — the common 外字 shape. Stored as a PNG so the
# render-side tests don't need the rasterizer (the walker consumes decoded image bytes, not SVG).
_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    b'<rect x="10" y="10" width="80" height="80" fill="black"/></svg>'
)

# A <text> gaiji — the 大辞林 漢/呉 badge shape: a bordered box with a font-drawn glyph. Without a font
# resvg draws ONLY the box (the #283 tofu bug); _load_media must hand it the bundled NotoSansJP.
_TEXT_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>"
    "<rect width='128' height='128' fill='none' stroke='black' stroke-width='8'/>"
    "<text text-anchor='middle' x='50%' y='50%' dy='.35em' font-family='sans-serif' "
    "font-size='100' fill='black'>漢</text></svg>"
).encode()


def _opaque(png: bytes) -> int:
    hist = Image.open(BytesIO(png)).convert("RGBA").getchannel("A").histogram()
    return sum(hist) - hist[0]  # every pixel minus the fully-transparent ones


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


def _ink(sprite: Image.Image):
    """The gaiji's centre pixel — solid ink in every fixture here. Read by proportion, not by a fixed
    coordinate, because the sprite is fitted to the box it is drawn in, not left at its decoded size."""
    return sprite.getpixel((sprite.width // 2, sprite.height // 2))


def test_sprite_is_fitted_to_the_box_it_reserves():
    # The decode is stored at a fixed base height unrelated to the drawn size (64px for a real gaiji),
    # and the box reserves `style.size`. Compositing the decode raw drew it ~2.4x oversize, over the
    # neighbouring text — visible on any display taking the 1x path (1080p, no `tip_scale`).
    node = {"tag": "img", "path": "x.png"}
    box = next(
        x
        for b in walk(node, Style(size=24), media={"x.png": _png()})
        for x in b.flow
        if isinstance(x, ImgBox)
    )
    assert box.sprite is not None
    assert box.sprite.size == (box.width, box.height)


def test_scaled_sprite_fills_the_projected_box_from_the_full_resolution_decode():
    # A gaiji is stored well above its drawn size, so the display-scale sprite is resampled from the
    # decode rather than from the fitted 1x thumbnail — and still lands exactly on the projected box.
    node = {"tag": "img", "path": "x.png"}
    box = next(
        x
        for b in walk(node, Style(size=24), media={"x.png": _png()})
        for x in b.flow
        if isinstance(x, ImgBox)
    )
    assert box.native is not None
    assert box.native(1.5).size == (round(box.width * 1.5), round(box.height * 1.5))


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
    assert _ink(sprite) == (200, 30, 30, 255)  # opaque ink recoloured to text colour


def test_default_appearance_keeps_colours_not_tinted():
    # Yomitan's img `appearance` defaults to "auto": an img with NO appearance field must keep its own
    # colours — tinting it (the inverted-default bug) would flatten a coloured diagram into a solid block.
    node = {"tag": "img", "path": "x.png"}  # no appearance → default auto
    blocks = walk(
        node, Style(size=24, color=(200, 30, 30, 255)), media={"x.png": _png((10, 160, 40, 255))}
    )
    sprite = next(x.sprite for b in blocks for x in b.flow if isinstance(x, ImgBox))
    assert sprite is not None
    assert _ink(sprite) == (10, 160, 40, 255)  # green preserved, NOT overwritten with red


def test_appearance_auto_keeps_the_svg_colours():
    node = {"tag": "img", "path": "x.png", "appearance": "auto"}
    blocks = walk(
        node, Style(size=24, color=(200, 30, 30, 255)), media={"x.png": _png((10, 160, 40, 255))}
    )
    sprite = next(x.sprite for b in blocks for x in b.flow if isinstance(x, ImgBox))
    assert sprite is not None
    assert _ink(sprite) == (10, 160, 40, 255)  # original green kept, NOT tinted red


def test_missing_media_falls_back_to_box():
    node = {"tag": "img", "path": "gone.svg"}
    blocks = walk(node, Style(size=24), media={})  # default install → empty map
    box = next(x for b in blocks for x in b.flow if isinstance(x, ImgBox))
    assert box.sprite is None and box.label == "▢"


# --- import + preload (needs the optional rasterizer) ------------------------------------------------


def test_import_rasterizes_svg_media_into_the_db(tmp_path):
    pytest.importorskip("resvg_py")
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
    pytest.importorskip("resvg_py")
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
    pytest.importorskip("resvg_py")
    zp = dicthelp.term_zip(
        tmp_path / "d.zip",
        "MediaDict",
        [("語", "ご", [{"type": "image", "path": "m/ok.svg"}])],
        media={"m/ok.svg": _SVG, "m/bad.svg": b"not an svg at all"},
    )
    d = dicthelp.load_dict(zp)  # must not raise despite the malformed SVG
    got = d.db.media_for(d.dict_id, ["m/ok.svg", "m/bad.svg"])
    assert set(got) == {"m/ok.svg"}  # good glyph stored; bad one skipped → ▢ fallback


def test_text_gaiji_rasterizes_with_its_glyph(tmp_path):
    # #283 regression: a <text> badge (漢) must store MORE ink than the same SVG rendered font-less —
    # i.e. the glyph is drawn, not an empty box. Metamorphic oracle: with-font ink > box-only ink.
    resvg_py = pytest.importorskip("resvg_py")
    zp = dicthelp.term_zip(
        tmp_path / "d.zip",
        "MediaDict",
        [("漢", "かん", [{"type": "image", "path": "m/kan.svg"}])],
        media={"m/kan.svg": _TEXT_SVG},
    )
    d = dicthelp.load_dict(zp)
    stored = d.db.media_for(d.dict_id, ["m/kan.svg"])["m/kan.svg"]
    # No fonts and no system fallback → border only (the old tofu), same knobs _rasterize_svg uses.
    box_only = resvg_py.svg_to_bytes(
        svg_string=_TEXT_SVG.decode(), height=64, skip_system_fonts=True
    )
    assert _opaque(stored) > _opaque(box_only)  # the 漢 glyph adds ink beyond the bare box


def test_gzipped_svg_rasterizes_like_its_plain_form(tmp_path):
    # Yomitan dictionaries carry gzipped payloads under plain `.svg` names. usvg unwrapped those itself,
    # so decoding the bytes as text instead would turn a renderable gaiji into a silent ▢. Metamorphic:
    # compressing the input must not change a pixel.
    pytest.importorskip("resvg_py")
    zp = dicthelp.term_zip(
        tmp_path / "d.zip",
        "MediaDict",
        [("語", "ご", [{"type": "image", "path": "m/plain.svg"}])],
        media={"m/plain.svg": _SVG, "m/gz.svg": gzip.compress(_SVG)},
    )
    d = dicthelp.load_dict(zp)
    got = d.db.media_for(d.dict_id, ["m/plain.svg", "m/gz.svg"])
    assert set(got) == {"m/plain.svg", "m/gz.svg"}  # the gzipped one is not skipped
    assert _opaque(got["m/gz.svg"]) == _opaque(got["m/plain.svg"])


def test_malformed_svg_is_logged_loudly(tmp_path, caplog):
    # "loud errors on failed renders": a skipped SVG must warn (per-file + a per-dict summary), not
    # vanish at debug — the silent ▢ is exactly what hid #283.
    pytest.importorskip("resvg_py")
    zp = dicthelp.term_zip(
        tmp_path / "d.zip",
        "MediaDict",
        [("語", "ご", [{"type": "image", "path": "m/ok.svg"}])],
        media={"m/ok.svg": _SVG, "m/bad.svg": b"not an svg at all"},
    )
    with caplog.at_level(logging.WARNING, logger="saitenka.app.dictdb"):
        dicthelp.load_dict(zp)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("resvg-py failed on m/bad.svg" in m for m in warnings)  # the per-file warning
    assert any("failed to rasterize" in m for m in warnings)  # the per-dict summary
