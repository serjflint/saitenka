"""Tokenizer strategy seam — per-language morphology routed through the active profile.

The ProfileController owns a :class:`Tokenizer` instance rather than calling the ``japanese.py``
module functions directly, so a profile switch (#254) can swap the whole segmentation/inflection
stack in one place. Today only Japanese exists: :class:`UnidicTokenizer` wraps the
fugashi/unidic-lite pipeline. It **delegates to the free functions in** ``japanese.py`` — a
one-directional dependency (no import cycle) that leaves the Japanese behaviour and its goldens
untouched; this module only adds the swappable OO seam on top.

Selection is by name (a profile's ``tokenizer`` value) against a registry, so one strategy can serve a
whole language family (a future ``latin`` strategy for fr/es/de/…) and a new language can point at an
existing strategy with no code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka_tokenize import japanese as _jp

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka_tokenize.japanese import Token


class Tokenizer(Protocol):
    """Language-dependent operations used by profile, tooltip, and mining code. A profile owns one;
    swapping it reroutes tokenization, content/skip classification, phrase probing and inflection
    without touching any call site — so a non-Japanese strategy defines its own content-ness."""

    name: str

    def tokenize(
        self, line: str, *, strip_furigana: bool = True, merge: bool = True
    ) -> list[Token]: ...
    # NOT complements: a token can be neither content nor skippable (e.g. a grammatical particle —
    # not worth mining, but still hit-testable/annotatable). Test both predicates, never `not` one.
    def is_content(self, token: Token) -> bool: ...
    def is_skippable(self, token: Token) -> bool: ...
    def query_token(self, query: str) -> Token | None: ...
    def inflected_in(self, tokens: list[Token], index: int) -> str: ...
    def phrase_terms(
        self, tokens: list[Token], index: int, has_term: Callable[[str], bool]
    ) -> tuple[list[str], int, int] | None: ...
    def merge_dict_compounds(
        self, tokens: list[Token], exists: Callable[[Sequence[str]], set[str]]
    ) -> list[Token]: ...


class UnidicTokenizer:
    """Japanese, via fugashi + unidic-lite — a thin OO wrapper over ``japanese.py``'s functions."""

    name = "unidic"

    def tokenize(
        self, line: str, *, strip_furigana: bool = True, merge: bool = True
    ) -> list[Token]:
        return _jp.tokenize(line, strip_furigana=strip_furigana, merge=merge)

    def is_content(self, token: Token) -> bool:
        """A content word worth annotating/mining — the unidic POS whitelist (名詞/動詞/形容詞/…)."""
        return token.pos in _jp.CONTENT_POS

    def is_skippable(self, token: Token) -> bool:
        """Not worth a tooltip/hit-test — unidic symbol/punct/whitespace POS, or a blank surface."""
        return token.pos in _jp.SKIP_POS or not token.surface.strip()

    def query_token(self, query: str) -> Token | None:
        return _jp.query_token(query)

    def inflected_in(self, tokens: list[Token], index: int) -> str:
        return _jp.inflected_in(tokens, index)

    def phrase_terms(
        self, tokens: list[Token], index: int, has_term: Callable[[str], bool]
    ) -> tuple[list[str], int, int] | None:
        return _jp.phrase_terms(tokens=tokens, index=index, has_term=has_term)

    def merge_dict_compounds(
        self, tokens: list[Token], exists: Callable[[Sequence[str]], set[str]]
    ) -> list[Token]:
        return _jp.merge_dict_compounds(tokens, exists)


DEFAULT_TOKENIZER = UnidicTokenizer.name
_FACTORIES: dict[str, Callable[[], Tokenizer]] = {UnidicTokenizer.name: UnidicTokenizer}


def register_tokenizer(name: str, factory: Callable[[], Tokenizer]) -> None:
    """Register a tokenizer-strategy factory under ``name`` (a profile's ``tokenizer`` value)."""
    _FACTORIES[name] = factory


def get_tokenizer(name: str = DEFAULT_TOKENIZER) -> Tokenizer:
    """The tokenizer strategy for ``name``. Unknown name → ``ValueError`` listing the registered ones."""
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise ValueError(f"unknown tokenizer {name!r}; registered: {sorted(_FACTORIES)}") from None
    return factory()


def _register_builtin_latin() -> None:
    """Register the Latin-script strategy (#254 W1) at import time. The ``tokenizer_latin`` import only
    pulls in the ``Token`` dataclass (already loaded via ``_jp``), not fugashi — so this stays cheap."""
    from saitenka_tokenize.latin import LatinTokenizer

    _FACTORIES.setdefault(LatinTokenizer.name, LatinTokenizer)


_register_builtin_latin()
