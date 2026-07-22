"""Data providers for subtitle coloring and tooltip pills: JLPT level, frequency rank, known-words.

Frequency and pitch dictionaries are imported into the consolidated :class:`~overlay.app.dictdb.DictionaryDb`
(their ``term_meta`` rows), so these classes are thin **views** over that DB — nothing re-parses a zip at
runtime. :class:`FreqSource` / :class:`PitchSource` query the DB per lookup (tooltip pills, on demand);
:class:`FreqDict` / :class:`JlptDict` load a small in-RAM dict once (the per-token coloring hot path).

The freq-value shapes seen in the wild (all handled at import time in ``dictdb``, and reflected in the
``term_meta`` columns ``reading`` / ``rank`` / ``disp``):
  - value form:     ``[term, "freq", {"value": rank, "displayValue": "rank㋕"}]``       (term = kana)
  - frequency form: ``[term, "freq", {"reading": r, "frequency": rank}]``               (term = word)
  - JLPT:           ``[term, "freq", {"reading": r, "frequency": {"value": -1, "displayValue": "N5"}}]``
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import re
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay.resources import asset
from datetime import UTC

if TYPE_CHECKING:
    from overlay.app.dictdb import DictionaryDb, DictRow

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _crc_lenient():
    """Temporarily disable zipfile CRC-32 validation. Some Yomitan dict exporters (notably certain
    pitch-accent dicts) write wrong/zero CRCs even though the deflate data is perfectly intact;
    Python's strict check would otherwise reject them. Scoped + restored, single-threaded use."""
    orig = zipfile.ZipExtFile._update_crc  # type: ignore[attr-defined]  # deliberate
    zipfile.ZipExtFile._update_crc = lambda self, newdata: None  # type: ignore[attr-defined]
    try:
        yield
    finally:
        zipfile.ZipExtFile._update_crc = orig  # type: ignore[attr-defined]  # restore


def read_json_bank(zf: zipfile.ZipFile, name: str):
    """Read + parse one bank, tolerating a wrong stored CRC (the data is still valid). Returns the
    decoded list, or None only if the JSON itself is unparseable."""
    try:
        return json.loads(zf.read(name))
    except zipfile.BadZipFile:
        try:
            with _crc_lenient():
                return json.loads(zf.read(name))
        except (zipfile.BadZipFile, ValueError):
            return None
    except ValueError:
        return None


ASSETS = asset("wordlists")  # importlib.resources so the wheel path works too
JLPT_ZIP = ASSETS / "jlpt.zip"

_LEVEL_RANK = {"N1": 1, "N2": 2, "N3": 3, "N4": 4, "N5": 5}


def ensure_bundled_jlpt(db: DictionaryDb) -> int:
    """Import the bundled JLPT-level dictionary into ``db`` once, returning its ``dict_id``.

    JLPT levels ship with the tool (a small bundled asset, not a user import), so — unlike every other
    dictionary — the runtime imports it on first use. Idempotent: if a dictionary with the bundled
    title already exists it is reused (no rebuild). This is the one build the runtime performs; every
    other dictionary is built only by an explicit ``import`` command."""
    from datetime import datetime

    from overlay.app.dictdb import _title_of

    with zipfile.ZipFile(JLPT_ZIP) as zf:
        title = _title_of(zf, "JLPT")
    found, _missing = db.resolve([title])
    if found:
        return found[0].id
    row = db.import_zip(JLPT_ZIP, imported_at=datetime.now(UTC).isoformat(), import_order=-1)
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
    def from_db(cls, db: DictionaryDb, row: DictRow) -> FreqDict:
        """Load one frequency dictionary's ranks into an in-RAM dict for the coloring hot path."""
        by_key: dict[str, int] = {}
        for term, reading, rank in db._conn().execute(
            "SELECT term, reading, rank FROM term_meta WHERE dict_id=? AND mode='freq'", (row.id,)
        ):
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

    def display(self, forms, reading: str | None = None) -> str | None:
        """Display string for the first matching form. Prefer entries whose reading matches the
        token's (disambiguates 本命/ほんめい), else fall back to all entries for that term."""
        conn = self.db._conn()
        for f in forms:
            if not f:
                continue
            rows = conn.execute(
                "SELECT reading, disp, rank FROM term_meta WHERE dict_id=? AND mode='freq' "
                "AND term=?",
                (self.dict_id, f),
            ).fetchall()
            ents: list[tuple[str | None, str]] = []
            for r, disp, rank in rows:
                display = disp if disp is not None else (str(rank) if rank is not None else None)
                if display is not None:
                    ents.append((r, display))
            if not ents:
                continue
            matched = [d for (r, d) in ents if reading is None or r is None or r == reading]
            use = matched or [d for _, d in ents]
            seen: set[str] = set()
            out = [d for d in use if not (d in seen or seen.add(d))]  # type: ignore[func-returns-value]
            return ", ".join(out)
        return None


