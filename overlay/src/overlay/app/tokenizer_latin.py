"""Latin-script tokenizer strategy — the second engine (#254 W1), covering fr/es/de/it/pt/… .

Yomitan's own model for European languages: there is no morphological analyzer (no MeCab equivalent).
Text is scanned into word runs on Unicode letter boundaries, and inflection is handled downstream by a
dictionary-form lookup with a transform-based deinflector (the GPL ``deinflect`` add-on's French rules),
not by a per-token lemma from the segmenter. So this tokenizer's job is only **segmentation + content
classification + offsets** — ``lemma`` is the surface (the lookup layer lowercases and deinflects).

Elision (``l'homme``, ``d'accord``, ``qu'il``) is split on the apostrophe: the clitic and the content
word become separate tokens. That mis-splits the rare fused word (``aujourd'hui``); accepted for v1 —
the alternative is a dictionary-driven scan (Yomitan's approach) that this token model doesn't do yet.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from overlay.app.tokenize import Token

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

WORD = "WORD"
PUNCT = "PUNCT"
SPACE = "SPACE"

# Apostrophe variants that mark French elision — both the straight and the typographic form.
_APOSTROPHES = frozenset("'’")


def _is_word_char(ch: str) -> bool:
    """A letter or a combining mark (so precomposed *and* decomposed accents stay inside the word).
    Digits and underscore are deliberately NOT word chars — a bare number isn't a lookup candidate."""
    return ch.isalpha() or unicodedata.combining(ch) != 0


def _classify(ch: str) -> str:
    if _is_word_char(ch):
        return WORD
    return SPACE if ch.isspace() else PUNCT


class LatinTokenizer:
    """Whitespace/punctuation segmentation for Latin-script languages. Implements the ``Tokenizer``
    protocol so a profile swap reroutes every tokenization call site with no code change."""

    name = "latin"

    def tokenize(
        self, line: str, *, strip_furigana: bool = True, merge: bool = True
    ) -> list[Token]:
        """Segment ``line`` into WORD / PUNCT / SPACE tokens with char offsets. ``strip_furigana`` and
        ``merge`` are part of the protocol (Japanese-only knobs) and ignored here."""
        del strip_furigana, merge  # protocol signature; no Latin analogue
        tokens: list[Token] = []
        i, n = 0, len(line)
        while i < n:
            kind = _classify(line[i])
            j = i + 1
            # An apostrophe ends a WORD run (elision split) but is itself PUNCT, so it can't extend a run.
            while j < n and _classify(line[j]) == kind and line[j] not in _APOSTROPHES:
                j += 1
            surface = line[i:j]
            tokens.append(
                Token(surface=surface, lemma=surface, reading="", pos=kind, start=i, end=j)
            )
            i = j
        return tokens

    def is_content(self, token: Token) -> bool:
        """A word worth annotating/mining. Every WORD run qualifies; a stray single-letter elision clitic
        (``l`` from ``l'homme``) simply won't resolve in the dictionary, so it's harmless noise, not a
        classification gate here."""
        return token.pos == WORD

    def is_skippable(self, token: Token) -> bool:
        """Punctuation / whitespace / blank — never worth a tooltip or hit-test."""
        return token.pos in {PUNCT, SPACE} or not token.surface.strip()

    def query_token(self, query: str) -> Token | None:
        """The whole query as one WORD token — a cross-reference link targets its text as one exact term."""
        q = query.strip()
        if not q:
            return None
        return Token(surface=q, lemma=q, reading="", pos=WORD, start=0, end=len(q))

    def inflected_in(self, tokens: list[Token], index: int) -> str:
        """The surface as-is — Latin has no trailing auxiliary tokens glued to the stem (the JP inflection
        chain), so the inflected form a mine records is just the token's own surface."""
        return tokens[index].surface

    def phrase_terms(
        self, tokens: list[Token], index: int, has_term: Callable[[str], bool]
    ) -> tuple[list[str], int, int] | None:
        """No multi-token phrase probing for Latin v1 — deinflection handles single-word inflection and
        there's no compound-merge grammar to reconstruct. Returns ``None`` (single-token lookup)."""
        del tokens, index, has_term  # protocol signature; no Latin phrase probe
        return None

    def merge_dict_compounds(
        self, tokens: list[Token], exists: Callable[[Sequence[str]], set[str]]
    ) -> list[Token]:
        """Identity — no dictionary-driven compound merge (a JP segmentation fixup) for Latin."""
        del exists  # protocol signature; no Latin compound merge
        return tokens
