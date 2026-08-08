"""Local stub for fugashi (Cython MeCab wrapper — ships no `py.typed`, and upstream adding one is
unlikely). Covers ONLY the surface saitenka touches in overlay/app/tokenize.py: `Tagger()` and, per
parsed node, `.surface` (str) and `.feature` (UniDic feature object whose fields we read via `getattr`,
so `Any` is correct — not every UniDic build carries every field). Keep this minimal: a wider stub would
type-check usage we don't have against a shape that varies by dictionary. See #216."""

from collections.abc import Iterator
from typing import Any

class Node:
    surface: str
    feature: Any  # UniDic feature record; fields (kana/lemma/pos1/pos2) read via getattr → Any

class Tagger:
    def __init__(self, arg: str = ...) -> None: ...
    def __call__(self, text: str) -> Iterator[Node]: ...
    def parse(self, text: str) -> str: ...

class GenericTagger(Tagger): ...
