"""Preview controls use player input and actual published frame geometry."""

import numpy as np
import pytest

from saitenka.app.config import PanelOptions, ReaderOptions


def test_escape_closes_preview_after_tooltip_dismissal(lookup_session):
    ui, _anki = lookup_session(preview=True)
    ui.subtitle("読む").hover_character(0).add_entry().wait_for_preview()
    ui.leave().finish_hover_delay()
    assert not ui.dictionary_images
    assert ui.preview_frame is not None

    ui.press("ESC")

    assert ui.preview_frame is None


def test_escape_closes_help_before_the_preview(lookup_session):
    ui, _anki = lookup_session(preview=True)
    ui.subtitle("読む").hover_character(0).add_entry().wait_for_preview()
    ui.press("F1").press("ESC")

    assert not ui.help_visible
    assert ui.preview_frame is not None
    assert len(ui.dictionary_images) == 1


def test_narrow_preview_keeps_media_and_close_reachable(lookup_session):
    ui, _anki = lookup_session(
        preview=True,
        viewport=(480, 720),
        image_size=(1920, 1080),
        options=ReaderOptions(panels=PanelOptions(scale=2.0)),
    )
    ui.subtitle("読む").hover_character(0).add_entry().wait_for_preview()
    image, x, y = ui.preview_frame
    assert 0 <= x < x + image.width <= 480
    assert 0 <= y < y + image.height <= 720
    blue = np.asarray(image)[:, :, :3]
    assert ((blue[:, :, 2] > 150) & (blue[:, :, 0] < 60)).sum() > 1000

    ui.close_preview()

    assert ui.preview_frame is None


def test_preview_controls_grow_after_viewport_resize(lookup_session):
    ui, _anki = lookup_session(preview=True)
    ui.subtitle("読む").hover_character(0).add_entry().wait_for_preview()
    before, _x, _y = ui.preview_frame
    before_close = ui.preview_close_bounds[2:]
    ui.resize(3840, 2160)
    image, x, y = ui.preview_frame

    assert image.width == before.width * 2
    assert ui.preview_close_bounds[2:] == tuple(size * 2 for size in before_close)
    assert 0 <= x < x + image.width <= 3840
    assert 0 <= y < y + image.height <= 2160
    ui.close_preview()
    assert ui.preview_frame is None


def test_preview_uses_the_configured_ui_scale(lookup_session):
    normal, _anki = lookup_session(preview=True)
    normal.subtitle("読む").hover_character(0).add_entry().wait_for_preview()
    before, _x, _y = normal.preview_frame
    before_close = normal.preview_close_bounds[2:]
    enlarged, _anki = lookup_session(
        preview=True, options=ReaderOptions(panels=PanelOptions(scale=2.0))
    )

    enlarged.subtitle("読む").hover_character(0).add_entry().wait_for_preview()

    image, _x, _y = enlarged.preview_frame
    assert image.width == before.width * 2
    assert image.height > before.height * 1.8
    assert enlarged.preview_close_bounds[2:] == tuple(size * 2 for size in before_close)


@pytest.mark.parametrize("viewport", [(1920, 1080), (3840, 2160)])
def test_close_button_edge_is_clickable_at_both_scales(lookup_session, viewport):
    ui, _anki = lookup_session(preview=True, viewport=viewport)
    ui.subtitle("読む").hover_character(0).add_entry().wait_for_preview().close_preview()

    assert ui.preview_frame is None