class PitchSource:
    """A pitch-accent dictionary → the ``reading [positions]`` label the tooltip shows, from the DB."""

    def __init__(self, db: DictionaryDb, row: DictRow):
        self.db = db
        self.dict_id = row.id
        self.title = row.title

    def accents(self, forms, reading: str | None = None) -> tuple[str, list[int]] | None:
        """Raw (reading, positions) for the first matching form — the pitch-graph input. Matches by
        term OR reading (a pitch dict is keyed by both)."""
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
                return (row[0], json.loads(row[1]))
        return None

    def display(self, forms, reading: str | None = None) -> str | None:
        got = self.accents(forms, reading)
        if got is None:
            return None
        r, positions = got
        return f"{r} [{','.join(str(p) for p in positions)}]"


_HTML_TAG = re.compile(r"<[^>]+>")
_FURI = re.compile(r"([^\s\[\]]+)\[([^\]]*)\]")  # Anki furigana segment: kanji[reading]

# Reading / furigana fields scanned in addition to the caller's fields, so readings are captured even
# when the note keeps them in a furigana field (Kanji Study `EntryFurigana`, Migaku/Lapis `*Furigana`).
_READING_FIELDS = (
    "Reading",
    "Word Reading",
    "ExpressionReading",
    "EntryFurigana",
    "Furigana",
    "ExpressionFurigana",
    "WordFurigana",
    "Kana",
)


def _field_forms(raw: str) -> list[str]:
    """Word forms a note field contributes to the known set: HTML stripped, and if it's Anki furigana
    (``お 孫[まご]さん``) both the plain surface (お孫さん) and the reading (おまごさん)."""
    val = _HTML_TAG.sub("", raw).strip()
    if not val:
        return []
    if "[" in val and "]" in val:
        surface = re.sub(r"\[[^\]]*\]", "", val).replace(" ", "")
        reading = _FURI.sub(lambda m: m.group(2), val).replace(" ", "")
        return [w for w in {surface, reading} if w]
    return [val]


@dataclass
class KnownWords:
    words: set[str]

    def is_known(self, *forms: str | None) -> bool:
        return any(f in self.words for f in forms if f)

    @classmethod
    def from_set(cls, it) -> KnownWords:
        return cls({w.strip() for w in it if w and w.strip()})

    @classmethod
    def from_ankiconnect(
        cls,
        decks: dict[str, list[str]],
        host: str = "http://127.0.0.1:8765",
        reading_fields=_READING_FIELDS,
    ) -> KnownWords:
        """Build the known set from Anki notes, mirroring SubMiner's decks→fields config. Requested
        fields that don't exist on a note are skipped; furigana fields yield both surface and reading."""
        import urllib.request

        def call(action, **params):
            body = json.dumps({"action": action, "version": 6, "params": params}).encode()
            req = urllib.request.Request(host, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read()).get("result")

        words: set[str] = set()
        for deck, fields in decks.items():
            ids = call("findNotes", query=f'deck:"{deck}"') or []
            for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
                for note in call("notesInfo", notes=chunk) or []:
                    nf = note.get("fields", {})
                    for fname in list(fields) + list(reading_fields):
                        words.update(_field_forms(nf.get(fname, {}).get("value", "")))
        return cls(words)
