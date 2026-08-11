from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

StructuredContent = (
    str | int | float | bool | None | list["StructuredContent"] | dict[str, "StructuredContent"]
)


class TermResultMode(StrEnum):
    SIMPLE = "simple"
    SPLIT = "split"
    GROUP = "group"
    TERM = "term"
    MERGE = "merge"


class Capability(StrEnum):
    TERM_LOOKUP = "term-lookup"
    KANJI_LOOKUP = "kanji-lookup"
    SEARCH = "search"
    IMPORT = "import"
    MEDIA = "media"
    ATTESTATION = "attestation"


@dataclass(frozen=True, slots=True)
class SourceTrace:
    dictionary: str
    dictionary_index: int = 0
    record_id: int | None = None


@dataclass(frozen=True, slots=True)
class MatchSource:
    original_text: str
    transformed_text: str
    deinflected_text: str
    matched_length: int
    match_type: str = "exact"
    match_source: str = "term"
    is_primary: bool = True


@dataclass(frozen=True, slots=True)
class Tag:
    name: str
    category: str = ""
    notes: str = ""
    order: int = 0
    score: int = 0
    source: SourceTrace | None = None


@dataclass(frozen=True, slots=True)
class Inflection:
    source: str
    reasons: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Headword:
    term: str
    reading: str = ""
    tags: tuple[Tag, ...] = ()
    sources: tuple[MatchSource, ...] = ()


@dataclass(frozen=True, slots=True)
class Definition:
    content: tuple[StructuredContent, ...]
    tags: tuple[Tag, ...] = ()
    source: SourceTrace | None = None
    score: int = 0


@dataclass(frozen=True, slots=True)
class Frequency:
    dictionary: str
    value: int | float | str
    display_value: str | None = None
    reading: str | None = None


@dataclass(frozen=True, slots=True)
class Pronunciation:
    dictionary: str
    reading: str
    pitch_positions: tuple[int | str, ...] = ()
    ipa: str | None = None
    nasal_morae: tuple[int, ...] = ()
    devoiced_morae: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class TermEntry:
    headwords: tuple[Headword, ...]
    definitions: tuple[Definition, ...]
    inflections: tuple[Inflection, ...] = ()
    frequencies: tuple[Frequency, ...] = ()
    pronunciations: tuple[Pronunciation, ...] = ()
    sequence: int = -1
    score: int = 0
    is_primary: bool = True
    matched_text_length: int = 0


@dataclass(frozen=True, slots=True)
class TermQuery:
    text: str
    mode: TermResultMode = TermResultMode.GROUP
    dictionaries: tuple[str, ...] = ()
    primary_reading: str | None = None
    max_results: int = 50
    inflections: tuple[Inflection, ...] = ()
    alternate_forms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TermResult:
    entries: tuple[TermEntry, ...]
    original_text_length: int
    matched_text_length: int


@dataclass(frozen=True, slots=True)
class SearchQuery:
    pattern: str
    dictionaries: tuple[str, ...] = ()
    max_results: int = 30


@dataclass(frozen=True, slots=True)
class KanjiEntry:
    character: str
    onyomi: tuple[str, ...] = ()
    kunyomi: tuple[str, ...] = ()
    meanings: tuple[str, ...] = ()
    tags: tuple[Tag, ...] = ()
    stats: tuple[tuple[str, StructuredContent], ...] = ()
    frequencies: tuple[Frequency, ...] = ()
    source: SourceTrace | None = None
    stat_tags: tuple[tuple[str, Tag], ...] = ()


@dataclass(frozen=True, slots=True)
class KanjiQuery:
    text: str
    dictionaries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KanjiResult:
    entries: tuple[KanjiEntry, ...]


@dataclass(frozen=True, slots=True)
class DictionaryInfo:
    title: str
    revision: str = ""
    format: int = 3
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    metadata: tuple[tuple[str, Any], ...] = ()
