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


class EmptyDictionaryStore:
    """A store that knows no words.

    "No dictionaries configured" is a real state, not an error — a fresh install has it, and so does a
    profile whose titles all failed to resolve. Modelling it as a store rather than as ``None`` keeps
    every consumer on one code path instead of carrying a second, barely-exercised branch for the
    empty case.
    """

    def find_terms(self, _search: TermSearch) -> tuple[TermRecord, ...]:
        return ()

    def search_terms(self, _search: TermSearch) -> tuple[TermRecord, ...]:
        return ()

    def exact_terms(
        self, _forms: tuple[str, ...], _dictionaries: tuple[str, ...]
    ) -> frozenset[str]:
        return frozenset()

    def media_for(self, _dictionary: str, _paths: tuple[str, ...]) -> dict[str, bytes]:
        return {}

    def find_related(
        self, _sequences: tuple[int, ...], _dictionaries: tuple[str, ...]
    ) -> tuple[TermRecord, ...]:
        return ()

    def frequent_terms(
        self, _limit: int, _dictionaries: tuple[str, ...] = ()
    ) -> tuple[tuple[str, str], ...]:
        return ()

    def find_frequencies(
        self, _headwords: tuple[tuple[str, str], ...], _dictionaries: tuple[str, ...]
    ) -> tuple[Frequency, ...]:
        return ()

    def find_pronunciations(
        self, _headwords: tuple[tuple[str, str], ...], _dictionaries: tuple[str, ...]
    ) -> tuple[Pronunciation, ...]:
        return ()

    def find_kanji(
        self, _characters: tuple[str, ...], _dictionaries: tuple[str, ...]
    ) -> tuple[KanjiEntry, ...]:
        return ()

    def decoded_entry_count(self) -> int:
        return 0


class DictionaryStore(Protocol):
    """The read surface a lookup source needs.

    Positional-only: an implementation is free to name its parameters (a null store prefixes them to
    say it ignores them), and every caller passes positionally anyway.
    """

    def find_terms(self, search: TermSearch, /) -> tuple[TermRecord, ...]: ...

    def search_terms(self, search: TermSearch, /) -> tuple[TermRecord, ...]: ...

    def exact_terms(
        self, forms: tuple[str, ...], dictionaries: tuple[str, ...], /
    ) -> frozenset[str]: ...

    def media_for(self, dictionary: str, paths: tuple[str, ...], /) -> dict[str, bytes]: ...

    def find_related(
        self, sequences: tuple[int, ...], dictionaries: tuple[str, ...], /
    ) -> tuple[TermRecord, ...]: ...

    def find_frequencies(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...], /
    ) -> tuple[Frequency, ...]: ...

    def find_pronunciations(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...], /
    ) -> tuple[Pronunciation, ...]: ...

    def find_kanji(
        self, characters: tuple[str, ...], dictionaries: tuple[str, ...], /
    ) -> tuple[KanjiEntry, ...]: ...
