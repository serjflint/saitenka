"""The in-RAM `term_meta` projections: what they load, and what they deliberately don't."""

from __future__ import annotations

import sqlite3

import pytest
from saitenka_dict import FreqDict, JlptDict
from saitenka_dict.schema import ensure_schema


@pytest.fixture
def connection(tmp_path):
    connection = sqlite3.connect(tmp_path / "d.sqlite")
    ensure_schema(connection)
    yield connection
    connection.close()


def _term_meta(connection, rows):
    connection.executemany(
        "INSERT INTO term_meta(dict_id, term, mode, reading, rank, disp) VALUES(?,?,?,?,?,?)", rows
    )
    connection.commit()


def test_a_term_is_reachable_by_its_reading_as_well_as_its_headword(connection):
    _term_meta(connection, [(1, "本", "freq", "ほん", 42, None)])

    table = FreqDict.from_connection(connection, 1)

    assert table.rank("本") == 42
    assert table.rank("ほん") == 42
    assert table.rank("犬") is None


def test_the_most_frequent_rank_wins_when_a_form_appears_twice(connection):
    """Frequency lists give the same surface several entries (a SUW and a LUW segmentation, say).
    Colouring needs one number, and the lowest rank is the one the learner meets."""
    _term_meta(
        connection, [(1, "本", "freq", "ほん", 900, None), (1, "本", "freq", "もと", 42, None)]
    )

    assert FreqDict.from_connection(connection, 1).rank("本") == 42


def test_a_rank_past_the_cap_is_not_loaded_at_all(connection):
    """A banded consumer cannot colour past its cap, so loading the tail is pure startup cost —
    JPDBv2 is 279k rows of which ~10k fall inside a typical one."""
    _term_meta(
        connection, [(1, "近", "freq", None, 5, None), (1, "稀", "freq", None, 50_000, None)]
    )

    assert FreqDict.from_connection(connection, 1, top_x=10_000).rank("稀") is None
    assert FreqDict.from_connection(connection, 1).rank("稀") == 50_000


def test_a_non_positive_rank_is_not_a_rank(connection):
    """The level dictionaries ride the freq mode with a `-1` sentinel; it must never colour as
    rank 1, the most frequent word there is."""
    _term_meta(connection, [(1, "本", "freq", None, -1, "N5"), (1, "犬", "freq", None, 0, None)])

    table = FreqDict.from_connection(connection, 1)

    assert table.rank("本") is None
    assert table.rank("犬") is None


def test_only_the_named_dictionary_is_loaded(connection):
    _term_meta(connection, [(1, "本", "freq", None, 1, None), (2, "犬", "freq", None, 2, None)])

    assert FreqDict.from_connection(connection, 1).rank("犬") is None


def test_the_hardest_level_wins_when_a_term_carries_several(connection):
    """N1 is the hardest, so a term listed under both N1 and N5 is an N1 word for the learner."""
    _term_meta(
        connection, [(1, "本", "freq", "ほん", -1, "N5"), (1, "本", "freq", "もと", -1, "N1")]
    )

    assert JlptDict.from_connection(connection, 1).level("本") == "N1"


def test_a_freq_row_that_is_not_a_level_is_ignored(connection):
    """Level tables read the same `freq` mode as a real frequency list; only `disp` distinguishes them."""
    _term_meta(connection, [(1, "本", "freq", "ほん", 42, "4073㋕")])

    assert JlptDict.from_connection(connection, 1).level("本") is None


@pytest.mark.parametrize(
    ("rank", "band"),
    [(1, 1), (2000, 1), (2001, 2), (10_000, 5), (10_001, None), (0, None), (-1, None)],
)
def test_the_band_partitions_the_cap_evenly(rank, band):
    assert FreqDict.band(rank) == band
