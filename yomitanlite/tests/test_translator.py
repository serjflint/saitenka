from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

import pytest
from yomitanlite.store import TermRecord, TermSearch

from yomitanlite import (
    Definition,
    Headword,
    KanjiEntry,
    KanjiQuery,
    MatchSource,
    SearchQuery,
    SourceTrace,
    TermQuery,
    TermResultMode,
    Translator,
)


@dataclass
class FakeStore:
    records: tuple[TermRecord, ...]
    kanji: tuple[KanjiEntry, ...] = ()
    queries: list[tuple[str, ...]] = field(default_factory=list)

    def find_terms(self, search: TermSearch):
        self.queries.append(search.forms)
        return tuple(
            r for r in self.records if r.term in search.forms or r.reading in search.forms
        )[: search.limit]

    def search_terms(self, search: TermSearch):
        return tuple(
            record
            for record in self.records
            if fnmatchcase(record.term, search.forms[0])
            or fnmatchcase(record.reading, search.forms[0])
        )[: search.limit]

    def exact_terms(self, forms, _dictionaries):
        return frozenset(record.term for record in self.records if record.term in forms)

    def media_for(self, _dictionary, _paths):
        return {}

    def find_related(self, sequences, _dictionaries):
        return tuple(r for r in self.records if r.sequence in sequences)

    def find_frequencies(self, _headwords, _dictionaries):
        return ()

    def find_pronunciations(self, _headwords, _dictionaries):
        return ()

    def find_kanji(self, characters, _dictionaries):
        return tuple(k for k in self.kanji if k.character in characters)


def record(
    term: str, reading: str, dictionary: str, sequence: int = -1, score: int = 0
) -> TermRecord:
    source = SourceTrace(dictionary)
    return TermRecord(
        term,
        reading,
        (Definition((dictionary,), source=source, score=score),),
        source,
        score=score,
        sequence=sequence,
    )


@pytest.mark.parametrize("mode", list(TermResultMode))
def test_every_yomitan_result_mode_is_supported(mode):
    result = Translator(FakeStore((record("食べる", "たべる", "A"),))).lookup_terms(
        TermQuery("食べる", mode=mode)
    )
    assert result.entries[0].headwords == (
        Headword(
            "食べる",
            "たべる",
            sources=(MatchSource("食べる", "食べる", "食べる", 3),),
        ),
    )


def test_group_coalesces_same_headword_but_split_preserves_rows():
    store = FakeStore((record("打ち込む", "うちこむ", "A"), record("打ち込む", "うちこむ", "B")))
    translator = Translator(store)

    split = translator.lookup_terms(TermQuery("打ち込む", mode=TermResultMode.SPLIT))
    grouped = translator.lookup_terms(TermQuery("打ち込む", mode=TermResultMode.GROUP))

    assert len(split.entries) == 2
    assert len(grouped.entries) == 1
    assert tuple(d.content[0] for d in grouped.entries[0].definitions) == ("A", "B")


def test_entries_and_merged_definitions_are_sorted_by_score():
    store = FakeStore(
        (
            record("打つ", "うつ", "low", sequence=3, score=1),
            record("打つ", "ぶつ", "high", sequence=3, score=10),
        )
    )

    result = Translator(store).lookup_terms(TermQuery("打つ", mode=TermResultMode.TERM))

    assert [definition.content[0] for definition in result.entries[0].definitions] == [
        "high",
        "low",
    ]


def test_longest_prefix_reports_consumed_text():
    store = FakeStore((record("食べる", "たべる", "A"),))

    result = Translator(store).lookup_terms(TermQuery("食べるもの"))

    assert result.original_text_length == 5
    assert result.matched_text_length == 3
    assert store.queries == [("食べるもの",), ("食べるも",), ("食べる",)]


def test_only_entries_at_the_longest_matching_prefix_are_returned():
    store = FakeStore(
        (
            record("打ち込む", "うちこむ", "A"),
            record("打つ", "うつ", "A"),
            record("打", "だ", "A"),
        )
    )

    result = Translator(store).lookup_terms(TermQuery("打ち込む", mode=TermResultMode.GROUP))

    assert [entry.headwords[0].term for entry in result.entries] == ["打ち込む"]
    assert result.matched_text_length == 4
    assert [entry.matched_text_length for entry in result.entries] == [4]


def test_kanji_lookup_deduplicates_input_in_order():
    store = FakeStore((), (KanjiEntry("漢", meanings=("Chinese",)), KanjiEntry("字")))

    result = Translator(store).lookup_kanji(KanjiQuery("漢字漢"))

    assert tuple(entry.character for entry in result.entries) == ("漢", "字")


def test_search_and_attestation_are_distinct_capabilities():
    translator = Translator(FakeStore((record("読む", "よむ", "A"),)))

    search = translator.search_terms(SearchQuery("読*"))

    assert search.entries[0].headwords[0].term == "読む"
    assert translator.exact_terms(("読む", "よむ")) == frozenset({"読む"})
