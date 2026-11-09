"""Subtitle scanning through player input and published popup images."""

import pytest
from util import assert_golden


def test_component_replaces_the_visible_whole_word(lookup_session):
    ui, _anki = lookup_session("component")
    ui.subtitle("やり遂げたい").hover_character(0).hover_character(2)

    assert len(ui.dictionary_images) == 1
    assert_golden(ui.dictionary_images[0], "scan_component.png")


def test_leaving_a_component_restores_the_whole_word(lookup_session):
    ui, _anki = lookup_session("component")
    ui.subtitle("やり遂げたい").hover_character(0).hover_character(2)
    ui.hover_character(0).finish_hover_delay()

    assert len(ui.dictionary_images) == 1
    assert_golden(ui.dictionary_images[0], "scan_whole_word.png")


def test_escape_closes_component_and_parent(lookup_session):
    ui, _anki = lookup_session("component")
    ui.subtitle("やり遂げたい").hover_character(0).hover_character(2).press("ESC")

    assert not ui.dictionary_images


def test_down_scrolls_the_visible_component(lookup_session):
    ui, _anki = lookup_session("long_component")
    ui.subtitle("やり遂げたい").hover_character(0).hover_character(2)
    before = ui.dictionary_images[0]
    ui.press("DOWN")

    assert len(ui.dictionary_images) == 1
    assert ui.dictionary_images[0].tobytes() != before.tobytes()


@pytest.mark.parametrize("offset", [0, 1])
def test_lookup_starts_at_the_hovered_character(lookup_session, offset):
    ui, _anki = lookup_session("phrase")
    ui.subtitle("大活躍でしたね").hover_character(offset)
    images = ui.read_popup()

    assert len(ui.dictionary_images) == 1
    assert_golden(images[0], f"phrase_from_character_{offset}.png")
    assert_golden(images[-1], f"phrase_from_character_{offset}_end.png")
