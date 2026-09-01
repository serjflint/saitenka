"""Yomitan term-bank dictionaries → an ordered multi-dictionary lookup (the Yomitan experience).

Each :class:`Dictionary` is a **read-only view of one imported dictionary** inside the consolidated
:class:`~saitenka.app.dictdb.DictionaryDb` (scoped by ``dict_id``) — the dictionaries were built into that
DB **once, at import time**; nothing is parsed or rebuilt here. A word is looked up across an **ordered
list** of them, building a panel :class:`Entry` with one dictionary section per source. The structured-
content walker + panel renderer draw the dict-name pills and rich glossaries.

Term-bank v3 entry: ``[term, reading, defTags, rules, score, glossary[], sequence, termTags]``. A glossary
item is a plain string or ``{"type": "structured-content", "content": <node>}`` (also ``image``/``text``).
The dictionary form comes from the tokenizer lemma; the optional ``saitenka-deinflect``
add-on (GPL-3.0, Yomitan-derived) supplies the inflection chain when installed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka import otel_metrics

try:
    # Optional GPL-3.0 add-on (derived from Yomitan). When installed, the panel shows the inflection
    # chain (🧩 -て « -いる « -た) AND — for a second-language profile whose tokenizer has no lemma —
    # supplies the dictionary form(s) to look up (parapluies → parapluie). Without it the chain is
    # empty and second-language lookup falls back to the raw surface.
    from saitenka_deinflect import deinflect as _deinflect  # noqa: TID251  # GPL chokepoint
    from saitenka_deinflect import inflection_chain  # noqa: TID251  # GPL chokepoint
except ImportError:  # pragma: no cover — exercised via the deinflect-absent path

    def inflection_chain(surface: str, *targets: str, language: str = "ja") -> list[str]:  # noqa: ARG001  # must match saitenka_deinflect.inflection_chain's signature (structural compat check between the two try/except branches)
        return []

    def _deinflect(text: str, *, language: str = "ja") -> list[Deinflection]:  # noqa: ARG001  # must match saitenka_deinflect.deinflect's signature (conditional-variant check)
        return []


from saitenka.app.dictionary_surface import (
    DEINFLECT_FORM_CAP as _DEINFLECT_FORM_CAP,
)
from saitenka.app.dictionary_surface import (
    JP_LANGS as _JP_LANGS,
)

if TYPE_CHECKING:
    from saitenka.app.source_adapter import DictionarySourceAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka_deinflect import Deinflection  # noqa: TID251  # GPL chokepoint (type only)
    from saitenka_dict import LookupSource
    from saitenka_tokenize.japanese import Token

    from saitenka.app.dictdb import DictionaryDb, DictRow
    from saitenka.app.lookup import CardData
    from saitenka.panel import Entry

log = logging.getLogger(__name__)


class DictionaryError(RuntimeError):
    """A requested dictionary can't be used (e.g. a configured title was never imported)."""


_MISSING_HINT = (
    "These are dictionary TITLES with no imported dictionary. Import the source .zip files first: "
    "`saitenka import <dir-with-zips>` (or `import-settings <settings.json> --scan-dir <dir>`), "
    "then they resolve by title. Run `saitenka doctor` to see what's imported."
)

# Fanning `Dictionary.lookup()` out across a thread pool in `entry_for` (one dict per worker) was
# tried and measured under `--stress` with the GIL confirmed off (PYTHON_GIL=0): peak RSS rose ~54%
# (1.1 GB → 1.7 GB, the free-threaded allocator's per-thread arenas) with latency flat-to-worse at
# every percentile — msgspec.json.decode doesn't release the GIL enough for the dispatch overhead to
# pay for itself at this width (9 dicts). Reverted in favour of the plain sequential loop below.


def split_existing(paths: Sequence[str | Path]) -> tuple[list[str], list[str]]:
    """Partition ``paths`` into (existing, missing) files — used by the import command to keep the
    zips that exist and report the rest, rather than raising a raw ``FileNotFoundError``."""
    existing: list[str] = []
    missing: list[str] = []
    for p in paths:
        (existing if Path(str(p)).expanduser().exists() else missing).append(str(p))
    return existing, missing


