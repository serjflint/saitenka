from __future__ import annotations

import re
from dataclasses import dataclass

from saitenka.sc.walk import _text_of

FREQ_COLOR = (74, 158, 92, 255)
PITCH_COLOR = (126, 96, 168, 255)
KANJI_STAT_SECTIONS = (
    ("misc", "Statistics"),
    ("class", "Classifications"),
    ("code", "Codepoints"),
    ("index", "Dictionary Indices"),
)
DEINFLECT_FORM_CAP = 24
JP_LANGS = frozenset({"jp", "ja"})


def to_glob(pattern: str) -> str:
    return pattern.replace("＊", "*").replace("？", "?")


def reading_affinity(dict_reading: str, context_reading: str) -> int:
    if not dict_reading or not context_reading:
        return 0
    if dict_reading == context_reading:
        return len(dict_reading) + 1
    length = 0
    for left, right in zip(dict_reading, context_reading, strict=False):
        if left != right:
            break
        length += 1
    return length


def glosses_of(glossary: list) -> list[str]:
    out: list[str] = []
    for item in glossary:
        text = (
            item
            if isinstance(item, str)
            else (
                _text_of(item.get("content"))
                if isinstance(item, dict) and item.get("type") == "structured-content"
                else item.get("text", "")
                if isinstance(item, dict)
                else ""
            )
        )
        text = re.sub(r"\s+", " ", text or "").strip()
        if text:
            out.append(text)
    return out


@dataclass(frozen=True, slots=True)
class SearchHit:
    term: str
    reading: str
    gloss: str


def search_result_nodes(items: list[SearchHit]) -> list:
    nodes: list = []
    for item in items:
        content: list = [{"tag": "a", "href": f"?query={item.term}", "content": item.term}]
        if item.reading and item.reading != item.term:
            content.append(f"【{item.reading}】")
        if item.gloss:
            content.append(
                {
                    "tag": "span",
                    "style": {"color": "#6a6a6a"},
                    "content": f" — {item.gloss}",
                }
            )
        nodes.append({"tag": "li", "content": content})
    return nodes
