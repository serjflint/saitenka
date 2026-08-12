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


def glossary_to_nodes(glossary: list) -> list:
    """Normalize Yomitan glossary item envelopes into renderer nodes."""
    wrap = len(glossary) > 1
    nodes: list = []
    for item in glossary:
        node: object
        if isinstance(item, str):
            node = item
        elif isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "structured-content":
                node = item.get("content")
            elif item_type == "text":
                node = item.get("text", "")
            elif item_type == "image":
                node = {"tag": "img", "path": item.get("path", "")}
            else:
                node = item
        else:
            continue
        nodes.append({"tag": "div", "content": node} if wrap else node)
    return nodes


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