_JMDICT_LIKE = re.compile(r"jmdict|jitendex", re.IGNORECASE)


def _looks_like_jmdict(title: str) -> bool:
    """True for a dict title that's JMdict itself or a JMdict-derived dict (Jitendex, …) — the only
    titles whose Yomitan ``seq`` is guaranteed to equal the Kanji Study ``ent_seq`` (#255). A non-JMdict
    dict's ``seq`` has no such guarantee, so its entries never populate ``card.idseq``: better no
    deep-link than a wrong one."""
    return bool(_JMDICT_LIKE.search(title))


class Dictionary:
    """One imported dictionary's identity within the consolidated DB.

    Reading it is `saitenka-dict`'s job; what a caller needs from this side is the title the semantic
    source is scoped by, and the handle to reach the file (cache keys, `stat`). It used to decode
    Yomitan rows and hold an LRU of them — `SqliteDictionaryStore` owns both now.
    """

    def __init__(self, db: DictionaryDb, row: DictRow):
        self.db = db
        self.dict_id = row.id
        self.title = row.title


def _empty_source() -> LookupSource:
    """The lookup source of a set with no dictionaries — an empty store, not ``None``.

    A set is built before its titles resolve (and a fresh install resolves none), so "nothing is
    configured" has to be an ordinary state. Modelling it as a source keeps every method on the one
    path instead of carrying a second, barely-exercised branch that drifts from it.
    """
    from saitenka_dict import EmptyDictionaryStore, Translator

    return Translator(EmptyDictionaryStore())


def _occurrence_based(db: DictionaryDb, freq_rows: Sequence[DictRow]) -> frozenset[str]:
    """Which of ``freq_rows`` store a per-corpus dense rank, from the mode the importer persisted.

    Asked of the package rather than read out of the ``meta`` table here: the key's spelling is the
    importer's business, and this module holding a copy of it is how the two-readers shape starts.
    """
    from saitenka_dict import DictionaryDatabase

    wanted = {row.title for row in freq_rows}
    if not wanted:
        return frozenset()
    return frozenset(
        info.title
        for info in DictionaryDatabase(db.path).list_dictionaries()
        if info.title in wanted and dict(info.metadata).get("frequency_mode") == "occurrence-based"
    )


