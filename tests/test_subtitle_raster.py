"""WP4.3: the reducer picks plain vs styled; a provider only prepares the raster."""

from __future__ import annotations

import pytest

from saitenka.app.subtitle_raster import (
    AnnotationOverlay,
    PillowRasterProvider,
    RasterContent,
    RasterStyle,
    SubtitleRasterRequest,
    annotation_visible,
    build_request,
    raster_style,
)


def token(surface: str = "猫"):
    from saitenka.app.tokenize import Token

    return Token(surface=surface, lemma=surface, reading="", pos="", start=0, end=len(surface))


BACKGROUND = (0, 0, 0, 128)


def request(**overrides: object) -> SubtitleRasterRequest:
    values: dict = {
        "style": RasterStyle.STYLED,
        "text": "猫を見る",
        "lines": [[token()]],
        "width": 1920,
        "size": 40,
        "annotated": True,
        "hover": -1,
        "hover_span": None,
        "styles": ["scored"],
        **overrides,
    }
    content = RasterContent(
        values["text"], values["lines"], values["width"], values["size"], BACKGROUND
    )
    overlay = AnnotationOverlay(
        values["annotated"], values["hover"], values["hover_span"], values["styles"]
    )
    return build_request(values["style"], content, overlay)


# --- the plain/styled decision -----------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["secondary_role", "upgrade_pending", "annotation_degraded"],
)
def test_a_cue_with_no_annotation_to_show_publishes_plain(reason: str) -> None:
    flags = dict.fromkeys(("secondary_role", "upgrade_pending", "annotation_degraded"), False)
    flags[reason] = True

    assert raster_style(**flags) is RasterStyle.PLAIN


def test_an_annotated_target_cue_publishes_styled() -> None:
    style = raster_style(secondary_role=False, upgrade_pending=False, annotation_degraded=False)

    assert style is RasterStyle.STYLED


def test_annotations_show_in_full_mode_or_while_hovering() -> None:
    assert annotation_visible(mode="full", hover_annotation=False)
    assert annotation_visible(mode="hover", hover_annotation=True)
    assert not annotation_visible(mode="hover", hover_annotation=False)


# --- request assembly --------------------------------------------------------------------------


def test_a_plain_request_carries_text_and_no_annotation_inputs() -> None:
    built = request(style=RasterStyle.PLAIN)

    assert built.text == "猫を見る"
    assert built.lines == ()
    assert (built.hover, built.hover_end, built.styles) == (None, None, None)


def test_an_unannotated_styled_request_drops_hover_and_styles() -> None:
    built = request(annotated=False, hover=2, styles=["scored"])

    assert (built.hover, built.hover_end, built.styles) == (None, None, None)


def test_a_hover_span_drives_the_underline_over_the_hovered_token() -> None:
    """A phrase span can start before the hovered token (a leading お in お休み)."""
    built = request(hover=3, hover_span=(2, 4))

    assert (built.hover, built.hover_end) == (2, 4)


def test_without_a_span_the_hovered_token_underlines() -> None:
    built = request(hover=3, hover_span=None)

    assert (built.hover, built.hover_end) == (3, None)


def test_no_hover_underlines_nothing() -> None:
    built = request(hover=-1, hover_span=None)

    assert built.hover is None


def test_a_request_is_immutable_and_holds_no_live_sequence() -> None:
    lines = [[token()]]
    built = request(lines=lines)
    lines.append([token("犬")])

    assert len(built.lines) == 1
    with pytest.raises(AttributeError):
        built.style = RasterStyle.PLAIN  # type: ignore[misc]


# --- provider neutrality -----------------------------------------------------------------------


def test_the_pillow_provider_satisfies_the_same_contract_as_a_fake() -> None:
    from util import RecordingRasterProvider

    built = request(style=RasterStyle.PLAIN)
    shipped = PillowRasterProvider().render(built)
    faked = RecordingRasterProvider().render(built)

    for result in (shipped, faked):
        assert result.image.width > 0
        assert isinstance(result.boxes, tuple)
    assert faked is not None


def test_the_pillow_provider_renders_both_styles() -> None:
    provider = PillowRasterProvider()

    plain = provider.render(request(style=RasterStyle.PLAIN))
    styled = provider.render(request(style=RasterStyle.STYLED, styles=None))

    assert plain.image.width > 0
    assert styled.image.width > 0
