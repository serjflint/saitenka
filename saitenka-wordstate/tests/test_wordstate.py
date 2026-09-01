"""The package's own suite: the contract a consumer that is not Saitenka can rely on.

Not a copy of the overlay's colouring tests — those assert palette RGBA and belong with the app that
owns the palette. These pin what makes this shippable: that a verdict carries no colour, that the
frequency and level tables are protocols rather than concrete types, and that the collection is
reached through an injected client.
"""

from __future__ import annotations

from dataclasses import fields

import pytest
from saitenka_tokenize import Token
from saitenka_wordstate import (
    KnownWords,
    Scorer,
    TokenVerdict,
    freq_band,
    harmonic_of,
    rareness_band,
)


class _Freqs:
    """A `FrequencyTable` that is not `FreqDict` — which is the point of the protocol."""

    def __init__(self, ranks: dict[str, int]):
        self.ranks = ranks

    def rank(self, *forms: str | None) -> int | None:
        found = [self.ranks[f] for f in forms if f and f in self.ranks]
        return min(found) if found else None


class _Levels:
    def __init__(self, levels: dict[str, str]):
        self.levels = levels

    def level(self, *forms: str | None) -> str | None:
        return next((self.levels[f] for f in forms if f and f in self.levels), None)


def _tok(surface: str, pos: str = "名詞") -> Token:
    return Token(surface, surface, "", pos, 0, len(surface))


def test_a_verdict_carries_no_colour():
    """The reason this is a library. A field here that held RGBA would make the package depend on a
    palette, which is exactly the edge the extraction cut."""
    names = {f.name for f in fields(TokenVerdict)}
    assert not any("color" in n or "colour" in n or "rgba" in n for n in names)
    assert names == {
        "is_content",
        "is_known",
        "fsrs_state",
        "n_plus",
        "jlpt",
        "freq_rank",
        "freq_band",
        "freq_single",
    }


def test_the_frequency_and_level_tables_are_protocols_not_types():
    """A stand-in that answers `rank` / `level` is enough — this package reads a collection, not a
    dictionary, so taking the app's concrete FreqDict/JlptDict would invert that."""
    scorer = Scorer(
        known=KnownWords.from_set([]),
        freq=_Freqs({"本": 500}),
        jlpt=_Levels({"猫": "N5"}),
        enable_n_plus_one=False,
    )
    assert scorer.verdict(_tok("本")).freq_band == 1
    assert scorer.verdict(_tok("猫")).jlpt == "N5"


def test_a_level_suppresses_the_frequency_signal():
    """SubMiner's rule: frequency speaks only when nothing else does. A levelled word reports no rank
    at all, not merely no colour."""
    scorer = Scorer(
        known=KnownWords.from_set([]),
        freq=_Freqs({"猫": 500}),
        jlpt=_Levels({"猫": "N5"}),
        enable_n_plus_one=False,
    )
    verdict = scorer.verdict(_tok("猫"))
    assert verdict.jlpt == "N5"
    assert verdict.freq_rank is None and verdict.freq_band is None


def test_a_function_word_is_never_content_however_known_it_is():
    scorer = Scorer(known=KnownWords.from_set(["を"]), enable_n_plus_one=False)
    verdict = scorer.verdict(_tok("を", pos="助詞"))
    assert verdict.is_known and not verdict.is_content and not verdict.is_mature


def test_known_matching_is_reading_aware():
    """A card teaching 床/ゆか must not mark 床/とこ as known — the homograph guard."""
    known = KnownWords.from_forms([_form("床", "ゆか")])
    assert known.is_known("床", None, "ゆか")
    assert not known.is_known("床", None, "とこ")


def _form(surface: str, reading: str):
    from saitenka_wordstate import KnownForm

    return KnownForm(surface, reading)


def test_the_collection_is_reached_through_an_injected_client():
    """No transport of its own: the consumer supplies one, so this package is testable without a
    running Anki and reusable against any note source."""

    class _Notes:
        @staticmethod
        def find_notes(_query: str) -> list[int]:
            return [1]

        @staticmethod
        def notes_info(ids: list[int]) -> list[dict]:
            return [{"noteId": i, "fields": {"Expression": {"value": "猫"}}} for i in ids]

    known = KnownWords.from_ankiconnect({"D": ["Expression"]}, _Notes())
    assert known.is_known("猫")


@pytest.mark.parametrize(
    ("rank", "band"), [(1, 1), (2000, 1), (2001, 2), (10_000, 5), (10_001, None)]
)
def test_freq_band_splits_the_cap_into_equal_slices(rank, band):
    assert freq_band(rank, top_x=10_000, bands=5) == band


def test_the_blend_and_its_bands_are_pure_numbers():
    assert harmonic_of([1000.0, 2000.0]) == pytest.approx(1333.33, rel=1e-3)
    assert harmonic_of([]) is None
    assert rareness_band(500) == "common"
    assert rareness_band(20_000) == "uncommon"
    assert rareness_band(40_000) == "rare"
