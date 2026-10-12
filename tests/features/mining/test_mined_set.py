"""The mined set's generation is derived from membership, never reported alongside it."""

from __future__ import annotations

import pytest

from saitenka.app.features.mining.mined_set import MinedSet


@pytest.mark.parametrize(
    ("write", "initial", "incoming", "known", "expected"),
    [
        (MinedSet.add, (), "読む", "読む", ("読む",)),
        (MinedSet.update, ("読む",), ("読む", "書く", "話す"), ("書く",), ("読む", "書く", "話す")),
    ],
)
def test_a_new_expression_moves_the_generation(write, initial, incoming, known, expected) -> None:
    mined = MinedSet(initial)
    generation = mined.generation

    assert write(mined, incoming) is True
    assert mined.snapshot() == set(expected)
    assert mined.generation > generation
    generation = mined.generation

    assert write(mined, known) is False
    assert mined.generation == generation


def test_re_mining_a_word_already_in_the_deck_leaves_every_cached_panel_valid() -> None:
    mined = MinedSet({"読む"})
    generation = mined.generation
    assert mined.add("読む") is False
    assert mined.generation == generation


def test_the_generation_cannot_be_written() -> None:
    mined = MinedSet()
    with pytest.raises(AttributeError):
        mined.generation = 7  # type: ignore[misc]


def test_a_snapshot_is_a_value_that_a_later_mine_cannot_move() -> None:
    mined = MinedSet({"猫"})
    held = mined.snapshot()

    mined.add("犬")

    assert held == {"猫"}
    assert mined.snapshot() == {"猫", "犬"}
