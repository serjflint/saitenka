"""Segment a subtitle line into tokens (surface + lemma + reading + POS) with char offsets.

fugashi + unidic-lite gives word boundaries (for per-word hit-testing) and, crucially, the **lemma**
(dictionary form) — so an inflected surface like 習わ resolves to 習う for lookup, deinflection for
free. The katakana reading is folded to hiragana for furigana.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# fugashi/MeCab wraps a C extension that has NOT declared free-threaded safety, so on a free-threaded
# build we run with PYTHON_GIL=0 (see examples/mpv_reader._ensure_free_threaded). Tokenising is
# main-thread-only in the app, but this lock makes it safe even if called concurrently.
_TAG_LOCK = threading.Lock()

_KANA_PUNCT = frozenset({0x30A0, 0x30FB})  # ゠ double-hyphen, ・ nakaguro — separators, not kana

CONTENT_POS = {"名詞", "動詞", "形容詞", "副詞", "形状詞", "連体詞", "感動詞"}
SKIP_POS = {"補助記号", "記号", "空白"}  # symbol / punctuation / whitespace — never worth a tooltip
AUX_POS = {"助動詞"}  # trailing tokens glued to the verb/adj surface for the inflection chain


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
        # Kana LETTERS only (hiragana/katakana incl. ー prolonged mark). Katakana punctuation — ・
        # (0x30FB nakaguro) and ゠ (0x30A0 double-hyphen) — sit inside the block but are separators,
        # not kana, so a name like ジョン・スミス isn't mistaken for a kana-only word and dropped from
        # N+1 candidacy.
        return bool(self.surface) and all(
            0x3040 <= ord(c) <= 0x30FF and ord(c) not in _KANA_PUNCT for c in self.surface
        )


def query_token(query: str) -> Token | None:
    """A single Token spanning the WHOLE query — a cross-reference / search link (``?query=X``) targets X
    as ONE exact term, so it is looked up whole and NEVER tokenized. Tokenizing + taking the first token
    capped それにしては → それ (the longest-match/segmentation picks the short prefix), so every compound
    link resolved to その instead of its own entry. ``None`` for a blank query."""
    q = query.strip()
    if not q:
        return None
    return Token(surface=q, lemma=q, reading="", pos="", start=0, end=len(q))


def kata_to_hira(s: str) -> str:
    out = []
    for ch in s or "":
        o = ord(ch)
        out.append(chr(o - 0x60) if 0x30A1 <= o <= 0x30F6 else ch)
    return "".join(out)


def _has_kanji(s: str) -> bool:
    # last range = supplementary planes (Ext B–H + Compat Supplement): astral surrogate-pair kanji
    return any(
        0x3400 <= ord(c) <= 0x9FFF  # CJK Unified + Ext A
        or 0xF900 <= ord(c) <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x20000 <= ord(c) <= 0x3FFFF
        for c in s
    )


def _all_hira(s: str) -> bool:
    return bool(s) and all(0x3040 <= ord(c) <= 0x309F for c in s)


def _match_furigana_run(tokens: list[Token], start: int, reading: str) -> tuple[int, bool]:
    """From `start`, accumulate a hiragana run and check whether it matches `reading` exactly at
    some prefix boundary. Returns ``(end_index, matched)``."""
    n = len(tokens)
    acc, k = "", start
    while k < n and _all_hira(tokens[k].surface) and len(acc) < len(reading):
        acc += tokens[k].surface
        k += 1
        if acc == reading:
            return k, True
    return k, False


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
            k, matched = _match_furigana_run(tokens, j, reading)
            out.extend(tokens[i:j])
            i = k if (matched and len(reading) >= 2) else j
            continue
        out.append(tokens[i])
        i += 1
    return out


@lru_cache(maxsize=1)
def _tagger():
    import fugashi

    return fugashi.Tagger()  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[unresolved-attribute]  # no stubs


_HEAD_POS = {"動詞", "形容詞", "形状詞"}  # can start a conjugation chain
_TE = {"て", "で"}  # connective that licenses an auxiliary verb after it
_AUX_HEAD = {"動詞", "形容詞"}  # an auxiliary after て/で is a verb (いる/しまう/くる/…)
# OR an adjective (ほしい/よい): ～てほしい, ～てよかった


def _scan_conjugation_tail(tokens: list[Token], i: int) -> int:
    """From ``tokens[i+1]``, extend the conjugation tail (auxiliaries chained via て/で after the
    head at `i`). Returns the tail's end index (exclusive)."""
    n = len(tokens)
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
    return j


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
        j = _scan_conjugation_tail(tokens, i)
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


