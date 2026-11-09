"""Frequency and layout assertions use the pixels actually published to mpv."""

from copy import deepcopy

import numpy as np
import pytest
from dicthelp import lookup_records
from util import assert_golden

COMMON = (74, 158, 92, 255)
RARE = (188, 86, 74, 255)


def _badge(images, color):
    for image in images:
        mask = np.all(np.asarray(image) == color, axis=2)
        if mask.sum() >= 100:
            # Source badges share green with the common difficulty badge.
            ys = np.flatnonzero(mask.any(axis=1))
            row = np.split(ys, np.flatnonzero(np.diff(ys) > 1) + 1)[-1]
            xs = np.flatnonzero(mask[row].any(axis=0))
            pill = np.split(xs, np.flatnonzero(np.diff(xs) > 1) + 1)[0]
            return image.crop((pill[0], row[0], pill[-1] + 1, row[-1] + 1))
    raise AssertionError(f"no visible badge with color {color}")


def _reference_badge(lookup_session, rank, color):
    ui, _anki = lookup_session(
        records={
            "terms": [["船内", "せんない", ["inboard"]]],
            "frequencies": [{"name": "Rank", "entries": [["船内", rank]]}],
        }
    )
    ui.subtitle("船内").hover_character(0)
    return np.asarray(_badge(ui.dictionary_images, color))


def test_difficulty_excludes_counts_and_duplicate_source_ranks(lookup_session):
    ui, _anki = lookup_session("frequency_sources")
    ui.subtitle("超満員").hover_character(0)
    images = ui.read_popup()

    assert np.array_equal(
        np.asarray(_badge(images, RARE)), _reference_badge(lookup_session, 54500, RARE)
    )
    assert np.array_equal(
        np.asarray(_badge(images, COMMON)), _reference_badge(lookup_session, 1400, COMMON)
    )
    assert_golden(images[0], "frequency_sources_first.png")
    assert_golden(images[-1], "frequency_sources_last.png")


@pytest.mark.parametrize(
    ("dataset", "cue"),
    [
        ("frequency_compound", "超満員"),
        ("frequency_many", "数ある"),
        ("frequency_honorific_rest", "お休み"),
        ("frequency_honorific_care", "お世話"),
        ("frequency_person", "人気者"),
    ],
)
def test_each_entry_displays_its_own_frequency(lookup_session, dataset, cue):
    ui, _anki = lookup_session(dataset)
    ui.subtitle(cue).hover_character(0)
    images = ui.read_popup()

    assert np.array_equal(
        np.asarray(_badge(images, RARE)), _reference_badge(lookup_session, 50000, RARE)
    )
    assert np.array_equal(
        np.asarray(_badge(images, COMMON)), _reference_badge(lookup_session, 1000, COMMON)
    )
    assert_golden(images[0], f"{dataset}.png")
    assert_golden(images[-1], f"{dataset}_end.png")


@pytest.mark.parametrize("cue", ["超満員", "超未登録"])
def test_missing_frequency_does_not_borrow_a_different_entry_rank(lookup_session, cue):
    ui, _anki = lookup_session("missing_frequency")
    ui.subtitle(cue).hover_character(0)
    images = ui.read_popup()

    assert not any(np.all(np.asarray(image) == RARE, axis=2).any() for image in images)
    assert np.array_equal(
        np.asarray(_badge(images, COMMON)), _reference_badge(lookup_session, 1000, COMMON)
    )
    name = "compound" if cue == "超満員" else "unknown_phrase"
    assert_golden(images[0], f"missing_frequency_{name}.png")
    assert_golden(images[-1], f"missing_frequency_{name}_end.png")


@pytest.mark.parametrize(
    ("dataset", "cue"),
    [
        ("canonical_spelling", "仕方ない"),
        ("canonical_verb", "読んだ"),
    ],
)
def test_frequency_follows_the_dictionary_entry(lookup_session, dataset, cue):
    ui, _anki = lookup_session(dataset)
    ui.subtitle(cue).hover_character(0)

    assert np.array_equal(
        np.asarray(_badge(ui.dictionary_images, RARE)),
        _reference_badge(lookup_session, 50000, RARE),
    )
    assert_golden(ui.dictionary_images[0], f"{dataset}.png")


def test_frequency_only_word_still_displays_difficulty(lookup_session):
    ui, _anki = lookup_session("frequency_only")
    ui.subtitle("猫").hover_character(0)

    assert np.array_equal(
        np.asarray(_badge(ui.dictionary_images, COMMON)),
        _reference_badge(lookup_session, 1500, COMMON),
    )
    assert_golden(ui.dictionary_images[0], "frequency_only.png")


def test_dense_badges_leave_room_for_the_first_definition(lookup_session):
    ui, _anki = lookup_session("dense")
    ui.subtitle("船内").hover_character(0)
    pages = ui.read_popup()
    for _ in pages:
        ui.press("UP")

    image = ui.dictionary_images[0]
    changed = deepcopy(lookup_records("dense"))
    changed["terms"][0][2] = ["船外。"]
    other, _anki = lookup_session(records=changed)
    other.subtitle("船内").hover_character(0)
    comparison = other.dictionary_images[0]

    assert image.size == comparison.size
    assert not np.array_equal(np.asarray(image)[-56:], np.asarray(comparison)[-56:])