@dataclass
class DictionarySet:
    dicts: list[Dictionary]
    #: Titles only. These used to be `FreqSource` / `PitchSource` — a second reader of `term_meta`
    #: alongside the store, with its own matching rules; what the set still needs from them is the
    #: title it passes to the source.
    freq_titles: list[str] = field(default_factory=list)
    pitch_titles: list[str] = field(default_factory=list)
    #: Of `freq_titles`, those whose rank is per-corpus dense rather than a real rank.
    occurrence_based: frozenset[str] = frozenset()
    # Active profile's main language (#254) — routes the deinflection chain to the right rule set.
    # Yomitan's ``jp`` default keeps every existing JP path byte-identical.
    language: str = "jp"
    source: LookupSource = field(default_factory=_empty_source)
    _adapter: DictionarySourceAdapter | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def _source_view(self) -> DictionarySourceAdapter:
        if self._adapter is None:
            from saitenka.app.source_adapter import DictionarySourceAdapter, SourceAdapterOptions

            dictionaries = tuple(
                dict.fromkeys(
                    (
                        *(dictionary.title for dictionary in self.dicts),
                        *self.freq_titles,
                        *self.pitch_titles,
                    )
                )
            )
            self._adapter = DictionarySourceAdapter(
                self.source,
                SourceAdapterOptions(
                    dictionaries=dictionaries,
                    sequence_dictionaries=tuple(
                        dictionary.title
                        for dictionary in self.dicts
                        if _looks_like_jmdict(dictionary.title)
                    ),
                    occurrence_based=self.occurrence_based,
                    language=self.language,
                    deinflected_forms=self._deinflected_candidates,
                    inflection_chain=self._source_inflection_chain,
                ),
            )
        return self._adapter

    def _source_inflection_chain(self, surface: str, targets: tuple[str, ...]) -> list[str]:
        return inflection_chain(surface, *targets, language=self.language)

    @classmethod
    def from_rows(
        cls,
        db: DictionaryDb,
        dict_rows: Sequence[DictRow],
        freq_rows: Sequence[DictRow] = (),
        pitch_rows: Sequence[DictRow] = (),
        *,
        language: str = "jp",
    ) -> DictionarySet:
        """Build an ordered dictionary set from already-resolved :class:`DictRow`s of the given DB."""
        from saitenka_dict import SqliteDictionaryStore, Translator

        class _CacheObserver:
            @staticmethod
            def hit() -> None:
                if otel_metrics.dict_cache_hits is not None:
                    otel_metrics.dict_cache_hits.add(1)

            @staticmethod
            def miss() -> None:
                if otel_metrics.dict_cache_misses is not None:
                    otel_metrics.dict_cache_misses.add(1)

            @staticmethod
            def eviction() -> None:
                if otel_metrics.dict_cache_evictions is not None:
                    otel_metrics.dict_cache_evictions.add(1)

        return cls(
            dicts=[Dictionary(db, r) for r in dict_rows],
            freq_titles=[r.title for r in freq_rows],
            pitch_titles=[r.title for r in pitch_rows],
            occurrence_based=_occurrence_based(db, freq_rows),
            language=language,
            source=Translator(
                SqliteDictionaryStore(
                    db.path,
                    entry_cache_max=db._opts.entry_cache_max,
                    cache_observer=_CacheObserver(),
                )
            ),
        )

    @classmethod
    def from_db(
        cls,
        db: DictionaryDb,
        dict_titles: Sequence[str] = (),
        freq_titles: Sequence[str] = (),
        pitch_titles: Sequence[str] = (),
        *,
        strict: bool = False,
        language: str = "jp",
    ) -> DictionarySet:
        """Resolve config **titles** to imported dictionaries of ``db`` and build the set, preserving
        order. Missing titles are skipped; with ``strict`` a single missing title raises
        :class:`DictionaryError` (the explicit-CLI / doctor path)."""
        d_rows, d_miss = db.resolve(dict_titles)
        f_rows, f_miss = db.resolve(freq_titles)
        p_rows, p_miss = db.resolve(pitch_titles)
        missing = [*d_miss, *f_miss, *p_miss]
        if strict and missing:
            raise DictionaryError(
                "dictionary title(s) not imported: "
                + ", ".join(repr(m) for m in missing)
                + ". "
                + _MISSING_HINT
            )
        return cls.from_rows(db, d_rows, f_rows, p_rows, language=language)

    def has_term(self, *forms: str | None) -> bool:
        """Any exact term/reading hit across the dictionaries? (kanji-fallback gate.)"""
        return self._source_view().has_term(*forms)

    def terms_exist(self, forms: Sequence[str]) -> set[str]:
        """The subset of ``forms`` that are an exact **term** (headword) in some dictionary — the batch
        existence probe backing dict-attested compound merging (:func:`~saitenka.app.tokenize.
        merge_dict_compounds`), mirroring anki_miner's ``offline_terms_exist``. One IN-list scan for the
        whole line via :meth:`_batch_exact`. Term-only, NOT reading: a kanji-compound candidate that
        merely coincides with some entry's *reading* must not license a false merge (unlike
        :meth:`has_term`, whose reading hits are wanted for the kanji-fallback gate)."""
        return self._source_view().terms_exist(forms)

    def rareness_rank(self, token) -> float | None:
        """The harmonic-mean rank of ``token`` across every **rank-based** frequency dictionary, or
        ``None`` when no such dictionary has the word.

        A read-only view, so a caller that wants the blend asks for it instead of walking the
        frequency dictionaries and re-deciding which of them may be blended.
        """
        return self._source_view().rareness_rank(token)

    def decoded_entry_count(self) -> int:
        """Decoded entries currently held by the active source's bounded per-dictionary LRUs."""
        return self._source_view().decoded_entry_count()

    def kanji_for(self, char: str, *, stroke_order: bool = False) -> Entry | None:
        """A panel :class:`Entry` for one kanji, from the first dict whose kanji_bank has it: big
        glyph headword, 音/訓 reading rows + numbered meanings, and the labeled/sectioned KANJIDIC stats
        — rendered through the normal panel path. ``stroke_order`` draws the headword in the numbered
        stroke-order font (the ``[tooltip] kanji_stroke_order`` toggle; visual only)."""
        return self._source_view().kanji_for(char, stroke_order=stroke_order)

    def frequency_field(self, token) -> tuple[str, str]:
        """(Frequency field HTML, FreqSort number) for a mined Lapis card — the same values the tooltip
        shows as green pills. Empty when no freq source has the word. The plan maps ``freq → Frequency``."""
        return self._source_view().frequency_field(token)

    def pitch_field(self, token) -> tuple[str, str]:
        """(pitch-accent field HTML, positions text) for a mined card — the same accents the tooltip
        shows as purple pills. Empty when no pitch source has the word. ``{pitch-accents}`` renders the
        HTML; ``{pitch-accent-positions}`` the bare numbers (e.g. ``0`` / ``0, 2``)."""
        return self._source_view().pitch_field(token)

    def card_for(self, token: Token, *, extra_terms: Sequence[str] = ()) -> CardData:
        """Mined-card fields (expression / reading / glossary) from the USER's dictionaries — the
        dict-first mining path. Returns the best entry (see :meth:`_best_hit`) of the first dictionary
        that has the word with a non-empty glossary; otherwise an expression-only CardData (empty
        ``glossary_html``) so the caller can fall back to the JMdict/jamdict source. No JMdict sequence
        id — Yomitan terms carry none. ``extra_terms`` are longer phrases (数ある) that outrank the bare
        word, so a hovered phrase mines the phrase by default."""
        return self._source_view().card_for(token, extra_terms=extra_terms)

    def cards_for(self, token: Token, *, extra_terms: Sequence[str] = ()) -> list[CardData]:
        """Every distinct entry (term + reading) across the user's dicts as a mineable CardData,
        best-first (same ranking ``card_for`` picks its default from) — the choices a per-entry mine
        button offers, mirroring Yomitan's stacked entries. The glossary for a (term, reading) comes
        from the first dict that has it with a non-empty glossary. Empty when no dict has a glossed hit,
        so the caller falls back to the JMdict source. ``cards_for(token)[0] == card_for(token)`` for a
        single dictionary. ``extra_terms`` (longer phrases 数ある) are looked up too and, being longer,
        sort ahead of the bare word."""
        return self._source_view().cards_for(token, extra_terms=extra_terms)

    def search(self, pattern: str, limit: int = 30) -> Entry:
        """Wildcard/prefix/suffix search across the dictionaries → a results :class:`Entry` that
        lists each matching headword as a **clickable** link: drilling into a result opens that
        exact term. ``pattern`` uses GLOB wildcards (``*``/``?``); a bare term prefix-matches via
        ``term*``."""
        return self._source_view().search(pattern, limit)

    def _deinflected_candidates(self, lemma: str) -> tuple[str, ...]:
        """Candidate dictionary forms for a second-language surface. The Latin tokenizer has no
        morphological analyzer, so its lemma is the inflected surface — the deinflector supplies the
        actual dictionary form(s) to look up (parapluies → parapluie, chats → chat). Empty for JP,
        whose MeCab lemma is already the dict form, so every JP lookup stays byte-identical. Bounded:
        the French suffix ruleset over-generates (harmless — a spurious form just misses in the DB —
        but it needn't bloat the IN-list)."""
        if self.language in _JP_LANGS or not lemma:
            return ()
        out = [
            d.text for d in _deinflect(lemma, language=self.language) if d.text and d.text != lemma
        ]
        return tuple(dict.fromkeys(out))[:_DEINFLECT_FORM_CAP]

    def entry_for(
        self, token: Token, inflected: str | None = None, *, extra_terms: Sequence[str] = ()
    ) -> Entry:
        return self._source_view().entry_for(token, inflected, extra_terms=extra_terms)
