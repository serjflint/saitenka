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

import hashlib
import json
import logging
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC
from itertools import starmap
from typing import TYPE_CHECKING

from overlay.app.known_cache import KnownCacheUpdate, known_cache_for
from overlay.app.tokenize import _has_kanji, kata_to_hira
from overlay.model import PitchAccent
from overlay.resources import asset

if TYPE_CHECKING:
    from overlay.app.dictdb import DictionaryDb, DictRow

log = logging.getLogger(__name__)


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

    from overlay.app.bankreader import _title_of

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


def _field_parse(raw: str) -> tuple[str, str | None]:
    """The (surface, reading) one note field contributes: HTML stripped; Anki furigana
    (``お 孫[まご]さん``) splits into surface (お孫さん) + reading (おまごさん); a plain value is a bare
    surface with no reading."""
    val = _HTML_TAG.sub("", raw).strip()
    if not val:
        return "", None
    if "[" in val and "]" in val:
        surface = re.sub(r"\[[^\]]*\]", "", val).replace(" ", "")
        reading = _FURI.sub(lambda m: m.group(2), val).replace(" ", "")
        return surface, (reading or None)
    return val, None


#: meta-table key holding the config signature the cache was built under — a change (different decks or
#: fields) means the stored payload was extracted by a different rule, so the cache must fully rebuild.
_KNOWN_SIG_KEY = "anki_known_sig"

#: Bumped when the cached per-note payload shape changes, so an old-format cache invalidates via the
#: signature and rebuilds. v2 = ``[surface, reading]`` pairs (was v1 flat word strings).
_KNOWN_CACHE_FORMAT = 2


