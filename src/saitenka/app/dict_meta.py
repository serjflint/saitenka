"""What the application decides *about* the dictionary DB's ``term_meta`` rows.

The tables themselves are `saitenka-dict`'s (:class:`saitenka_dict.FreqDict` /
:class:`saitenka_dict.JlptDict`, loaded once for the per-token colouring hot path). What stays here is
policy the package has no business holding: which JLPT dictionary ships with the tool and where its
asset lives, plus the two per-lookup tooltip sources.

:class:`FreqSource` / :class:`PitchSource` still query directly because they duplicate
:meth:`Translator.frequencies_for` / :meth:`~Translator.pronunciations_for` field-for-field but for
two gaps: ``occurrence_based``, which no package type carries, and :class:`PitchAccent`, an app render
type. Closing those retires both classes; until then they are the last app-side reads of this file.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC
from typing import TYPE_CHECKING

from saitenka.model import PitchAccent
from saitenka.resources import asset

if TYPE_CHECKING:
    from pathlib import Path

    from saitenka_dict import JlptDict

    from saitenka.app.dictdb import DictionaryDb, DictRow


def bundled_jlpt_zip() -> Path:
    """Where the bundled JLPT dictionary ships. A function, not an import-time constant: resolving the
    asset root when this module is *imported* is the application's layout decided by a library."""
    return asset("wordlists") / "jlpt.zip"


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


def load_jlpt(db: DictionaryDb) -> JlptDict:
    """The JLPT levels table, importing the bundled dictionary on first use."""
    from saitenka_dict import JlptDict as _JlptDict

    return _JlptDict.from_connection(db.connection(), ensure_bundled_jlpt(db))


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
        conn = self.db.connection()
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
        conn = self.db.connection()
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
        conn = self.db.connection()
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
