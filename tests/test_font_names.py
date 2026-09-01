"""Which families a font supplies, and which ones a document carries inside itself.

The answer decides whether the overprint may draw a token or must leave it uncolored, and both
directions of a wrong answer are silent on screen: a family missed here paints the word in a
substitute face, a family invented here costs a color that was available. So the oracle is a real
font's real name table, and a `[Fonts]` section encoded the way libass encodes one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from saitenka_subtitles import font_names
from util import uuencode

REPO_FONT = Path(__file__).resolve().parents[1] / "src/saitenka/assets/fonts/NotoSans.ttf"


def document_with_fonts(payload: str) -> bytes:
    return (
        "[Script Info]\nScriptType: v4.00+\n\n"
        f"[Fonts]\nfontname: Embedded_0.ttf\n{payload}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ).encode()


def test_a_fonts_families_are_read_from_its_name_table() -> None:
    found = font_names.families(REPO_FONT.read_bytes())

    assert "noto sans" in found
    assert all(name == name.casefold() for name in found)


def test_bytes_that_are_not_a_font_name_nothing() -> None:
    """An unparseable attachment must not raise: mpv loads whatever the container holds, and a
    container we cannot read is a demotion at worst, never a dead track."""
    assert font_names.families(b"not a font at all") == frozenset()
    assert font_names.families(b"") == frozenset()


def test_an_embedded_section_yields_the_same_families_as_the_font_itself() -> None:
    """The round trip that matters: the section decodes to the exact bytes, so the families it
    advertises are the ones the subtitle renderer will resolve — and the OSD one will not."""
    data = REPO_FONT.read_bytes()

    assert font_names.in_document(document_with_fonts(uuencode(data))) == font_names.families(data)


def test_a_document_without_an_embedded_section_supplies_nothing() -> None:
    assert font_names.in_document(b"[Script Info]\nScriptType: v4.00+\n") == frozenset()


def test_the_section_ends_where_the_next_one_begins() -> None:
    """A payload that swallowed `[Events]` would decode to garbage and quietly name no families —
    which reads exactly like a document that embeds nothing."""
    document = document_with_fonts(uuencode(REPO_FONT.read_bytes()))

    assert font_names.in_document(document) == font_names.in_document(
        document.replace(b"\n\n[Events]", b"\n\n[Graphics]\nfoo\n\n[Events]")
    )


@pytest.mark.parametrize("truncate_to", [1, 5, 9])
def test_a_group_of_one_trailing_character_is_the_bad_size_libass_refuses(truncate_to: int) -> None:
    """libass calls `size % 4 == 1` a bad encoded size and drops the font. Decoding it anyway would
    invent a family name out of misaligned bytes."""
    packed = "".join(uuencode(REPO_FONT.read_bytes()).split())[:truncate_to]

    assert (font_names._decode_uu(packed) == b"") is (len(packed) % 4 == 1)
