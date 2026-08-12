from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from saitenka_dict.models import (
        Definition,
        Frequency,
        KanjiEntry,
        Pronunciation,
        SourceTrace,
        Tag,
    )


@dataclass(frozen=True, slots=True)
class TermRecord:
    term: str
    reading: str
    definitions: tuple[Definition, ...]
    source: SourceTrace
    definition_tags: tuple[Tag, ...] = ()
    term_tags: tuple[Tag, ...] = ()
    rules: tuple[str, ...] = ()
    score: int = 0
    sequence: int = -1


@dataclass(frozen=True, slots=True)
class TermSearch:
    forms: tuple[str, ...]
    dictionaries: tuple[str, ...] = ()
    limit: int = 50


class CacheObserver(Protocol):
    def hit(self) -> None: ...

    def miss(self) -> None: ...

    def eviction(self) -> None: ...


class DictionaryStore(Protocol):
    def find_terms(self, search: TermSearch) -> tuple[TermRecord, ...]: ...

    def search_terms(self, search: TermSearch) -> tuple[TermRecord, ...]: ...

    def exact_terms(
        self, forms: tuple[str, ...], dictionaries: tuple[str, ...]
    ) -> frozenset[str]: ...

    def media_for(self, dictionary: str, paths: tuple[str, ...]) -> dict[str, bytes]: ...

    def find_related(
        self, sequences: tuple[int, ...], dictionaries: tuple[str, ...]
    ) -> tuple[TermRecord, ...]: ...

    def find_frequencies(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...]
    ) -> tuple[Frequency, ...]: ...

    def find_pronunciations(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...]
    ) -> tuple[Pronunciation, ...]: ...

    def find_kanji(
        self, characters: tuple[str, ...], dictionaries: tuple[str, ...]
    ) -> tuple[KanjiEntry, ...]: ...
