"""Do both subtitle engines resolve the same token under the same cursor?

Their layouts are Pillow and libass and will never agree on pixels, so a pixel differential between
them is not an oracle — it would fail on every cue and say nothing. What a user notices is a
*different word* under the cursor, so that is the level the comparison lives at: sample a grid of
positions and ask each engine's boxes which token is there.

The two engines also place the cue differently on the frame, and that is not a fault either — a
uniform shift or scale moves every word together and no one can tell. So the native boxes are
mapped onto the legacy cue's extent before the comparison, which leaves exactly the disagreement
that matters: one engine giving a word more or less of the line than the other, until a cursor lands
on the neighbour.

The legacy renderer is this work's comparison target, and this is what makes it one.
"""

from __future__ import annotations

import pytest
from saitenka_tokenize.japanese import Token

from saitenka.app.subtitles import WordBox, render_subtitle, token_at

CUE = "猫を見る犬も見る"
OSD = (1280, 720)

#: Token boundaries in `CUE`, as (start, end) character offsets.
SPANS = ((0, 1), (1, 2), (2, 4), (4, 5), (5, 6), (6, 8))

HEADER = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\nWrapStyle: 2\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
    "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: D,sans-serif,44,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
    "1,0,0,2,20,20,30,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
    "MarginV, Effect, Text\n"
)
EVENT_ROW = f"Dialogue: 0,0:00:01.00,0:00:03.00,D,,0,0,0,,{CUE}"


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


def native_boxes() -> list[WordBox]:
    """Boxes the way the measuring renderer produces them: real libass, through the real pipeline.

    Not a stand-in. The whole point of the comparison is that a libass layout is arrived at by a
    different route than Pillow's — its own shaper, its own metrics, its own line breaking — so a
    fixture derived from the legacy boxes would compare the legacy engine against itself.
    """
    from saitenka_subtitles import (
        GeometryPaletteEntry,
        GeometryRequest,
        SubtitleTrackId,
        TokenAnnotation,
    )
    from saitenka_subtitles.ass_geometry import prepare_ass_hit_map_frame
    from saitenka_subtitles.libass_backend import LibassGeometryBackend

    track = SubtitleTrackId("same-token-oracle")
    prepared = prepare_ass_hit_map_frame(
        (HEADER + EVENT_ROW + "\n").encode(),
        track,
        active_rows=EVENT_ROW,
        text=CUE,
        tokens=[TokenAnnotation(index, *span) for index, span in enumerate(SPANS)],
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(
            GeometryRequest(
                1,
                track,
                prepared.frame_id,
                2_000,
                OSD,
                OSD,
                prepared.ass,
                palette=tuple(
                    GeometryPaletteEntry(
                        entry.event_id, entry.token_index, entry.rgb, entry.font_name, 44.0
                    )
                    for entry in prepared.palette
                ),
                reserved_rgb=prepared.reserved_rgb,
            )
        )
    finally:
        backend.close()
    return [
        WordBox(
            token.token_index,
            token.bounds.x,
            token.bounds.y,
            token.bounds.width,
            token.bounds.height,
        )
        for token in snapshot.tokens
    ]


def onto(source: list[WordBox], target: list[WordBox]) -> list[WordBox]:
    """`source`'s boxes rescaled so its cue occupies `target`'s extent.

    A uniform shift or scale between the engines is not a fault — the whole line moves and every
    word keeps its share of it. Removing it is what leaves the comparison measuring the one thing a
    user can see: a word taking more or less of the line in one engine than in the other.

    Vertical placement is out of scope, not normalised: the row's `y`/`h` are taken from the target
    outright, so a native box at the wrong height would not be caught here. That is deliberate —
    the two engines' baselines and line heights genuinely differ — but it means this oracle speaks
    only about horizontal partitioning.
    """
    left, right = min(box.x for box in source), max(box.x + box.w for box in source)
    into_left, into_right = min(box.x for box in target), max(box.x + box.w for box in target)
    scale = (into_right - into_left) / (right - left)
    row = target[0]
    return [
        WordBox(
            box.index,
            round(into_left + (box.x - left) * scale),
            row.y,
            max(1, round(box.w * scale)),
            row.h,
        )
        for box in source
    ]


def sample_points(boxes: list[WordBox]) -> list[tuple[float, float]]:
    """Interior points across every box.

    Deliberately not the seams. Two shapers put a boundary a pixel or two apart on the same cue —
    measured, not assumed: real libass and Pillow disagree at one seam of this cue — and neither is
    wrong there. Asserting on the seams would fail on layouts a user cannot tell apart, which is how
    an oracle stops being read. A word actually landing in the wrong place moves its interior too.
    """
    return [
        (box.x + box.w * fraction, box.y + box.h / 2)
        for box in boxes
        for fraction in (0.25, 0.5, 0.75)
    ]


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


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_both_engines_resolve_the_same_token_under_the_same_cursor() -> None:
    pytest.importorskip("libasslite")
    legacy, _origin = legacy_boxes(cue_tokens())
    native = onto(native_boxes(), legacy)
    points = sample_points(legacy)

    assert [box.index for box in native] == [box.index for box in legacy]
    assert disagreements(legacy, native, points) == []
    # Both must actually resolve every token somewhere, or the comparison is two lists of -1.
    assert set(resolved(legacy, points)) >= {box.index for box in legacy}
    assert set(resolved(native, points)) >= {box.index for box in native}


def test_the_same_token_oracle_can_fail() -> None:
    """Negative control. An oracle nobody has seen fail is not evidence — and this one deliberately
    tolerates a pixel disagreement, so it has to be shown to bite on the thing it is for: a cursor
    landing on a different word."""
    legacy, _origin = legacy_boxes(cue_tokens())
    # One token's worth of horizontal drift: exactly the fault the hit boxes exist to prevent.
    drift = legacy[1].x - legacy[0].x
    shifted = [WordBox(box.index, box.x + drift, box.y, box.w, box.h) for box in legacy]

    assert disagreements(legacy, shifted, sample_points(legacy))


@pytest.mark.parametrize("origin", [(0, 0), (40, 600)])
def test_the_origin_moves_the_answer_with_the_boxes(origin: tuple[int, int]) -> None:
    """The boxes are in subtitle-local coordinates and the cursor is in frame coordinates, so the
    origin is part of the resolution. A native snapshot publishes boxes already in frame
    coordinates and an origin of (0, 0); the legacy render publishes local ones."""
    legacy, _ = legacy_boxes(cue_tokens())
    box = legacy[2]
    centre = (box.x + box.w / 2 + origin[0], box.y + box.h / 2 + origin[1])

    assert token_at(legacy, centre, origin, is_skippable=lambda _index: False) == box.index


def test_a_skippable_token_is_never_the_answer() -> None:
    """Punctuation and whitespace are drawn and not interactive; both engines must agree on that
    too, or one of them offers a tooltip the other refuses."""
    legacy, _ = legacy_boxes(cue_tokens())
    box = legacy[1]
    centre = (box.x + box.w / 2, box.y + box.h / 2)

    assert token_at(legacy, centre, (0, 0), is_skippable=lambda index: index == 1) != 1
