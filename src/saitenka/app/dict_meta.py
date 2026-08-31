"""Views over the dictionary DB's ``term_meta`` rows: JLPT level, frequency rank, pitch accent.

Frequency and pitch dictionaries are imported into the consolidated
:class:`~saitenka.app.dictdb.DictionaryDb`, so these classes re-parse nothing at runtime.
:class:`FreqSource` / :class:`PitchSource` query per lookup (tooltip pills, on demand);
:class:`FreqDict` / :class:`JlptDict` load a small in-RAM dict once (the per-token coloring hot path).

Split out of ``wordlists``, which held these beside the Anki-known set: two capabilities with nothing
in common but a filename. These read the DICTIONARY; what is left in ``wordlists`` reads the user's
COLLECTION. `saitenka-dict` already ships `FrequencySource` / `PronunciationSource` protocols this
duplicates, so the eventual home of this module is that package, not a word-state one.

The freq-value shapes seen in the wild (all handled at import time in ``dictdb``, and reflected in the
``term_meta`` columns ``reading`` / ``rank`` / ``disp``):
  - value form:     ``[term, "freq", {"value": rank, "displayValue": "rank㋕"}]``       (term = kana)
  - frequency form: ``[term, "freq", {"reading": r, "frequency": rank}]``               (term = word)
  - JLPT:           ``[term, "freq", {"reading": r, "frequency": {"value": -1, "displayValue": "N5"}}]``
"""

from __future__ import annotations

import json
import math
import zipfile
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

from saitenka.model import PitchAccent
from saitenka.resources import asset

if TYPE_CHECKING:
    from pathlib import Path

    from saitenka.app.dictdb import DictionaryDb, DictRow


def bundled_jlpt_zip() -> Path:
    """Where the bundled JLPT dictionary ships. A function, not an import-time constant: resolving the
    asset root when this module is *imported* is the application's layout decided by a library."""
    return asset("wordlists") / "jlpt.zip"


_LEVEL_RANK = {"N1": 1, "N2": 2, "N3": 3, "N4": 4, "N5": 5}


def ensure_bundled_jlpt(db: DictionaryDb, jlpt_zip: Path | None = None) -> int:
    """Import the bundled JLPT-level dictionary into ``db`` once, returning its ``dict_id``.

    JLPT levels ship with the tool (a small bundled asset, not a user import), so — unlike every other
    dictionary — the runtime imports it on first use. Idempotent: if a dictionary with the bundled
    title already exists it is reused (no rebuild). This is the one build the runtime performs; every
    other dictionary is built only by an explicit ``import`` command.

    ``jlpt_zip`` defaults to the bundled asset; passing one lets a caller supply the archive instead of
    inheriting this module's idea of where assets live."""
    from datetime import datetime

    from saitenka.app.bankreader import _title_of

    archive = jlpt_zip if jlpt_zip is not None else bundled_jlpt_zip()
    with zipfile.ZipFile(archive) as zf:
        title = _title_of(zf, "JLPT")
    found, _missing = db.resolve([title])
    if found:
        return found[0].id
    row = db.import_zip(archive, imported_at=datetime.now(UTC).isoformat(), import_order=-1)
    return row.id


