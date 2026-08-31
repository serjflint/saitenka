"""The user's known-word set, read from their Anki collection.

Built either from AnkiConnect (mirroring SubMiner's decks→fields config) or from the persistent
SQLite cache, and matched reading-aware so a same-spelling homograph does not inherit a state it was
never taught. The dictionary-side views this used to sit beside now live in
:mod:`saitenka.app.dict_meta`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from itertools import starmap

from saitenka.app.known_cache import KnownCacheUpdate, known_cache_for
from saitenka.app.tokenize import has_kanji, kata_to_hira

log = logging.getLogger(__name__)


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
    from saitenka.app.anki import Anki

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
        if not surface or has_kanji(surface):
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
        from saitenka import otel_metrics

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
    from saitenka import otel_metrics

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
