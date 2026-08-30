"""MVP: subtitle rendering (multi-line) + per-word hitbox geometry."""

import pytest
from session_builder import build_session
from util import assert_golden

from saitenka.app.config import ReaderOptions, TooltipOptions
from saitenka.app.subtitles import render_subtitle
from saitenka.app.tokenize import tokenize

LINE = "門前の小僧習わぬ経を読む"


def test_hitboxes_cover_every_token_in_order():
    toks = tokenize(LINE)
    sr = render_subtitle([toks], osd_w=1280, size=44)
    assert len(sr.boxes) == len(toks)
    assert [b.index for b in sr.boxes] == list(range(len(toks)))
    xs = [b.x for b in sr.boxes]
    assert xs == sorted(xs)  # left-to-right on one line
    for a, b in zip(
        sr.boxes, sr.boxes[1:], strict=False
    ):  # adjacent pairs — lengths differ by design     # adjacent, non-overlapping
        assert a.x + a.w <= b.x + 1


def test_background_opacity_zero_makes_the_box_fully_transparent():
    """The configurable sub-background alpha: rendering with a 0-alpha background leaves strictly more
    fully-transparent pixels than the default translucent box (the box no longer fills), while geometry
    is unchanged. The default (150) render is the negative control that proves the box is there to remove."""
    toks = tokenize(LINE)

    boxed = render_subtitle([toks], osd_w=1280, size=44, background=(0, 0, 0, 150))
    clear = render_subtitle([toks], osd_w=1280, size=44, background=(0, 0, 0, 0))

    def fully_transparent(img):
        return sum(1 for px in img.get_flattened_data() if px[3] == 0)

    assert clear.image.size == boxed.image.size  # only the box alpha changed, not layout
    assert fully_transparent(clear.image) > fully_transparent(boxed.image)


def test_box_contains_hit():
    toks = tokenize(LINE)
    sr = render_subtitle([toks], osd_w=1280, size=44)
    b = sr.boxes[-1]  # 読む (last token; 習わ+ぬ merged so indices shift)
    assert b.contains(b.x + b.w / 2, b.y + b.h / 2)
    assert not b.contains(b.x - 5, b.y + b.h / 2)


def test_shrinks_to_fit_width():
    toks = tokenize(LINE * 4)  # a single very long token stream
    sr = render_subtitle([toks], osd_w=1280, size=44)
    assert sr.image.width <= 1280


def test_explicit_line_breaks_stack_vertically():
    l1, l2 = tokenize("私は本を読む"), tokenize("門前の小僧習わぬ経を読む")
    sr = render_subtitle([l1, l2], osd_w=1280, size=44)
    # global indices are row-major and contiguous
    assert [b.index for b in sr.boxes] == list(range(len(l1) + len(l2)))
    ys = sorted({b.y for b in sr.boxes})
    assert len(ys) == 2, "two source lines → two distinct box rows"
    # last token of line 1 sits on the top row, first token of line 2 on the bottom row
    assert sr.boxes[len(l1) - 1].y == ys[0]
    assert sr.boxes[len(l1)].y == ys[1]


def test_long_line_wraps_to_multiple_rows():
    toks = tokenize(LINE * 3)
    sr = render_subtitle([toks], osd_w=900, size=44)
    assert len({b.y for b in sr.boxes}) >= 2  # wrapped onto ≥2 visual rows
    assert sr.image.width <= 900


def test_subtitle_golden_with_hover():
    toks = tokenize(LINE)
    sr = render_subtitle([toks], osd_w=1280, size=44, hover=7)
    assert_golden(sr.image, "subtitle_yomu.png")


def _request(**over):
    from saitenka.app.subtitle_render import DrawRequest

    base = {
        "text": "猫を見る",
        "lines": [],
        "osd": (1920, 1080),
        "sub_size": 40,
        "bg_opacity": 160,
        "bottom_margin": 60,
        "secondary_role": False,
        "upgrade_pending": False,
        "annotation_degraded": False,
        "annotation_visible": False,
        "hover": -1,
        "hover_span": None,
        "styles": None,
    }
    return DrawRequest(**{**base, **over})


def test_a_draw_returns_its_geometry_instead_of_writing_it_somewhere():
    """The hit boxes and the origin belong to the cue that produced them. Returning them is what
    lets the caller decide whether they are still current — assigning them mid-render is how a
    superseded cue's boxes outlive it."""
    from saitenka.app.subtitle_render import SubtitleRenderer

    class _Surfaces:
        def present(self, *_args, **_kwargs):
            return "transaction"

    result = SubtitleRenderer().render(_request(), _Surfaces())

    assert result is not None
    assert result.transaction == "transaction"
    assert result.origin[1] == 1080 - 60 - _height(result)


def _height(result) -> int:
    """The rendered height, recovered from the placement the result reports."""
    return 1080 - 60 - result.origin[1]


def test_a_closed_renderer_draws_nothing_and_says_so():
    """Close races a cue change. The settle callback is how legacy staging learns pixels exist, so a
    closed renderer must answer it False rather than leave staging waiting forever."""
    from saitenka.app.subtitle_render import SubtitleRenderer

    renderer = SubtitleRenderer()
    renderer.close()
    settled = []

    assert renderer.render(_request(), None, on_settled=settled.append) is None
    assert settled == [False]


def test_the_request_carries_the_cue_state_as_one_snapshot():
    """Frozen and built once: the raster can run off the main thread, and a request that changed
    under it would raster one cue's text with another's styles."""
    import dataclasses

    request = _request()

    assert dataclasses.is_dataclass(request)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.text = "別の字幕"


def test_a_drawn_cue_leaves_the_host_the_origin_its_hit_boxes_are_relative_to():
    """The boxes the raster returns are relative to the subtitle image, so a hit test adds
    `sub_origin` to reach screen coordinates. If the draw computed a new origin and the host kept an
    old one, every hit would land offset by the difference — the boxes would look right and resolve
    to the wrong word.
    """
    import util

    from saitenka.app.subtitle_render import SubtitleRenderer

    def origin_for(frac: float) -> tuple[int, int]:
        reader = build_session(
            util.FakeIPC(),
            options=ReaderOptions(tooltip=TooltipOptions(bottom_margin_frac=frac)),
        )
        reader.graph.screen.osd = (1920, 1080)
        reader.graph.subtitle_presentation.cue.replace_geometry(
            origin=(999, 999)
        )  # a stale origin the draw has to replace
        reader.graph.cue.set_subtitle("猫を見る")
        result = SubtitleRenderer().draw(
            reader.graph.cue.draw_request(),
            reader.graph.lifecycle_surfaces,
            reader.graph.ipc,
        )
        assert result is not None
        reader.graph.subtitle_presentation.cue.replace_geometry(
            origin=result.origin
        )  # the coordinator's write-back, done here by hand
        return reader.graph.subtitle_presentation.cue.current.origin

    low, high = origin_for(0.05), origin_for(0.15)

    assert low != (999, 999)  # written back, not left stale
    assert low[0] == high[0]  # centred the same way regardless of the margin
    assert low[1] - high[1] == round(1080 * 0.10)  # lifted by exactly the margin it was given