def _known_signature(decks: dict[str, list[str]]) -> str:
    """Stable short hash of the decks→fields config + payload format. The extracted forms depend on
    WHICH fields are read (and the payload shape), so this gates cache reuse (see
    :meth:`KnownWords.from_cache` / :func:`refresh_known_cache`)."""
    payload = json.dumps(
        {"fmt": _KNOWN_CACHE_FORMAT, "decks": {d: sorted(f) for d, f in sorted(decks.items())}},
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _ankiconnect(host: str, action: str, **params):
    """One AnkiConnect JSON-RPC call for the known-word coloring path — no retry, otel-traced (the
    IO/parse spans settle the IO-vs-CPU budget). Routes through the single :class:`Anki` client (SSOT),
    so auth (the apiKey this used to silently drop) and error handling live in one place."""
    from overlay.app.anki import Anki

    return Anki(host)._call(action, timeout=10, attempts=1, trace=True, **params)


@dataclass(frozen=True, slots=True)
class KnownForm:
    """One (surface, reading) a card teaches. ``reading`` is hiragana, or ``""`` when the card gave no
    reading (surface-only — can't disambiguate a homograph). ``surface`` is ``""`` for a reading-only
    note (kana-only match). Serialised to the cache as a compact ``[surface, reading]`` row."""

    surface: str
    reading: str

    def as_row(self) -> list[str]:
        return [self.surface, self.reading]


def _extract_forms(note: dict, fields, reading_fields) -> list[KnownForm]:
    """The :class:`KnownForm`s one note contributes to the known set. Surface fields give the written
    form (a furigana surface field also yields its reading); reading fields give the reading. Every
    surface is paired with every reading (a note is one word), readings folded to hiragana so they
    compare against a token's reading. This pairing is what lets coloring reject a same-spelling /
    different-reading homograph — see :meth:`KnownWords.is_known`."""
    nf = note.get("fields", {})
    surfaces: list[str] = []
    readings: list[str] = []
    for fname in fields:
        surface, reading = _field_parse(nf.get(fname, {}).get("value", ""))
        if surface:
            surfaces.append(surface)
        if reading:
            readings.append(reading)
    for fname in reading_fields:
        surface, reading = _field_parse(nf.get(fname, {}).get("value", ""))
        got = reading or surface  # a reading field's plain value IS the reading
        if got:
            readings.append(got)
    readings = [kata_to_hira(r) for r in readings]

    forms: list[KnownForm] = []
    seen: set[tuple[str, str]] = set()
    for s, r in _form_combos(surfaces, readings):
        if (s or r) and (s, r) not in seen:
            seen.add((s, r))
            forms.append(KnownForm(s, r))
    return forms


def _form_combos(surfaces: list[str], readings: list[str]):
    """Yield (surface, reading) combinations: cross every surface with every reading; a surface with no
    reading yields ``(surface, "")``; a reading with no surface yields ``("", reading)``."""
    if surfaces and readings:
        for s in surfaces:
            for r in readings:
                yield s, r
    elif surfaces:
        for s in surfaces:
            yield s, ""
    else:
        for r in readings:
            yield "", r


@dataclass
class KnownWords:
    """Reading-aware known set. ``by_surface`` maps each written surface to the readings the user's
    cards teach for it (``""`` = a card gave no reading); ``readings`` is every taught reading, for
    kana-only tokens. Matching keys on BOTH surface and reading, so a kanji token whose card teaches a
    *different* reading (床 とこ vs a known 床/ゆか card) is not falsely marked known."""

    by_surface: dict[str, set[str]]
    readings: set[str]

    @property
    def words(self) -> set[str]:
        """Every known form (surfaces + readings) — the flat view for counts/diagnostics."""
        return set(self.by_surface) | self.readings

    def is_known(
        self, surface: str | None, lemma: str | None = None, reading: str | None = None
    ) -> bool:
        read = kata_to_hira(reading) if reading else ""
        return self._surface_hit(surface, lemma, read) or self._kana_hit(surface, read)

    def _surface_hit(self, surface: str | None, lemma: str | None, read: str) -> bool:
        """A surface/lemma spelling is known and its taught readings are consistent with ``read``. A
        spelling taught only with a *different* reading is a same-spelling homograph — skipped, not
        matched (the bug this fix closes)."""
        for spelling in (surface, lemma):
            taught = self.by_surface.get(spelling) if spelling else None
            if taught is not None and (not read or "" in taught or read in taught):
                return True
        return False

    def _kana_hit(self, surface: str | None, read: str) -> bool:
        """A kana-only token (no competing kanji identity) matches any taught reading, so a kanji card
        resurfaces when its word appears written in kana."""
        if not surface or _has_kanji(surface):
            return False
        return bool(read and read in self.readings) or surface in self.readings

    @classmethod
    def from_forms(cls, forms) -> KnownWords:
        by_surface: dict[str, set[str]] = {}
        readings: set[str] = set()
        for f in forms:
            if f.surface:
                by_surface.setdefault(f.surface, set()).add(f.reading)
            if f.reading:
                readings.add(f.reading)
        return cls(by_surface, readings)

    @classmethod
    def from_cache(cls, store: object, decks: dict[str, list[str]]) -> KnownWords | None:
        """Build the known set from the persistent SQLite cache for an instant startup (~1 ms vs the
        ~190 ms AnkiConnect load). Returns ``None`` on a miss — an empty cache or a config signature
        that no longer matches (fields or payload format changed) — so the caller falls back to a full
        load."""
        cache = known_cache_for(store)
        if cache.metadata(_KNOWN_SIG_KEY) != _known_signature(decks):
            return None
        forms: list[KnownForm] = []
        seen_row = False
        for per_deck in cache.read(list(decks)).values():
            for _mod, note_rows in per_deck.values():
                seen_row = True
                forms.extend(starmap(KnownForm, note_rows))
        return cls.from_forms(forms) if seen_row else None

    @classmethod
    def from_set(cls, it) -> KnownWords:
        """Known set from bare surface forms (no reading info) — the offline/testing fallback."""
        return cls.from_forms(KnownForm(w.strip(), "") for w in it if w and w.strip())

    @classmethod
    def from_ankiconnect(
        cls,
        decks: dict[str, list[str]],
        host: str = "http://127.0.0.1:8765",
        reading_fields=_READING_FIELDS,
    ) -> KnownWords:
        """Build the known set from Anki notes, mirroring SubMiner's decks→fields config. Requested
        fields that don't exist on a note are skipped; furigana fields yield both surface and reading.

        One ``notesInfo`` call per deck with ALL its note ids (no batching — see the git history for why
        chunking over threads was a net loss). This is the un-cached full load; :func:`refresh_known_cache`
        is the cache-aware path that only re-fetches changed notes. Traced in three stages
        (``anki_http_call`` IO / ``anki_json_parse`` CPU / ``anki_known_extract`` CPU)."""
        from overlay import otel_metrics

        forms: list[KnownForm] = []
        for deck, fields in decks.items():
            ids = _ankiconnect(host, "findNotes", query=f'deck:"{deck}"') or []
            if not ids:
                continue
            notes = _ankiconnect(host, "notesInfo", notes=ids) or []
            with otel_metrics.traced("anki_known_extract", deck=deck, notes=str(len(notes))):
                for note in notes:
                    forms.extend(_extract_forms(note, fields, reading_fields))
        return cls.from_forms(forms)


def _fetch_forms(
    host: str, deck: str, ids: list[int], fields, reading_fields
) -> dict[int, list[KnownForm]]:
    """``{note_id: [KnownForm]}`` for a subset of note ids — the only heavy (``notesInfo``) call, made
    solely for the changed notes the diff selected."""
    from overlay import otel_metrics

    notes = _ankiconnect(host, "notesInfo", notes=ids) or []
    with otel_metrics.traced("anki_known_extract", deck=deck, notes=str(len(notes))):
        return {n["noteId"]: _extract_forms(n, fields, reading_fields) for n in notes}


def _refresh_deck(
    store: object, deck: str, fields, host: str, reading_fields, *, force_full: bool
) -> list[KnownForm]:
    """Reconcile one deck's cache against Anki by note mod-time, re-fetching only changed notes, and
    return its :class:`KnownForm`s. ``force_full`` (empty/stale cache) treats every note as changed, so
    no old-format cached payload is ever read back."""
    cache = known_cache_for(store)
    cached = cache.read([deck])[deck]  # {note_id: (mod, [[surface, reading]])}
    ids = _ankiconnect(host, "findNotes", query=f'deck:"{deck}"') or []
    mods = {n["noteId"]: n["mod"] for n in (_ankiconnect(host, "notesModTime", notes=ids) or [])}
    changed = (
        ids if force_full else [i for i in ids if mods.get(i) != (cached.get(i) or (None,))[0]]
    )
    deleted = [i for i in cached if i not in mods]
    fetched = _fetch_forms(host, deck, changed, fields, reading_fields) if changed else {}
    cache.write(
        KnownCacheUpdate(
            deck,
            tuple((i, mods.get(i, 0), [f.as_row() for f in forms]) for i, forms in fetched.items()),
            tuple(deleted),
        )
    )
    return _merge_forms(ids, fetched, cached)


def _merge_forms(ids, fetched, cached) -> list[KnownForm]:
    """Freshly-fetched forms for changed notes + the untouched cached forms for the rest, in deck
    order. Cached rows are ``[surface, reading]`` lists (JSON round-trip) → back to :class:`KnownForm`."""
    out: list[KnownForm] = []
    for i in ids:
        if i in fetched:
            out.extend(fetched[i])
        else:
            out.extend(starmap(KnownForm, (cached.get(i) or (0, []))[1]))
    return out


def refresh_known_cache(
    store: object,
    decks: dict[str, list[str]],
    host: str = "http://127.0.0.1:8765",
    reading_fields=_READING_FIELDS,
) -> KnownWords:
    """Cache-aware known-set load: per deck, diff against the SQLite cache by mod-time and re-fetch only
    the changed subset (a full fetch when the cache is empty or the config signature changed), update the
    cache, and return the fresh set. Its own RW connection makes it safe to run on a background thread."""
    sig = _known_signature(decks)
    cache = known_cache_for(store)
    force_full = cache.metadata(_KNOWN_SIG_KEY) != sig
    forms: list[KnownForm] = []
    for deck, fields in decks.items():
        forms.extend(
            _refresh_deck(cache, deck, fields, host, reading_fields, force_full=force_full)
        )
    cache.set_metadata(_KNOWN_SIG_KEY, sig)
    return KnownWords.from_forms(forms)
