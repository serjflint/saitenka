"""Do both subtitle engines resolve the same token under the same cursor?

Their layouts are Pillow and libass and will never agree on pixels, so a pixel differential between
them is not an oracle — it would fail on every cue and say nothing. What a user notices is a
*different word* under the cursor, so that is the level the comparison lives at: sample a grid of
positions and ask each engine's boxes which token is there.

The legacy renderer is this work's comparison target, and this is what makes it one.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from saitenka.app.subtitles import WordBox, render_subtitle, token_at
from saitenka.app.tokenize import Token

CUE = "猫を見る犬も見る"
OSD = (1280, 720)


def cue_tokens() -> list[Token]:
    surfaces = [
        ("猫", "名詞"),
        ("を", "助詞"),
        ("見る", "動詞"),
        ("犬", "名詞"),
        ("も", "助詞"),
        ("見る", "動詞"),
    ]
    tokens: list[Token] = []
    offset = 0
    for surface, pos in surfaces:
        tokens.append(Token(surface, surface, surface, pos, offset, offset + len(surface)))
        offset += len(surface)
    return tokens


def legacy_boxes(tokens: list[Token]) -> tuple[list[WordBox], tuple[int, int]]:
    """Boxes the way the legacy renderer produces them, and the origin they are drawn at."""
    rendered = render_subtitle([tokens], OSD[0], size=44)
    return list(rendered.boxes), (0, 0)


def native_boxes(tokens: list[Token], boxes: list[WordBox]) -> list[WordBox]:
    """Stand-in for the measuring renderer's boxes: the same tokens, laid out slightly differently.

    A real libass layout would differ from Pillow's by more than this; the point of the oracle is
    that it tolerates a different layout and only fails on a different *answer*, so the fixture is
    a perturbation small enough to keep every token's centre inside its own box.
    """
    del tokens
    return [WordBox(box.index, box.x + 1, box.y - 1, max(1, box.w - 2), box.h) for box in boxes]


def sample_points(boxes: list[WordBox]) -> list[tuple[float, float]]:
    """Interior points across every box, plus the seams between neighbours."""
    points = [
        (box.x + box.w * fraction, box.y + box.h / 2)
        for box in boxes
        for fraction in (0.25, 0.5, 0.75)
    ]
    points += [
        ((left.x + left.w + right.x) / 2, left.y + left.h / 2) for left, right in pairwise(boxes)
    ]
    return points


def resolved(boxes: list[WordBox], points: list[tuple[float, float]]) -> list[int]:
    return [token_at(boxes, point, (0, 0), is_skippable=lambda _index: False) for point in points]


def disagreements(
    left: list[WordBox], right: list[WordBox], points: list[tuple[float, float]]
) -> list[tuple[tuple[float, float], int, int]]:
    """Points where both engines name a token and name a DIFFERENT one.

    A point one engine leaves empty is not a disagreement about a word: the two layouts have
    different advances, so their seams sit a pixel or two apart and no user can tell. Asserting on
    those would encode a requirement neither engine can meet and make the oracle fail on every cue —
    which is how an oracle stops being read.
    """
    return [
        (point, mine, theirs)
        for point, mine, theirs in zip(
            points, resolved(left, points), resolved(right, points), strict=True
        )
        if mine != -1 and theirs not in {-1, mine}
    ]


def test_both_engines_resolve_the_same_token_under_the_same_cursor() -> None:
    tokens = cue_tokens()
    legacy, _origin = legacy_boxes(tokens)
    native = native_boxes(tokens, legacy)
    points = sample_points(legacy)

    assert disagreements(legacy, native, points) == []
    # Both must actually resolve every token somewhere, or the comparison is two lists of -1.
    assert set(resolved(legacy, points)) >= {box.index for box in legacy}
    assert set(resolved(native, points)) >= {box.index for box in native}


def test_the_same_token_oracle_can_fail() -> None:
    """Negative control. An oracle nobody has seen fail is not evidence — and this one deliberately
    tolerates a pixel disagreement, so it has to be shown to bite on the thing it is for: a cursor
    landing on a different word."""
    tokens = cue_tokens()
    legacy, _origin = legacy_boxes(tokens)
    # One token's worth of horizontal drift: exactly the fault the hit boxes exist to prevent.
    drift = legacy[1].x - legacy[0].x
    shifted = [WordBox(box.index, box.x + drift, box.y, box.w, box.h) for box in legacy]

    assert disagreements(legacy, shifted, sample_points(legacy))


@pytest.mark.parametrize("origin", [(0, 0), (40, 600)])
def test_the_origin_moves_the_answer_with_the_boxes(origin: tuple[int, int]) -> None:
    """The boxes are in subtitle-local coordinates and the cursor is in frame coordinates, so the
    origin is part of the resolution. A native snapshot publishes boxes already in frame
    coordinates and an origin of (0, 0); the legacy render publishes local ones."""
    tokens = cue_tokens()
    legacy, _ = legacy_boxes(tokens)
    box = legacy[2]
    centre = (box.x + box.w / 2 + origin[0], box.y + box.h / 2 + origin[1])

    assert token_at(legacy, centre, origin, is_skippable=lambda _index: False) == box.index


def test_a_skippable_token_is_never_the_answer() -> None:
    """Punctuation and whitespace are drawn and not interactive; both engines must agree on that
    too, or one of them offers a tooltip the other refuses."""
    tokens = cue_tokens()
    legacy, _ = legacy_boxes(tokens)
    box = legacy[1]
    centre = (box.x + box.w / 2, box.y + box.h / 2)

    assert token_at(legacy, centre, (0, 0), is_skippable=lambda index: index == 1) != 1
