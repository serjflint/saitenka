"""Turn a token into a panel :class:`Entry` via JMdict (jamdict): readings, POS, English glosses.

Lookup is on the **lemma** first (dictionary form), then the surface, then the reading — so inflected
words resolve. The result is adapted into the same ``Entry`` the 読む golden uses, so ``render_panel``
draws a genuine Yomitan-like tooltip. (A monolingual / structured-content dictionary can be swapped in
later behind this same adapter — the walker is already there.)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from overlay.app.tokenize import Token
from overlay.panel import Definition, Entry

POS_EN = {
    "名詞": "noun",
    "動詞": "verb",
    "形容詞": "adjective",
    "形状詞": "adj-na",
    "副詞": "adverb",
    "助詞": "particle",
    "助動詞": "auxiliary",
    "連体詞": "pre-noun",
    "接続詞": "conjunction",
    "感動詞": "interjection",
    "接頭辞": "prefix",
    "接尾辞": "suffix",
    "記号": "symbol",
    "補助記号": "punctuation",
    "代名詞": "pronoun",
}


def _is_kana(ch: str) -> bool:
    return 0x3040 <= ord(ch) <= 0x30FF


def furigana(surface: str, reading: str):
    """Structured-content nodes for `surface` with `reading` as ruby, aligning okurigana.

    e.g. 読む / よむ → [ruby(読, よ), "む"]; 小僧 / こぞう → [ruby(小僧, こぞう)].
    """
    if not surface:
        return [reading]
    if not reading or surface == reading:
        return [surface]
    s, r = surface, reading
    tail = ""
    while s and r and s[-1] == r[-1] and _is_kana(s[-1]):
        tail = s[-1] + tail
        s, r = s[:-1], r[:-1]
    head = ""
    while s and r and s[0] == r[0] and _is_kana(s[0]):
        head += s[0]
        s, r = s[1:], r[1:]
    nodes: list = []
    if head:
        nodes.append(head)
    if s:
        nodes.append({"tag": "ruby", "content": [s, {"tag": "rt", "content": r}]} if r else s)
    if tail:
        nodes.append(tail)
    return nodes or [surface]


@lru_cache(maxsize=1)
def _jam():
    from jamdict import Jamdict

    return Jamdict()


def _lookup(word: str):
    # words-only: skip the JMnedict (names) + KanjiDic searches — ~1ms vs 14-70ms for common kanji
    return _jam().lookup(word, lookup_chars=False, lookup_ne=False)


def _tags(pos_en: str, sense_pos: list[str]) -> list[str]:
    tags = [pos_en]
    joined = " ".join(sense_pos).lower()
    if "transitive" in joined:
        tags.append("transitive")
    elif "intransitive" in joined:
        tags.append("intransitive")
    return tags[:2]


def _pick(entries, reading: str, surface: str, lemma: str):
    """Choose the best entry: reading match dominates (本/ほん not 元/もと), then kanji match."""

    def score(e) -> int:
        kana = [k.text for k in e.kana_forms]
        kanji = [k.text for k in e.kanji_forms]
        s = 0
        if reading and reading in kana:
            s += 4
        if lemma in kanji or surface in kanji:
            s += 2
        if kanji and kanji[0] in (lemma, surface):  # target is the primary kanji form
            s += 1
        return s

    return max(entries, key=score)


@lru_cache(maxsize=512)
def lookup_entry(surface: str, lemma: str, reading: str, pos: str) -> Entry:
    pos_en = POS_EN.get(pos, pos or "word")
    try:
        result = _lookup(lemma) if lemma else None
        entries = result.entries if result else []
        if not entries and surface != lemma:
            entries = (_lookup(surface).entries) or []
    except Exception:
        entries = []

    if not entries:
        # no dictionary hit (often a particle) — minimal tooltip
        head = furigana(surface, reading)
        return Entry(
            headword=head, tags=[pos_en], defs=[Definition("—", ["（辞書に見つかりませんでした）"])]
        )

    e = _pick(entries, reading, surface, lemma)
    kanji = e.kanji_forms[0].text if e.kanji_forms else surface
    kana = e.kana_forms[0].text if e.kana_forms else reading
    head = furigana(kanji, kana)

    sense_pos_first = list(e.senses[0].pos) if e.senses else []
    tags = _tags(pos_en, sense_pos_first)

    li = []
    for s in e.senses[:8]:
        glosses = "; ".join(str(g) for g in s.gloss)
        if glosses:
            li.append({"tag": "li", "content": glosses})
    content = {"tag": "ol", "content": li} if li else ["（語義なし）"]
    return Entry(headword=head, tags=tags, defs=[Definition("JMdict", content)])


def entry_for(token: Token) -> Entry:
    return lookup_entry(token.surface, token.lemma, token.reading, token.pos)


@dataclass
class CardData:
    expression: str
    reading: str
    glossary_html: str
    idseq: str = ""
    glosses: tuple[str, ...] = ()  # raw sense strings, for the card preview


@lru_cache(maxsize=512)
def card_data(surface: str, lemma: str, reading: str) -> CardData:
    """Fields for a mined Anki card: dictionary expression/reading, glossary HTML, JMdict id."""
    try:
        entries = (_lookup(lemma).entries if lemma else []) or (
            _lookup(surface).entries if surface != lemma else []
        )
    except Exception:
        entries = []
    if not entries:
        return CardData(expression=lemma or surface, reading=reading, glossary_html="")
    e = _pick(entries, reading, surface, lemma)
    expression = e.kanji_forms[0].text if e.kanji_forms else (lemma or surface)
    kana = e.kana_forms[0].text if e.kana_forms else reading
    glosses = []
    for s in e.senses:
        gl = "; ".join(str(g) for g in s.gloss)
        if gl:
            glosses.append(gl)
    glossary = f"<ol>{''.join(f'<li>{g}</li>' for g in glosses)}</ol>" if glosses else ""
    return CardData(expression, kana, glossary, str(getattr(e, "idseq", "") or ""), tuple(glosses))


def card_for(token: Token) -> CardData:
    return card_data(token.surface, token.lemma, token.reading)
