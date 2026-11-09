"""Lookup interaction through imported dictionaries and player input."""

import pytest
from util import assert_golden

from saitenka.app.config import ReaderOptions


@pytest.mark.parametrize("viewport", [(1920, 1080), (1280, 720)])
@pytest.mark.parametrize("offset", [0, 3])
def test_pointing_at_a_rendered_word_opens_its_dictionary(
    lookup_session, viewport, offset, monkeypatch
):
    from saitenka.app.features.tooltip import tooltip

    copied = []
    monkeypatch.setattr(tooltip, "copy_clipboard", copied.append)
    ui, _anki = lookup_session(
        viewport=viewport,
        records={
            "terms": [
                ["本命", "ほんめい", ["favorite"]],
                ["読む", "よむ", ["read"]],
            ]
        },
    )
    ui.subtitle("本命を読む").hover_character(offset).move_into_tip().press("MBTN_RIGHT")

    assert len(ui.dictionary_images) == 1
    assert copied == ["本命【ほんめい】" if offset == 0 else "読む【よむ】"]


def test_pointing_outside_subtitles_does_not_open_a_dictionary(lookup_session):
    ui, _anki = lookup_session()
    ui.subtitle("読む").move(0, 0)

    assert not ui.dictionary_images


def test_right_click_copies_the_hovered_word(lookup_session, monkeypatch):
    from saitenka.app.features.tooltip import tooltip

    copied = []
    monkeypatch.setattr(tooltip, "copy_clipboard", copied.append)
    ui, _anki = lookup_session(records={"terms": [["本命", "ほんめい", ["favorite"]]]})
    ui.subtitle("本命を読む").hover_character(0).move_into_tip().press("MBTN_RIGHT")

    assert copied == ["本命【ほんめい】"]


def test_clicking_a_dictionary_link_opens_its_lookup(lookup_session):
    ui, _anki = lookup_session(
        records={
            "terms": [
                [
                    "読む",
                    "よむ",
                    [
                        {
                            "type": "structured-content",
                            "content": {
                                "tag": "a",
                                "href": "?query=見る",
                                "content": "見る",
                            },
                        }
                    ],
                ],
                ["見る", "みる", ["see"]],
            ]
        }
    )
    ui.subtitle("読む").hover_character(0).click_link("見る")

    assert_golden(ui.dictionary_images[-1], "dictionary_link_lookup.png")


def test_hover_pauses_playback_and_leaving_releases_it(lookup_session):
    ui, _anki = lookup_session()
    ui.ipc.set_prop("pause", value=False)
    ui.session.pump()
    ui.subtitle("読む").hover_character(0)
    assert ("set_property", "pause", True) in ui.ipc.commands

    ui.leave().finish_hover_delay()

    pauses = [c[2] for c in ui.ipc.commands if c[:2] == ("set_property", "pause")]
    assert pauses[-1] is False


@pytest.mark.parametrize(
    ("height", "override"),
    [
        (1080, 0.0),
        (2160, 0.0),
        (1080, 1.5),
        (2160, 1.5),
        (2161, 0.0),
        (2159, 0.0),
        (1621, 0.0),
        (1080, 1.53),
    ],
)
def test_tooltip_scale_keeps_published_pixels_inside_the_viewport(lookup_session, height, override):
    width = round(height * 16 / 9)
    ui, _anki = lookup_session(
        viewport=(width, height), options=ReaderOptions().with_overrides(tip_scale=override)
    )
    ui.subtitle("読む").hover_character(0)

    assert len(ui.dictionary_frames) == 1
    image, x, y = ui.dictionary_frames[0]
    assert 0 <= x < x + image.width <= width
    assert 0 <= y < y + image.height <= height
