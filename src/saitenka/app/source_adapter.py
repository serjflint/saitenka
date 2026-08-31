from __future__ import annotations

import html
import re
import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from saitenka_dict import (
    AttestationSource,
    KanjiQuery,
    LookupSource,
    MediaSource,
    SearchQuery,
    SearchSource,
    TermQuery,
    TermResultMode,
)

from saitenka import otel_metrics
from saitenka.app.dictionary_surface import (
    FREQ_COLOR,
    PITCH_COLOR,
)
from saitenka.app.dictionary_surface import (
    KANJI_STAT_SECTIONS as _KANJI_STAT_SECTIONS,
)
from saitenka.app.dictionary_surface import (
    SearchHit as _SearchHit,
)
from saitenka.app.dictionary_surface import entry_rank_key as _entry_rank_key
from saitenka.app.dictionary_surface import glossary_to_nodes as _glossary_to_nodes
from saitenka.app.dictionary_surface import (
    glosses_of as _glosses_of,
)
from saitenka.app.dictionary_surface import (
    search_result_nodes as _search_result_nodes,
)
from saitenka.app.dictionary_surface import (
    to_glob as _to_glob,
)
from saitenka.app.lookup import CardData, furigana
from saitenka.fonts import STROKE_ORDER_FONT
from saitenka.model import PitchAccent
from saitenka.panel import Definition, Entry, EntryGroup, Freq
from saitenka.render.sc_adapter import collect_img_paths

if TYPE_CHECKING:
    from collections.abc import Callable
from saitenka_dict import (
    FrequencySource as SemanticFrequencySource,
)
from saitenka_dict import (
    PronunciationSource as SemanticPronunciationSource,
)

# Sampled on prefetch workers, where a per-word span floods the trace and prefetch_decode already
# covers the phase. The histogram still records every call.
_BG_SQL_SPAN_SAMPLE = 8
_sql_tls = threading.local()


def _emit_sql_span() -> bool:
    """True → this ``dict_sql`` call gets a trace span. Always on the foreground (hover/cue) threads
    for full step resolution; 1-in-``_BG_SQL_SPAN_SAMPLE`` on ``saitenka-prefetch-*`` workers. The
    per-thread tick is race-free under free-threading (no shared counter)."""
    if not threading.current_thread().name.startswith("saitenka-prefetch"):
        return True
    n = getattr(_sql_tls, "tick", 0)
    _sql_tls.tick = n + 1
    return n % _BG_SQL_SPAN_SAMPLE == 0


def _short_freq_name(title: str) -> str:
    """Freq-pill display name: strip the ``Saitenka`` product prefix (``Saitenka Known`` → ``Known``)
    so our own frequency lists don't waste pill width. Case-insensitive; other dicts pass through."""
    for prefix in ("Saitenka ", "saitenka-"):
        if title.lower().startswith(prefix.lower()):
            return title[len(prefix) :]
    return title


def _no_deinflection(_lemma: str) -> tuple[str, ...]:
    return ()


def _no_inflection_chain(_surface: str, _targets: tuple[str, ...]) -> list[str]:
    return []


@dataclass(frozen=True, slots=True)
class SourceAdapterOptions:
    dictionaries: tuple[str, ...] = ()
    sequence_dictionaries: tuple[str, ...] = ()
    language: str = "jp"
    result_mode: TermResultMode = TermResultMode.GROUP
    deinflected_forms: Callable[[str], tuple[str, ...]] = _no_deinflection
    inflection_chain: Callable[[str, tuple[str, ...]], list[str]] = _no_inflection_chain