def inflected_in(tokens: list[Token], index: int) -> str:
    """Token surface + trailing auxiliary tokens (助動詞), so the chain deinflects the full word
    (習わ + ぬ → 習わぬ); the tokenizer splits inflected verbs from their auxiliaries. Free function so
    a prefetch lookahead can inflect a *future* line's tokens, not just the on-screen tokens."""
    s = tokens[index].surface
    j = index + 1
    while j < len(tokens) and tokens[j].pos in AUX_POS:
        s += tokens[j].surface
        j += 1
    return s


_MAX_MERGE_SPAN = 4  # forward longest-match window; unidic over-splits compounds/set phrases by 2-3
_PREFIX_POS = {
    "接頭辞",
    "接頭詞",
}  # honorific/attached prefix (お・ご) — fold into the word it precedes


def _scan_from(
    tokens: list[Token], start: int, index: int, has_term: Callable[[str], bool]
) -> list[tuple[int, int, int, str]]:
    """Dictionary terms that begin at ``start`` and reach the hovered ``index``, as
    ``(ntokens, start, end, surface)``. Stops at punctuation/space or a line break (per-line offsets
    restart, so a next-start below the previous end means a different source line — not adjacent)."""
    surface, prev_end = tokens[start].surface, tokens[start].end
    out: list[tuple[int, int, int, str]] = []
    for k in range(start + 1, min(len(tokens), start + _MAX_MERGE_SPAN)):
        nxt = tokens[k]
        if nxt.pos in SKIP_POS or not nxt.surface.strip() or nxt.start < prev_end:
            break
        surface += nxt.surface
        prev_end = nxt.end
        if k >= index and has_term(surface):  # the span must cover the hovered token
            out.append((k + 1 - start, start, k + 1, surface))
    return out


def phrase_terms(
    tokens: list[Token], index: int, has_term: Callable[[str], bool]
) -> tuple[list[str], int, int] | None:
    """Multi-token dictionary terms covering ``tokens[index]``, longest first. unidic over-splits set
    phrases and compounds (数+ある → 数ある) and detaches honorific prefixes (お+休み → お休み); before a
    tooltip, probe whether the hovered token plus its neighbours concatenate to terms the dictionaries
    actually have, so the tooltip can stack them above the bare word — Yomitan-style longest-match-first.
    The scan runs forward from the hovered token, and also from an immediately-preceding prefix (お/ご) so
    hovering the content word 休み — not the tiny 接頭辞 お — still finds お休み. Returns
    ``(terms, start, end)`` (``terms`` longest-first, ``[start, end)`` the token span of the longest) or
    ``None`` when no multi-token term covers the hovered word.

    ``has_term`` is the dict-set membership seam, kept a callable so this stays dict-free and the whole
    search strategy is swappable (a future char-level line scanner drops in here). These surfaces are
    fed to :meth:`DictionarySet.entry_for` as extra lookup terms; the dictionary supplies each reading."""
    starts = [index]
    prev = tokens[index - 1] if index > 0 else None
    if prev is not None and prev.pos in _PREFIX_POS and prev.end == tokens[index].start:
        starts.append(index - 1)
    hits = [h for s in starts for h in _scan_from(tokens, s, index, has_term)]
    if not hits:
        return None
    hits.sort(key=lambda h: -h[0])  # longest (most tokens) first
    seen: set[str] = set()
    terms: list[str] = []
    for *_, surface in hits:
        if surface not in seen:
            seen.add(surface)
            terms.append(surface)
    return terms, hits[0][1], hits[0][2]


_COMPOUND_INFLECTABLE = {
    "動詞",
    "形容詞",
}  # a tail whose dict form (lemma) differs from its surface
_COMPOUND_MAX_TOKENS = 4  # span cap (tokens) — unidic over-splits lexicalized compounds by 2-3
_COMPOUND_MAX_CHARS = 16  # safety char bound; admits katakana tech compounds (アプリケーション…)


def _compound_form(span: list[Token]) -> str:
    """The dictionary headword a span resolves to: the joined surfaces with the tail deinflected to its
    dict form (走り+出した → 走り出す) when the tail conjugates (動詞/形容詞), else the plain surface join
    (応急+処置 → 応急処置). Mirrors anki_miner's orthBase-deinflected candidate."""
    prefix = "".join(t.surface for t in span[:-1])
    tail = span[-1]
    return prefix + (tail.lemma if tail.pos in _COMPOUND_INFLECTABLE else tail.surface)


