"""The corrections a token needs when it is drawn on its own instead of inside its line."""

from __future__ import annotations

from itertools import pairwise

from saitenka_subtitles import TokenAnnotation
from saitenka_subtitles.fragments import (
    Fragment,
    FragmentRequest,
    fragments_from,
    probe_document,
    run_tags,
)

_REQUESTS = (
    FragmentRequest(0, "猫", "sans-serif", 40.0),
    FragmentRequest(1, "見る", "sans-serif", 40.0),
)
_RGB = (0x010000, 0x010001)


def test_the_probe_draws_each_token_alone_in_the_form_the_overprint_uses() -> None:
    r"""The probe is only informative if it asks for the same layout the overprint will: one
    ``\an7\pos``-ed event per token, its own face and size, no border or shadow to inflate the ink."""
    layout = probe_document(_REQUESTS, _RGB, (640, 360))

    events = [line for line in layout.document.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == len(_REQUESTS)
    for event, slot in zip(events, layout.slots, strict=True):
        x, y = slot.anchor
        assert rf"\an7\pos({x},{y})" in event
        assert r"\bord0\shad0" in event
    assert events[0].endswith("猫")
    assert r"\fnsans-serif\fs40" in events[0]


def test_each_token_gets_a_row_of_its_own() -> None:
    """Two tokens sharing a row could have their ink attributed to the wrong one; the pitch is taken
    from the largest font in the batch so a big token cannot reach into the row below."""
    layout = probe_document(_REQUESTS, _RGB, (640, 360))

    ys = [slot.anchor[1] for slot in layout.slots]
    assert ys == sorted(set(ys))
    assert min(b - a for a, b in pairwise(ys)) > 40


def test_an_offset_becomes_the_anchor_that_puts_the_ink_where_the_word_is() -> None:
    """The correction, in the direction that matters: ink landing 6px low means anchoring 6px high."""
    layout = probe_document(_REQUESTS, _RGB, (640, 360))
    # Ink landing six pixels down-right of each token's own anchor row.
    first, second = (slot.anchor for slot in layout.slots)
    fragments = fragments_from(
        [
            (0, first[0] + 6, first[1] + 6, 30, 30),
            (1, second[0] + 1, second[1] + 6, 59, 28),
        ],
        _REQUESTS,
        layout,
    )

    assert fragments[0] == Fragment(0, 6, 6, 30, 30)
    assert fragments[0].anchor_for(241, 295) == (235, 289)
    assert fragments[1].anchor_for(305, 297) == (304, 291)


def test_a_token_the_probe_could_not_recover_is_absent_rather_than_guessed() -> None:
    """No correction is better than an invented one: the caller keeps today's behaviour for it."""
    layout = probe_document(_REQUESTS, _RGB, (640, 360))
    second = layout.slots[1].anchor
    fragments = fragments_from([(1, second[0] + 1, second[1] + 6, 59, 28)], _REQUESTS, layout)

    assert 0 not in fragments
    assert 1 in fragments


def test_an_extent_that_survives_isolation_is_a_token_the_overprint_can_redraw() -> None:
    assert Fragment(0, 1, 6, 30, 30).matches(30, 30)
    assert Fragment(0, 1, 6, 25, 28).matches(26, 28)  # one pixel of independent rounding


def test_an_extent_that_changes_is_a_token_the_overprint_must_not_redraw() -> None:
    """Shaped with its neighbours a token can take a different width, and redrawing it alone puts a
    colored smear beside the word. The raster device tints the mask instead, which cannot drift."""
    assert not Fragment(0, 1, 6, 44, 25).matches(51, 25)
    assert not Fragment(0, 1, 6, 30, 30).matches(30, 37)


def test_a_token_after_a_literal_break_gets_its_own_characters() -> None:
    r"""Token offsets are defined against the normalized text — ``\r`` dropped, literal ``\N`` folded
    to one newline. Slicing the raw text instead shifts every token past the break by a character, so
    each probes a neighbour's string and takes a correction measured for different glyphs.
    """
    from saitenka_subtitles.ass_geometry import _token_surface

    normalized = "猫を見る\n犬も見る"
    tokens = (
        TokenAnnotation(0, 0, 1),
        TokenAnnotation(1, 2, 4),
        TokenAnnotation(2, 5, 6),
        TokenAnnotation(3, 7, 9),
    )

    assert [_token_surface(normalized, tokens, index) for index in range(4)] == [
        "猫",
        "見る",
        "犬",
        "見る",
    ]


def test_a_run_that_typesets_nothing_emits_the_bytes_it_always_did() -> None:
    """The payload string is the calibration's cache key, so a cue overriding none of these must
    produce exactly what it produced before they were carried."""
    assert run_tags(0.0, 100.0) == ""


def test_each_run_field_reaches_the_payload() -> None:
    assert run_tags(8.0, 50.0, bold=True, italic=True) == r"\fsp8\fscx50\b1\i1"


_SPACED = (FragmentRequest(0, "すごい", "sans-serif", 40.0, spacing=4.0),)


def test_only_a_spaced_run_is_probed_glyph_by_glyph() -> None:
    r"""Splitting a token costs a probe row and a payload event per glyph, so it is spent only where
    it buys something: letter spacing is the one condition that makes mpv's two libass instances
    segment shaping runs differently."""
    assert _SPACED[0].per_glyph
    assert not FragmentRequest(0, "すごい", "sans-serif", 40.0).per_glyph  # no spacing
    assert not FragmentRequest(0, "猫", "sans-serif", 40.0, spacing=4.0).per_glyph  # one glyph


def test_a_spaced_token_is_measured_inside_its_run_and_again_alone() -> None:
    r"""Both are needed and neither alone is enough: where the glyph sits *within* the token is what
    the redraw has to reproduce, and where it sits when drawn by itself is what ``\pos`` will give
    it. The offset the payload needs is the difference."""
    layout = probe_document(_SPACED, range(0x010000, 0x010010), (640, 360))

    inside = [slot for slot in layout.slots if not slot.alone]
    alone = [slot for slot in layout.slots if slot.alone]
    assert [slot.glyph_index for slot in inside] == [0, 1, 2]
    assert [slot.glyph_index for slot in alone] == [0, 1, 2]
    # The whole token is one event; each glyph drawn alone gets a row to itself.
    assert len({slot.anchor for slot in inside}) == 1
    assert len({slot.anchor for slot in alone}) == 3


def test_a_glyph_offset_is_where_it_sits_in_the_run_minus_where_it_sits_alone() -> None:
    """The arithmetic the payload depends on, in one place.

    A glyph 80px into its token that lands 2px right of its own anchor when drawn by itself must be
    anchored at +78, so its ink lands back at +80.
    """
    layout = probe_document(_SPACED, range(0x010000, 0x010010), (640, 360))
    row = next(slot.anchor for slot in layout.slots if not slot.alone)
    alone = [slot.anchor for slot in layout.slots if slot.alone]
    measured = [
        (0, row[0] + 0, row[1] + 5, 30, 30),
        (1, row[0] + 40, row[1] + 5, 30, 30),
        (2, row[0] + 80, row[1] + 5, 30, 30),
        (3, alone[0][0] + 0, alone[0][1] + 5, 30, 30),
        (4, alone[1][0] + 1, alone[1][1] + 5, 30, 30),
        (5, alone[2][0] + 2, alone[2][1] + 5, 30, 30),
    ]

    fragment = fragments_from(measured, _SPACED, layout)[0]

    assert fragment.glyph_dx == (0, 39, 78)
    assert fragment.glyph_dy == (0, 0, 0)


def test_a_spaced_token_missing_one_glyph_is_dropped_whole() -> None:
    """Half a per-glyph layout would draw some of the token and silently leave the rest uncoloured,
    which reads as a rendering bug rather than a missing measurement."""
    layout = probe_document(_SPACED, range(0x010000, 0x010010), (640, 360))
    row = next(slot.anchor for slot in layout.slots if not slot.alone)
    alone = [slot.anchor for slot in layout.slots if slot.alone]

    complete = [
        (0, row[0], row[1], 30, 30),
        (1, row[0] + 40, row[1], 30, 30),
        (2, row[0] + 80, row[1], 30, 30),
        (3, alone[0][0], alone[0][1], 30, 30),
        (4, alone[1][0], alone[1][1], 30, 30),
        (5, alone[2][0], alone[2][1], 30, 30),
    ]

    assert 0 in fragments_from(complete, _SPACED, layout)
    assert 0 not in fragments_from(complete[:-1], _SPACED, layout)  # one lone glyph unmeasured
    assert 0 not in fragments_from(complete[1:], _SPACED, layout)  # one in-run glyph unmeasured