class DictionarySourceAdapter:
    """Present any semantic LookupSource through Saitenka's stable dictionary facade."""

    dicts: tuple[object, ...] = ()
    freqs: tuple[object, ...] = ()
    pitches: tuple[object, ...] = ()

    def __init__(self, source: LookupSource, options: SourceAdapterOptions | None = None):
        self.source = source
        self.options = options or SourceAdapterOptions()

    def _deinflected(self, lemma: str) -> tuple[str, ...]:
        return self.options.deinflected_forms(lemma)

    def _result(self, token, extra_terms=(), inflected=None):
        deinflected = self._deinflected(token.lemma)
        candidates = tuple(
            value
            for value in dict.fromkeys(
                (token.lemma, *extra_terms, inflected, token.surface, *deinflected, token.reading)
            )
            if value
        )
        if not candidates:
            return self.source.lookup_terms(TermQuery(""))
        with otel_metrics.instrumented(
            otel_metrics.dict_sql_duration_ms, "dict_sql", emit_span=_emit_sql_span()
        ):
            result = self.source.lookup_terms(
                TermQuery(
                    candidates[0],
                    mode=self.options.result_mode,
                    dictionaries=self.options.dictionaries,
                    primary_reading=token.reading,
                    alternate_forms=candidates[1:],
                )
            )
        termforms = {
            value for value in (*extra_terms, token.lemma, token.surface, *deinflected) if value
        }
        preferred = frozenset(deinflected)

        def rank(entry):
            headword = entry.headwords[0]
            frequencies = [
                item.value
                for item in entry.frequencies
                if isinstance(item.value, int) and item.value > 0
            ]
            return _entry_rank_key(
                headword.term,
                headword.reading,
                token.reading,
                termforms,
                preferred,
                min(frequencies, default=None),
            )

        return replace(result, entries=tuple(sorted(result.entries, key=rank)))

    def entry_for(self, token, inflected=None, *, extra_terms=()):
        result = self._result(token, extra_terms, inflected)
        if not result.entries:
            return self._missing_entry(token, inflected)
        entries = self._matching_entries(result.entries, token, extra_terms)
        first = entries[0]
        headword = first.headwords[0]
        groups = self._groups(entries) if len(entries) > 1 else []
        header = groups[0].headword if groups else furigana(headword.term, headword.reading)
        reading = groups[0].reading if groups else headword.reading
        surface = inflected or token.surface
        inflection_targets = tuple(
            value for value in (token.lemma, headword.term) if value and value != surface
        )
        return Entry(
            headword=header,
            freqs=self._frequency_pills(first),
            defs=self._fused_definitions(entries),
            inflection_chain=self.options.inflection_chain(surface, inflection_targets),
            reading=reading,
            pitches=self._pitches(first),
            groups=groups,
        )

    def _missing_entry(self, token, inflected=None):
        """No dictionary has the word — but the deinflection chain still does.

        The chain is computed from surface→lemma, not from a hit, and it is the one thing that can
        explain an unknown word to a learner ("parlons: present indicative"). Dropping it here is what
        made a second-language profile show a bare "not found" for every inflected form it missed.
        """
        message = (
            "（辞書に見つかりませんでした）"
            if self.options.language in {"jp", "ja"}
            else "(not found in dictionary)"
        )
        surface = inflected or token.surface
        return Entry(
            headword=furigana(token.lemma or token.surface, token.reading),
            defs=[Definition("—", [message])],
            inflection_chain=self.options.inflection_chain(
                surface, tuple(value for value in (token.lemma,) if value and value != surface)
            ),
            reading=token.reading,
        )

    def _matching_entries(self, entries, token, extra_terms=()):
        termforms = {
            value
            for value in (
                *extra_terms,
                token.lemma,
                token.surface,
                *self._deinflected(token.lemma),
            )
            if value
        }
        exact = tuple(
            entry
            for entry in entries
            if any(headword.term in termforms for headword in entry.headwords)
        )
        return exact or entries

    def kanji_for(self, char: str, *, stroke_order: bool = False):
        result = self.source.lookup_kanji(KanjiQuery(char, self.options.dictionaries))
        if not result.entries:
            return None
        entry = result.entries[0]
        content: list = []
        if entry.onyomi:
            content.append({"tag": "div", "content": f"音　{' '.join(entry.onyomi)}"})
        if entry.kunyomi:
            content.append({"tag": "div", "content": f"訓　{' '.join(entry.kunyomi)}"})
        if entry.meanings:
            content.append(
                {
                    "tag": "ol",
                    "content": [{"tag": "li", "content": meaning} for meaning in entry.meanings],
                }
            )
        content.extend(self._kanji_stats(entry))
        return Entry(
            headword=[char],
            tags=[tag.name for tag in entry.tags[:3]],
            freqs=[
                Freq(item.dictionary, str(item.display_value or item.value), FREQ_COLOR)
                for item in entry.frequencies
            ],
            defs=[Definition(entry.source.dictionary if entry.source else "—", content)],
            reading=(entry.kunyomi or entry.onyomi or ("",))[0].split(".")[0],
            headword_font=STROKE_ORDER_FONT if stroke_order else None,
        )

    @staticmethod
    def _kanji_stats(entry):
        values = dict(entry.stats)
        tags = dict(entry.stat_tags)
        grouped: dict[str, list[tuple[int, str, str]]] = {}
        for code, value in values.items():
            tag = tags.get(code)
            grouped.setdefault(tag.category if tag else "", []).append(
                (tag.order if tag else 999, tag.notes or tag.name if tag else code, str(value))
            )
        nodes: list = []
        section_titles = dict(_KANJI_STAT_SECTIONS)
        for category in (*section_titles, *sorted(set(grouped) - set(section_titles))):
            rows = grouped.get(category)
            if not rows:
                continue
            if title := section_titles.get(category, ""):
                nodes.append({"tag": "div", "style": {"fontWeight": "bold"}, "content": title})
            nodes.append(
                {
                    "tag": "table",
                    "content": [
                        {
                            "tag": "tr",
                            "content": [
                                {"tag": "td", "content": label},
                                {"tag": "td", "content": value},
                            ],
                        }
                        for _order, label, value in sorted(rows)
                    ],
                }
            )
        return nodes

    def has_term(self, *forms: str | None) -> bool:
        return any(
            self.source.lookup_terms(
                TermQuery(form, mode=TermResultMode.SIMPLE, dictionaries=self.options.dictionaries)
            ).entries
            for form in forms
            if form
        )

    def terms_exist(self, forms):
        values = tuple(dict.fromkeys(form for form in forms if form))
        if isinstance(self.source, AttestationSource):
            return set(self.source.exact_terms(values, self.options.dictionaries))
        return {
            form
            for form in values
            if any(
                headword.term == form
                for entry in self.source.lookup_terms(
                    TermQuery(
                        form,
                        mode=TermResultMode.SIMPLE,
                        dictionaries=self.options.dictionaries,
                    )
                ).entries
                for headword in entry.headwords
            )
        }

    def cards_for(self, token, *, extra_terms=()):
        result = self._result(token, extra_terms)
        entries = self._matching_entries(result.entries, token, extra_terms)
        cards: list[CardData] = []
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            for headword in entry.headwords:
                identity = (headword.term, headword.reading)
                if identity in seen:
                    continue
                seen.add(identity)
                cards.append(self._card_data(entry, headword))
        return cards

    def _card_data(self, entry, headword):
        definitions = self._card_definitions(entry)
        glosses = tuple(
            gloss for definition in definitions for gloss in _glosses_of(list(definition.content))
        )
        body = "".join(f"<li>{html.escape(gloss)}</li>" for gloss in glosses)
        return CardData(
            headword.term,
            headword.reading,
            f"<ol>{body}</ol>",
            self._sequence(definitions),
            glosses,
        )

    @staticmethod
    def _card_definitions(entry):
        first = next((item for item in entry.definitions if _glosses_of(list(item.content))), None)
        return () if first is None else (first,)

    def _sequence(self, definitions) -> str:
        if not definitions or definitions[0].source is None or definitions[0].sequence < 0:
            return ""
        dictionary = definitions[0].source.dictionary
        return (
            str(definitions[0].sequence) if dictionary in self.options.sequence_dictionaries else ""
        )

    def card_for(self, token, *, extra_terms=()):
        cards = self.cards_for(token, extra_terms=extra_terms)
        return cards[0] if cards else CardData(token.lemma or token.surface, token.reading, "")

    def frequency_field(self, token):
        if isinstance(self.source, SemanticFrequencySource):
            items = self.source.frequencies_for(self._headwords(token), self.options.dictionaries)
        else:
            entries = self._result(token).entries
            items = entries[0].frequencies if entries else ()
        if not items:
            return "", ""
        body = "".join(
            f"<li>{html.escape(item.dictionary)}: {html.escape(str(item.display_value or item.value))}</li>"
            for item in items
        )
        numbers = [int(value) for item in items for value in re.findall(r"\d+", str(item.value))]
        return f'<ul style="text-align:left;margin:0;padding-left:1.1em;">{body}</ul>', (
            str(min(numbers)) if numbers else ""
        )

    def pitch_field(self, token):
        if isinstance(self.source, SemanticPronunciationSource):
            items = self.source.pronunciations_for(
                self._headwords(token), self.options.dictionaries
            )
        else:
            entries = self._result(token).entries
            items = entries[0].pronunciations if entries else ()
        if not items:
            return "", ""
        body = "".join(
            f"<li>{html.escape(item.reading)}: "
            + ", ".join(f"[{position}]" for position in item.pitch_positions)
            + "</li>"
            for item in items
        )
        positions = ", ".join(str(position) for item in items for position in item.pitch_positions)
        return f'<ul style="text-align:left;margin:0;padding-left:1.1em;">{body}</ul>', positions

    def search(self, pattern: str, limit: int = 30):
        if not isinstance(self.source, SearchSource):
            return Entry(
                headword=[pattern],
                defs=[Definition("検索", ["（この情報源はワイルドカード検索に対応していません）"])],
            )
        glob = _to_glob(pattern)
        if not any(character in glob for character in "*?"):
            glob += "*"
        result = self.source.search_terms(SearchQuery(glob, self.options.dictionaries, limit))
        items = self._search_items(result.entries, limit)
        content = (
            [{"tag": "ul", "content": _search_result_nodes(items)}]
            if items
            else ["（一致する語がありません）"]
        )
        return Entry(
            headword=[pattern],
            defs=[Definition(f"検索 “{pattern}” · {len(items)}件", content)],
        )

    @staticmethod
    def _search_items(entries, limit: int) -> list[_SearchHit]:
        items: list[_SearchHit] = []
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            glosses = [
                gloss
                for definition in entry.definitions
                for gloss in _glosses_of(list(definition.content))
            ]
            for headword in entry.headwords:
                identity = (headword.term, headword.reading)
                if identity in seen:
                    continue
                seen.add(identity)
                items.append(
                    _SearchHit(
                        term=headword.term,
                        reading=headword.reading,
                        gloss=glosses[0] if glosses else "",
                    )
                )
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        return items

    def decoded_entry_count(self) -> int:
        counter = getattr(self.source, "decoded_entry_count", None)
        return counter() if counter is not None else 0

    def _headwords(self, token) -> tuple[tuple[str, str], ...]:
        forms = (token.lemma, token.surface, *self._deinflected(token.lemma))
        return tuple(dict.fromkeys((form, token.reading) for form in forms if form))

    def _definition(self, definition):
        source = definition.source.dictionary if definition.source else "—"
        content = _glossary_to_nodes(list(definition.content))
        paths = tuple(dict.fromkeys(collect_img_paths(content)))
        media = (
            self.source.media_for(source, paths)
            if paths and isinstance(self.source, MediaSource)
            else {}
        )
        return Definition(
            source,
            content,
            [tag.name for tag in definition.tags],
            media=media,
        )

    def _fused_definitions(self, entries):
        fused: dict[str, Definition] = {}
        for entry in entries:
            for semantic in entry.definitions:
                definition = self._definition(semantic)
                current = fused.get(definition.dict_name)
                if current is None:
                    fused[definition.dict_name] = definition
                    continue
                current_content = (
                    current.content if isinstance(current.content, list) else [current.content]
                )
                new_content = (
                    definition.content
                    if isinstance(definition.content, list)
                    else [definition.content]
                )
                fused[definition.dict_name] = Definition(
                    definition.dict_name,
                    [*current_content, *new_content],
                    list(dict.fromkeys((*current.tags, *definition.tags))),
                    {**current.media, **definition.media},
                )
        return list(fused.values())

    def _groups(self, entries):
        return [
            EntryGroup(
                furigana(headword.term, headword.reading),
                headword.reading,
                [self._definition(definition) for definition in entry.definitions],
                index,
            )
            for index, entry in enumerate(entries)
            for headword in entry.headwords[:1]
        ]

    @staticmethod
    def _frequency_pills(entry):
        pills = [
            Freq(
                _short_freq_name(item.dictionary), str(item.display_value or item.value), FREQ_COLOR
            )
            for item in entry.frequencies
        ]
        pills.extend(
            Freq(
                item.dictionary,
                item.reading
                + " "
                + ", ".join(f"[{position}]" for position in item.pitch_positions),
                PITCH_COLOR,
            )
            for item in entry.pronunciations
            if item.pitch_positions
        )
        return pills

    @staticmethod
    def _pitches(entry):
        return [
            (
                item.reading,
                tuple(
                    PitchAccent(position, item.devoiced_morae, item.nasal_morae)
                    for position in item.pitch_positions
                    if isinstance(position, int)
                ),
            )
            for item in entry.pronunciations
            if any(isinstance(position, int) for position in item.pitch_positions)
        ]