def _compound_spans(tokens: list[Token]) -> dict[tuple[int, int], str]:
    """Every mergeable ``(start, end)`` span → its candidate headword (:func:`_compound_form`). A span
    starts at a content token and extends only over content tokens that are exactly adjacent on the same
    line — a non-content token (助詞/助動詞/punct) or an offset gap (a new source line restarts offsets)
    ends it — up to the token- and char-caps. That content-only rule is the 格/係助詞 boundary guard the
    tooltip's :func:`phrase_terms` and :func:`merge_inflected` already respect."""
    n = len(tokens)
    spans: dict[tuple[int, int], str] = {}
    for i in range(n):
        if not tokens[i].is_content:
            continue
        prev_end, chars = tokens[i].end, len(tokens[i].surface)
        for j in range(i + 1, min(n, i + _COMPOUND_MAX_TOKENS)):
            nxt = tokens[j]
            if not nxt.is_content or nxt.start != prev_end:
                break
            chars += len(nxt.surface)
            if chars > _COMPOUND_MAX_CHARS:
                break
            prev_end = nxt.end
            spans[(i, j)] = _compound_form(tokens[i : j + 1])
    return spans


def _compound_token(span: list[Token], headword: str) -> Token:
    """One merged token: SURFACE/READING are the concatenations (display + reading-affinity ranking),
    LEMMA is the attested ``headword`` (drives lookup). An inflectable tail lends its verb/adjective POS
    (so downstream inflection/mining treats the whole as one conjugating word); a nominal span keeps the
    head's POS.

    Concatenating the component readings is safe because a compound with a non-compositional reading
    (jukujikun 今日/大人, whole-compound rendaku) is lexicalized as ONE unidic token and never reaches
    this pass; a compound unidic DOES over-split carries each fragment's contextual reading (rendaku
    already applied: 消費+税 → しょうひ+ぜい), so the join reconstructs it. The reading is secondary
    regardless — lookup keys on the lemma, and the tooltip/TTS prefer the matched dict entry's reading."""
    head, tail = span[0], span[-1]
    inflectable = tail.pos in _COMPOUND_INFLECTABLE
    return Token(
        surface="".join(t.surface for t in span),
        lemma=headword,
        reading="".join(t.reading for t in span),
        pos=tail.pos if inflectable else head.pos,
        start=head.start,
        end=tail.end,
        pos2="" if inflectable else head.pos2,
    )


def merge_dict_compounds(
    tokens: list[Token], exists: Callable[[Sequence[str]], set[str]]
) -> list[Token]:
    """Merge adjacent tokens into ONE token wherever their joined span is an exact dictionary headword —
    Yomitan's longest-match at the token level, so a lexicalized compound unidic over-splits (応急+処置 →
    応急処置, 満員+電車 → 満員電車, 走り+出した → 走り出した) becomes a single hover / hit-test / color / mine
    unit instead of fragments. Runs AFTER :func:`merge_inflected` (the conjugation tail is already glued
    on, so 走り出した is available as [走り, 出した]). Greedy left-to-right, longest span first; guardrails
    and per-token shape live in :func:`_compound_spans` / :func:`_compound_token`.

    ``exists`` is the batch dict-set existence seam (:meth:`DictionarySet.terms_exist`), a callable so
    this stays dict-free and swappable, exactly like :func:`phrase_terms`' ``has_term``."""
    n = len(tokens)
    if n < 2:
        return tokens
    spans = _compound_spans(
        tokens
    )  # one batched probe covers the whole line, not a lookup per span
    if not spans:
        return tokens
    hits = exists(sorted(set(spans.values())))
    out: list[Token] = []
    i = 0
    while i < n:
        widest = range(min(n - 1, i + _COMPOUND_MAX_TOKENS - 1), i, -1)  # longest span first
        end = next((j for j in widest if spans.get((i, j)) in hits), i)
        if end > i:
            out.append(_compound_token(tokens[i : end + 1], spans[(i, end)]))
            i = end + 1
        else:
            out.append(tokens[i])
            i += 1
    return out


def tokenize(line: str, *, strip_furigana: bool = True, merge: bool = True) -> list[Token]:
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