@dataclass
class JlptDict:
    by_key: dict[str, str]  # term|reading -> level ("N1".."N5"), highest (N1) wins

    @classmethod
    def load(cls, db: DictionaryDb) -> JlptDict:
        """Load JLPT levels from the bundled dictionary in ``db`` (importing it on first use)."""
        dict_id = ensure_bundled_jlpt(db)
        by_key: dict[str, str] = {}
        for term, reading, disp in db._conn().execute(
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
        cur = by_key.get(key)
        if cur is None or _LEVEL_RANK[level] < _LEVEL_RANK[cur]:
            by_key[key] = level

    def level(self, *forms: str | None) -> str | None:
        for f in forms:
            if f and f in self.by_key:
                return self.by_key[f]
        return None


@dataclass
class FreqDict:
    by_key: dict[str, int]  # term|reading -> rank (lowest/most-frequent wins)
    title: str = ""

    @classmethod
    def from_db(cls, db: DictionaryDb, row: DictRow, *, top_x: int | None = None) -> FreqDict:
        """Load one frequency dictionary's ranks into an in-RAM dict for the coloring hot path.

        ``top_x`` caps the load to ``rank <= top_x`` — the banded scorer can't color a rarer word
        anyway (``FreqDict.band`` returns ``None`` past its cap), so loading the tail is pure startup
        cost (JPDBv2: 279k rows, ~10k within the cap → ~3x faster load). ``None`` loads everything, for
        a consumer that needs the full ranking (e.g. 'single' freq_mode, which colors on any presence)."""
        by_key: dict[str, int] = {}
        sql = "SELECT term, reading, rank FROM term_meta WHERE dict_id=? AND mode='freq'"
        params: tuple[object, ...] = (row.id,)
        if top_x is not None:
            sql += " AND rank <= ?"
            params = (row.id, top_x)
        for term, reading, rank in db._conn().execute(sql, params):
            cls._put(by_key, term, rank)
            cls._put(by_key, reading, rank)
        return cls(by_key, row.title)

    @staticmethod
    def _put(by_key: dict[str, int], key: str | None, rank: int | None) -> None:
        if not key or rank is None or rank <= 0:
            return
        cur = by_key.get(key)
        if cur is None or rank < cur:
            by_key[key] = rank

    def rank(self, *forms: str | None) -> int | None:
        ranks = [self.by_key[f] for f in forms if f and f in self.by_key]
        return min(ranks) if ranks else None

    @staticmethod
    def band(rank: int, top_x: int = 10000, bands: int = 5) -> int | None:
        if rank <= 0 or rank > top_x:
            return None
        return min(bands, max(1, math.ceil(rank / top_x * bands)))


class FreqSource:
    """One frequency dictionary as the tooltip shows it — a title + per-term display string(s), queried
    from the consolidated DB on demand.

    Keeps the human display value SubMiner shows in the pill row — the ``displayValue`` if present
    (``"8912, 143969㋕"``), else the raw rank. A term can have several entries (some freq lists give
    SUW+LUW → ``12813, 14117``); we join them, preferring entries whose reading matches the token's."""

    def __init__(self, db: DictionaryDb, row: DictRow):
        self.db = db
        self.dict_id = row.id
        self.title = row.title
        # ORIGINAL frequency mode, persisted at import. Occurrence-based dicts store a per-corpus
        # dense rank that is not comparable across dicts, so the harmonic-blend pill excludes them —
        # only true rank-based lists may be blended. Missing key (pre-persist imports) → rank-based.
        self.occurrence_based = db.meta_get(f"freqmode:{row.id}") == "occurrence"

    def _entries_for_form(self, conn, form: str) -> list[tuple[str | None, str]]:
        rows = conn.execute(
            "SELECT reading, disp, rank FROM term_meta WHERE dict_id=? AND mode='freq' AND term=?",
            (self.dict_id, form),
        ).fetchall()
        ents: list[tuple[str | None, str]] = []
        for r, disp, rank in rows:
            display = disp if disp is not None else (str(rank) if rank is not None else None)
            if display is not None:
                ents.append((r, display))
        return ents

    @staticmethod
    def _dedup_preferring_reading(ents: list[tuple[str | None, str]], reading: str | None) -> str:
        matched = [d for (r, d) in ents if reading is None or r is None or r == reading]
        use = matched or [d for _, d in ents]
        seen: set[str] = set()
        out = [d for d in use if not (d in seen or seen.add(d))]  # type: ignore[func-returns-value]
        return ", ".join(out)

    def display(self, forms, reading: str | None = None) -> str | None:
        """Display string for the first matching form. Prefer entries whose reading matches the
        token's (disambiguates 本命/ほんめい), else fall back to all entries for that term."""
        conn = self.db._conn()
        for f in forms:
            if not f:
                continue
            ents = self._entries_for_form(conn, f)
            if not ents:
                continue
            return self._dedup_preferring_reading(ents, reading)
        return None

    def rank(self, forms, reading: str | None = None) -> int | None:
        """Numeric rank for the first matching form (min across its entries) — the raw signal the
        harmonic-blend pill consumes, parallel to :meth:`display` which formats it for the row. When
        ``reading`` is given, entries whose reading matches it are preferred (so a multi-reading term
        like 退く scores のく separately from しりぞく for the card tie-breaker), falling back to all
        entries for the term when none match."""
        conn = self.db._conn()
        for f in forms:
            if not f:
                continue
            rows = conn.execute(
                "SELECT reading, rank FROM term_meta WHERE dict_id=? AND mode='freq' AND term=?",
                (self.dict_id, f),
            ).fetchall()
            ranks = [
                rk
                for r, rk in rows
                if rk is not None and rk > 0 and (reading is None or r is None or r == reading)
            ]
            use = ranks or [rk for _, rk in rows if rk is not None and rk > 0]
            if use:
                return min(use)
        return None


def _to_accent(a) -> PitchAccent:
    """Normalise one stored accent — a bare ``int`` (plain dict) or a ``{"p","d","n"}`` object (NHK/
    Kanjium, with devoice/nasal) — into a :class:`PitchAccent`."""
    if isinstance(a, dict):
        return PitchAccent(a["p"], tuple(a.get("d", ())), tuple(a.get("n", ())))
    return PitchAccent(a)


class PitchSource:
    """A pitch-accent dictionary → the ``reading [positions]`` label the tooltip shows, from the DB."""

    def __init__(self, db: DictionaryDb, row: DictRow):
        self.db = db
        self.dict_id = row.id
        self.title = row.title

    def accents(self, forms, _reading: str | None = None) -> tuple[str, list[PitchAccent]] | None:
        """Raw (reading, accents) for the first matching form — the pitch-graph input. Matches by term
        OR reading (a pitch dict is keyed by both). Each accent is a :class:`PitchAccent` carrying the
        downstep position plus any NHK/Kanjium devoice/nasal mora indices; a plain accent dict stores
        bare ints, normalised here to ``PitchAccent(n)``."""
        conn = self.db._conn()
        for f in forms:
            if not f:
                continue
            row = conn.execute(
                "SELECT reading, positions FROM term_meta WHERE dict_id=? AND mode='pitch' "
                "AND (term=? OR reading=?) LIMIT 1",
                (self.dict_id, f, f),
            ).fetchone()
            if row is not None:
                return (row[0], [_to_accent(a) for a in json.loads(row[1])])
        return None

    def display(self, forms, reading: str | None = None) -> str | None:
        got = self.accents(forms, reading)
        if got is None:
            return None
        r, accents = got
        return f"{r} [{','.join(str(a.position) for a in accents)}]"
