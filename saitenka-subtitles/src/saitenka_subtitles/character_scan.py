"""Optional Japanese character cells; semantic token identity remains unchanged."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from saitenka_subtitles.ass_geometry import prepare_ass_hit_map_frame
from saitenka_subtitles.document import TokenAnnotation
from saitenka_subtitles.geometry import CharacterMap, Rect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka_subtitles.document import SubtitleTrackId
    from saitenka_subtitles.geometry import CharacterGeometry


def character_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for index, char in enumerate(text):
        if unicodedata.category(char).startswith("M") and spans:
            spans[-1] = (spans[-1][0], index + 1)
        elif unicodedata.name(char, "").startswith(("CJK ", "HIRAGANA ", "KATAKANA")):
            spans.append((index, index + 1))
        else:
            return ()
    return tuple(spans) if len(spans) > 1 else ()


def bounds_of(regions: Sequence[Rect]) -> Rect:
    left, top = min(r.x for r in regions), min(r.y for r in regions)
    right = max(r.x + r.width for r in regions)
    bottom = max(r.y + r.height for r in regions)
    return Rect(left, top, right - left, bottom - top)


def disjoint_characters(cells: tuple[CharacterGeometry, ...]) -> bool:
    regions = [
        (index, rect)
        for index, cell in enumerate(cells)
        for rect in (cell.regions or (cell.bounds,))
    ]
    return all(
        index == other
        or rect.x + rect.width <= box.x
        or box.x + box.width <= rect.x
        or rect.y + rect.height <= box.y
        or box.y + box.height <= rect.y
        for position, (index, rect) in enumerate(regions)
        for other, box in regions[position + 1 :]
    )


def prepare_character_map(
    source: bytes,
    track_id: SubtitleTrackId,
    *,
    active_rows: str,
    text: str,
    tokens: tuple[TokenAnnotation, ...],
) -> CharacterMap | None:
    characters = tuple(
        TokenAnnotation(token.text_start + start, token.text_start + start, token.text_start + end)
        for token in tokens
        for start, end in character_spans(text[token.text_start : token.text_end])
    )
    if not characters:
        return None
    try:
        prepared = prepare_ass_hit_map_frame(
            source,
            track_id,
            active_rows=active_rows,
            text=text,
            tokens=characters,
        )
    except (ValueError, TypeError):
        return None
    return CharacterMap(prepared.ass, prepared.palette, prepared.reserved_rgb, tokens)
