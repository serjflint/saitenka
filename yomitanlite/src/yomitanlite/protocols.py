from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from yomitanlite.models import (
        Capability,
        DictionaryInfo,
        Frequency,
        KanjiQuery,
        KanjiResult,
        Pronunciation,
        SearchQuery,
        TermQuery,
        TermResult,
    )


class LookupSource(Protocol):
    @property
    def capabilities(self) -> frozenset[Capability]: ...

    def lookup_terms(self, query: TermQuery) -> TermResult: ...

    def lookup_kanji(self, query: KanjiQuery) -> KanjiResult: ...


@runtime_checkable
class SearchSource(Protocol):
    def search_terms(self, query: SearchQuery) -> TermResult: ...


@runtime_checkable
class MediaSource(Protocol):
    def media_for(self, dictionary: str, paths: tuple[str, ...]) -> dict[str, bytes]: ...


@runtime_checkable
class AttestationSource(Protocol):
    def exact_terms(
        self, forms: tuple[str, ...], dictionaries: tuple[str, ...] = ()
    ) -> frozenset[str]: ...


@runtime_checkable
class FrequencySource(Protocol):
    def frequencies_for(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...] = ()
    ) -> tuple[Frequency, ...]: ...


@runtime_checkable
class PronunciationSource(Protocol):
    def pronunciations_for(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...] = ()
    ) -> tuple[Pronunciation, ...]: ...


class DictionaryAdmin(Protocol):
    def import_dictionary(self, archive: str | Path) -> DictionaryInfo: ...

    def list_dictionaries(self) -> tuple[DictionaryInfo, ...]: ...

    def remove_dictionary(self, title: str) -> bool: ...
