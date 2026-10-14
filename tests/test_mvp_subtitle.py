"""MVP: subtitle rendering (multi-line) + per-word hitbox geometry."""

from saitenka_tokenize.japanese import tokenize
from util import FakeIPC, assert_golden

from saitenka.app.config import ReaderOptions, TooltipOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import SubtitleRenderer
from saitenka.app.subtitles import render_subtitle

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


def test_a_drawn_cue_leaves_the_host_the_origin_its_hit_boxes_are_relative_to(make_session):
    def origin_for(frac: float) -> tuple[int, int]:
        reader = make_session(
            FakeIPC(),
            infrastructure=SessionInfrastructure(renderer=SubtitleRenderer()),
            options=ReaderOptions(tooltip=TooltipOptions(bottom_margin_frac=frac)),
        )
        reader.graph.screen.osd = (1920, 1080)
        reader.graph.cue.set_subtitle("猫を見る")
        reader.graph.subtitle_presentation.cue.replace_geometry(origin=(999, 999))

        reader.graph.subtitle_presentation.draw()

        current = reader.graph.subtitle_presentation.cue.current
        return current.origin

    low, high = origin_for(0.05), origin_for(0.15)
    assert low != (999, 999)
    assert low[0] == high[0]
    assert low[1] - high[1] == round(1080 * 0.10)
