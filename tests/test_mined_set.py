"""The mined set's generation is derived from membership, never reported alongside it."""

from __future__ import annotations

import pytest

from saitenka.app.mined_set import MinedSet


def test_a_new_expression_moves_the_generation() -> None:
    mined = MinedSet()
    assert mined.generation == 0
    assert mined.add("読む") is True
    assert "読む" in mined
    assert mined.generation == 1


def test_re_mining_a_word_already_in_the_deck_leaves_every_cached_panel_valid() -> None:
    """REGRESSION: the duplicate path bumped the counter unconditionally, so mining a word twice
    invalidated every panel keyed on the generation for a membership change that never happened."""
    mined = MinedSet({"読む"})
    assert mined.add("読む") is False
    assert mined.generation == 0


def test_a_bulk_seed_moves_the_generation_once_and_only_if_something_is_new() -> None:
    mined = MinedSet({"読む"})
    assert mined.update({"読む", "書く", "話す"}) is True
    assert mined.generation == 1
    assert mined.update({"書く"}) is False
    assert mined.generation == 1


def test_the_generation_cannot_be_written() -> None:
    """The whole point of the type: no caller can claim a change the set did not make."""
    mined = MinedSet()
    with pytest.raises(AttributeError):
        mined.generation = 7  # type: ignore[misc]


def test_it_reads_as_the_set_it_replaced() -> None:
    mined = MinedSet({"読む", "書く"})
    assert len(mined) == 2
    assert frozenset(mined) == {"読む", "書く"}
    assert mined == {"読む", "書く"}
    assert mined != {"読む"}


def test_a_snapshot_is_a_value_that_a_later_mine_cannot_move() -> None:
    """The copy readers hold must not track the set — panel keys are compared against it later.

    `frozenset(a_plain_set)` used to give this for free. Reading through `MinedSet` goes via Python
    `__iter__`, so the copy is the class's job now, and taking it under the lock is what stops a
    concurrent mine resizing the set mid-iteration.
    """
    mined = MinedSet({"猫"})
    held = mined.snapshot()

    mined.add("犬")

    assert held == {"猫"}
    assert mined.snapshot() == {"猫", "犬"}


def test_iteration_does_not_raise_when_a_write_lands_during_it() -> None:
    """A writer mid-iteration is a resize; without the snapshot this is `Set changed size`."""
    mined = MinedSet(str(n) for n in range(500))
    seen = 0
    for _ in mined:
        seen += 1
        mined.add(f"new-{seen}")  # noqa: B909  # the mutation IS the assertion here

    assert seen == 500  # the loop walked the snapshot, not the live set
