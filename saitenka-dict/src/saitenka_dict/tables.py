"""In-RAM projections of ``term_meta`` for the per-token hot path.

The semantic store answers a *lookup* — one term, everything known about it. Colouring a subtitle asks
the opposite question of every token on screen every frame, so it reads a plain dict instead: these
load once at startup and never touch SQLite again.

They satisfy `saitenka-wordstate`'s ``FrequencyTable`` / ``LevelTable`` protocols structurally, which
is what keeps a word-state classifier from having to know a dictionary exists.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

#: N1 is the hardest level, so it wins when a term appears under several.
_LEVEL_RANK = {"N1": 1, "N2": 2, "N3": 3, "N4": 4, "N5": 5}


@dataclass
class JlptDict:
    """term|reading → JLPT level, from a level dictionary's ``term_meta`` rows.

    A level dictionary rides the ``freq`` mode with the level in ``disp`` and a ``-1`` rank sentinel,
    which is how Yomitan ships them.
    """

    by_key: dict[str, str]

    @classmethod
    def from_connection(cls, connection: sqlite3.Connection, dict_id: int) -> JlptDict:
        by_key: dict[str, str] = {}
        for term, reading, disp in connection.execute(
            "SELECT term, reading, disp FROM term_meta WHERE dict_id=? AND mode='freq'", (dict_id,)
        ):
            if disp in _LEVEL_RANK:
                cls._put(by_key, term, disp)
                cls._put(by_key, reading, disp)
        return cls(by_key)

    @staticmethod
    def _put(by_key: dict[str, str], key: str | None, level: str) -> None:
        if not key:
            return
        current = by_key.get(key)
        if current is None or _LEVEL_RANK[level] < _LEVEL_RANK[current]:
            by_key[key] = level

    def level(self, *forms: str | None) -> str | None:
        for form in forms:
            if form and form in self.by_key:
                return self.by_key[form]
        return None


@dataclass
class FreqDict:
    """term|reading → frequency rank (lowest, i.e. most frequent, wins)."""

    by_key: dict[str, int]
    title: str = ""

    @classmethod
    def from_connection(
        cls,
        connection: sqlite3.Connection,
        dict_id: int,
        title: str = "",
        *,
        top_x: int | None = None,
    ) -> FreqDict:
        """Load one frequency dictionary's ranks.

        ``top_x`` caps the load to ``rank <= top_x``. A banded consumer cannot colour a rarer word
        anyway (:meth:`band` returns ``None`` past its cap), so loading the tail is pure startup cost
        — JPDBv2 is 279k rows of which ~10k fall inside a typical cap. ``None`` loads everything, for
        a consumer that colours on mere presence rather than on a band.
        """
        sql = "SELECT term, reading, rank FROM term_meta WHERE dict_id=? AND mode='freq'"
        params: tuple[object, ...] = (dict_id,)
        if top_x is not None:
            sql += " AND rank <= ?"
            params = (dict_id, top_x)
        by_key: dict[str, int] = {}
        for term, reading, rank in connection.execute(sql, params):
            cls._put(by_key, term, rank)
            cls._put(by_key, reading, rank)
        return cls(by_key, title)

    @staticmethod
    def _put(by_key: dict[str, int], key: str | None, rank: int | None) -> None:
        if not key or rank is None or rank <= 0:
            return
        current = by_key.get(key)
        if current is None or rank < current:
            by_key[key] = rank

    def rank(self, *forms: str | None) -> int | None:
        ranks = [self.by_key[form] for form in forms if form and form in self.by_key]
        return min(ranks) if ranks else None

    @staticmethod
    def band(rank: int, top_x: int = 10000, bands: int = 5) -> int | None:
        if rank <= 0 or rank > top_x:
            return None
        return min(bands, max(1, math.ceil(rank / top_x * bands)))
