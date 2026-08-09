"""Tokenizer strategy seam — the per-language morphology operations the reader routes through.

The Reader owns a :class:`Tokenizer` instance rather than calling the ``tokenize.py`` module functions
directly, so a profile switch (#254) can swap the whole segmentation/inflection stack in one place. Today
only Japanese exists: :class:`UnidicTokenizer` wraps the fugashi/unidic-lite pipeline. It **delegates to
the free functions in** ``tokenize.py`` — a one-directional dependency (no import cycle) that leaves the
Japanese behaviour and its goldens untouched; this module only adds the swappable OO seam on top.

Selection is by name (a profile's ``tokenizer`` value) against a registry, so one strategy can serve a
whole language family (a future ``latin`` strategy for fr/es/de/…) and a new language can point at an
existing strategy with no code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from overlay.app import tokenize as _jp

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from overlay.app.tokenize import Token


class Tokenizer(Protocol):
    """The language-dependent operations the reader/tooltip/mining route through. A profile owns one;
    swapping it reroutes tokenization, content/skip classification, phrase probing and inflection
    without touching any call site — so a non-Japanese strategy defines its own content-ness."""

    name: str

    def tokenize(
        self, line: str, *, strip_furigana: bool = True, merge: bool = True
    ) -> list[Token]: ...
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
    """Japanese, via fugashi + unidic-lite — a thin OO wrapper over ``tokenize.py``'s functions."""

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
