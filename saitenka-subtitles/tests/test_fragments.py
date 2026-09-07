"""The corrections a token needs when it is drawn on its own instead of inside its line."""

from __future__ import annotations

from itertools import pairwise

from saitenka_subtitles import TokenAnnotation
from saitenka_subtitles.fragments import (
    Fragment,
    FragmentRequest,
    fragments_from,
    probe_document,
)

_REQUESTS = (
    FragmentRequest(0, "猫", "sans-serif", 40.0),
    FragmentRequest(1, "見る", "sans-serif", 40.0),
)
_RGB = (0x010000, 0x010001)


def test_the_probe_draws_each_token_alone_in_the_form_the_overprint_uses() -> None:
    r"""The probe is only informative if it asks for the same layout the overprint will: one
    ``\an7\pos``-ed event per token, its own face and size, no border or shadow to inflate the ink."""
    document, anchors = probe_document(_REQUESTS, _RGB, (640, 360))

    events = [line for line in document.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == len(_REQUESTS)
    for event, (x, y) in zip(events, anchors, strict=True):
        assert rf"\an7\pos({x},{y})" in event
        assert r"\bord0\shad0" in event
    assert events[0].endswith("猫")
    assert r"\fnsans-serif\fs40" in events[0]


def test_each_token_gets_a_row_of_its_own() -> None:
    """Two tokens sharing a row could have their ink attributed to the wrong one; the pitch is taken
    from the largest font in the batch so a big token cannot reach into the row below."""
    _document, anchors = probe_document(_REQUESTS, _RGB, (640, 360))

    ys = [y for _x, y in anchors]
    assert ys == sorted(set(ys))
    assert min(b - a for a, b in pairwise(ys)) > 40


def test_an_offset_becomes_the_anchor_that_puts_the_ink_where_the_word_is() -> None:
    """The correction, in the direction that matters: ink landing 6px low means anchoring 6px high."""
    fragments = fragments_from(
        [(0, 70, 106, 30, 30), (1, 65, 206, 59, 28)], _REQUESTS, ((64, 100), (64, 200))
    )

    assert fragments[0] == Fragment(0, 6, 6, 30, 30)
    assert fragments[0].anchor_for(241, 295) == (235, 289)
    assert fragments[1].anchor_for(305, 297) == (304, 291)


def test_a_token_the_probe_could_not_recover_is_absent_rather_than_guessed() -> None:
    """No correction is better than an invented one: the caller keeps today's behaviour for it."""
    fragments = fragments_from([(1, 65, 206, 59, 28)], _REQUESTS, ((64, 100), (64, 200)))

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
