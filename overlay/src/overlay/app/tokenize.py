"""Segment a subtitle line into tokens (surface + lemma + reading + POS) with char offsets.

fugashi + unidic-lite gives word boundaries (for per-word hit-testing) and, crucially, the **lemma**
(dictionary form) — so an inflected surface like 習わ resolves to 習う for lookup, deinflection for
free. The katakana reading is folded to hiragana for furigana.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache

# fugashi/MeCab wraps a C extension that has NOT declared free-threaded safety, so on a free-threaded
# build we run with PYTHON_GIL=0 (see examples/mpv_reader._ensure_free_threaded). Tokenising is
# main-thread-only in the app, but this lock makes it safe even if called concurrently.
_TAG_LOCK = threading.Lock()

CONTENT_POS = {"名詞", "動詞", "形容詞", "副詞", "形状詞", "連体詞", "感動詞"}


@dataclass(frozen=True, slots=True)
class Token:
    surface: str
    lemma: str
    reading: str  # hiragana
    pos: str  # unidic pos1 (名詞/動詞/助詞…)
    start: int  # char index into the line
    end: int
    pos2: str = ""  # unidic pos2 (固有名詞 for proper nouns, …)

    @property
    def is_content(self) -> bool:
        return self.pos in CONTENT_POS

    @property
    def is_proper_noun(self) -> bool:
        return self.pos == "名詞" and self.pos2 == "固有名詞"

    @property
    def is_kana_only(self) -> bool:
        return all(0x3040 <= ord(c) <= 0x30FF for c in self.surface)


def kata_to_hira(s: str) -> str:
    out = []
    for ch in s or "":
        o = ord(ch)
        out.append(chr(o - 0x60) if 0x30A1 <= o <= 0x30F6 else ch)
    return "".join(out)


def _has_kanji(s: str) -> bool:
    return any(0x3400 <= ord(c) <= 0x9FFF or 0xF900 <= ord(c) <= 0xFAFF for c in s)


def _all_hira(s: str) -> bool:
    return bool(s) and all(0x3040 <= ord(c) <= 0x309F for c in s)


def strip_inline_furigana(tokens: list[Token]) -> list[Token]:
    """Drop Amazon-style inline furigana: a kanji run immediately followed by hiragana that spells its
    reading (龍門光英りゅうもんみつひで → 龍門光英). Matches the reading as an exact token-boundary prefix
    of the following hiragana run, leaving trailing particles (…は) intact."""
    out: list[Token] = []
    i, n = 0, len(tokens)
    while i < n:
        if _has_kanji(tokens[i].surface):
            j = i
            while j < n and _has_kanji(tokens[j].surface):
                j += 1
            reading = "".join(t.reading for t in tokens[i:j])
            acc, k = "", j
            while k < n and _all_hira(tokens[k].surface) and len(acc) < len(reading):
                acc += tokens[k].surface
                k += 1
                if acc == reading:
                    break
            out.extend(tokens[i:j])
            i = k if (acc == reading and len(reading) >= 2) else j
            continue
        out.append(tokens[i])
        i += 1
    return out


@lru_cache(maxsize=1)
def _tagger():
    import fugashi

    return fugashi.Tagger()  # pyright: ignore[reportAttributeAccessIssue]  # no stubs


_HEAD_POS = {"動詞", "形容詞", "形状詞"}  # can start a conjugation chain
_TE = {"て", "で"}  # connective that licenses an auxiliary verb after it
_AUX_HEAD = {"動詞", "形容詞"}  # an auxiliary after て/で is a verb (いる/しまう/くる/…)
# OR an adjective (ほしい/よい): ～てほしい, ～てよかった


def merge_inflected(tokens: list[Token]) -> list[Token]:
    """Merge a verb/adjective with its whole conjugation tail into ONE token, so hovering selects the
    full inflected word like Yomitan (習わ+ぬ → 習わぬ, 聞こえ+て+た → 聞こえてた, 食べ+て+いる → 食べている,
    食べ+て+ほしい → 食べてほしい) rather than a bare MeCab morpheme. Stops at real word boundaries
    (格/係助詞 を・と・は・も), so ``預けた`` doesn't swallow ``としても``. The head verb's lemma drives the
    lookup; the merged surface drives the inflection chain."""
    out: list[Token] = []
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        if t.pos not in _HEAD_POS:
            out.append(t)
            i += 1
            continue
        j, prev_te = i + 1, False
        while j < n:
            nx = tokens[j]
            if nx.pos == "助動詞":
                prev_te = nx.surface in _TE
            elif nx.pos == "助詞" and nx.pos2 == "接続助詞" and nx.surface in _TE:
                prev_te = True
            elif nx.pos in _AUX_HEAD and nx.pos2 == "非自立可能" and prev_te:
                prev_te = False  # auxiliary verb いる/しまう/… or adjective ほしい after て/で
            else:
                break
            j += 1
        if j > i + 1:
            g = tokens[i:j]
            out.append(
                Token(
                    "".join(x.surface for x in g),
                    t.lemma,
                    "".join(x.reading for x in g),
                    t.pos,
                    t.start,
                    g[-1].end,
                    t.pos2,
                )
            )
            i = j
        else:
            out.append(t)
            i += 1
    return out


def tokenize(line: str, strip_furigana: bool = True, merge: bool = True) -> list[Token]:
    tokens: list[Token] = []
    idx = 0
    # Hold _TAG_LOCK across the full parse AND feature attribute reads: fugashi's C extension has
    # not declared free-threading safety, and w.feature may access MeCab-internal state that is
    # only safe from one thread at a time on a free-threaded (no-GIL) build.
    with _TAG_LOCK:
        parsed = list(_tagger()(line))
        raw: list[tuple[str, str, str, str, str]] = []
        for w in parsed:
            surf = w.surface
            f = w.feature  # read inside the lock (free-threaded safety)
            reading = kata_to_hira(getattr(f, "kana", None) or surf)
            lemma = getattr(f, "lemma", None) or surf
            # unidic lemma can carry a "-reading" suffix (e.g. read-ヨム); strip it
            lemma = lemma.split("-", 1)[0]
            pos = getattr(f, "pos1", None) or ""
            pos2 = getattr(f, "pos2", None) or ""
            raw.append((surf, lemma, reading, pos, pos2))
    for surf, lemma, reading, pos, pos2 in raw:
        start = line.find(surf, idx)
        if start < 0:
            start = idx
        end = start + len(surf)
        idx = end
        tokens.append(Token(surf, lemma, reading, pos, start, end, pos2))
    if strip_furigana:
        tokens = strip_inline_furigana(tokens)
    return merge_inflected(tokens) if merge else tokens
