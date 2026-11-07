"""Explicit Kiku scene grouping, independent of Anki transport."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka_card.note import MineConfig

SCENE_FIELDS = ("Sentence", "SentenceAudio", "Picture", "MiscInfo")
OPTIONAL_FIELDS = ("SentenceFurigana", "SentenceTranslation")
_TEMPLATES = {
    "Sentence": "{sentence}",
    "SentenceAudio": "{sentence-audio}",
    "Picture": "{screenshot}",
    "MiscInfo": "{misc}",
}
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def supports_kiku(config: MineConfig) -> bool:
    if config.merge_adapter != "kiku" or config.model != "Kiku":
        return False
    if config.card_format:
        return all(config.card_format.get(name) == marker for name, marker in _TEMPLATES.items())
    return all(
        config.fields.get(logical) == real
        for logical, real in zip(
            ("sentence", "audio", "picture", "misc"), SCENE_FIELDS, strict=True
        )
    )


class _Groups(HTMLParser):
    def __init__(self, value: str, *, picture: bool = False) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.roots: list[str | None] = []
        self.picture = picture
        self.outside_text = False
        self.feed(value)
        self.close()
        if self.stack:
            raise ValueError("unclosed Kiku scene markup")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        group = values.get("data-group-id")
        if "data-group-id" in values and not group:
            raise ValueError("empty Kiku group ID")
        if group is not None and (
            self.stack or not re.fullmatch(r"[1-9][0-9]{0,15}", group) or int(group) > 2**53 - 1
        ):
            raise ValueError("unsupported Kiku group ID")
        if not self.stack:
            if self.picture and tag != "img":
                raise ValueError("Kiku pictures must be image elements")
            if group is not None and tag not in {"span", "img"}:
                raise ValueError("unsupported Kiku group element")
            self.roots.append(group)
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("unbalanced Kiku scene markup")

    def handle_data(self, data: str) -> None:
        if not self.stack and data.strip():
            self.outside_text = True
            if self.picture:
                raise ValueError("unsupported Kiku picture text")

    def handle_entityref(self, name: str) -> None:
        self.handle_data("&" + name + ";")

    def handle_charref(self, name: str) -> None:
        self.handle_data("&#" + name + ";")


def scene_ids(fields: dict[str, str]) -> tuple[str, ...]:
    """Reject ambiguous grouping instead of guessing which sentence owns media."""
    if not all(name in fields for name in SCENE_FIELDS):
        raise ValueError("Kiku scene fields are missing")
    expected: tuple[str, ...] | None = None
    for name in (*SCENE_FIELDS, *(name for name in OPTIONAL_FIELDS if name in fields)):
        parsed = _Groups(fields[name], picture=name == "Picture")
        ids = tuple(group for group in parsed.roots if group is not None)
        if ids and (None in parsed.roots or parsed.outside_text or len(set(ids)) != len(ids)):
            raise ValueError("mixed or repeated Kiku scene groups")
        if name in OPTIONAL_FIELDS and not fields[name].strip():
            continue
        if (
            name == "Picture"
            and not ids
            and (len(parsed.roots) > 1 or (fields[name].strip() and not parsed.roots))
        ):
            raise ValueError("ungrouped Kiku pictures are ambiguous")
        if expected is None:
            expected = ids
        elif ids != expected:
            raise ValueError("Kiku scene groups do not align")
    return expected or ()


def merge_scene(
    fields: dict[str, str], scene: dict[str, str], note_id: int, group_id: str
) -> dict[str, str]:
    ids = scene_ids(fields)
    if group_id in ids:
        return {}
    if group_id == str(note_id):
        raise ValueError("Kiku scene identity collides with retained note")
    result = {}
    for name in (*SCENE_FIELDS, *(name for name in OPTIONAL_FIELDS if name in fields)):
        old = fields[name]
        if name == "SentenceFurigana" and not old.strip():
            old = fields["Sentence"]
        elif name == "SentenceTranslation" and not old.strip() and ids:
            old = "".join(_group(name, "", identity) for identity in ids)
        if not ids:
            old = _group(name, old, str(note_id))
        result[name] = old + _group(name, scene.get(name, ""), group_id)
    return result


def _group(name: str, value: str, group_id: str) -> str:
    if name == "Picture":
        if not value.strip():
            return f'<img data-group-id="{group_id}">'
        return re.sub(r"<img\b", f'<img data-group-id="{group_id}"', value, flags=re.IGNORECASE)
    return f'<span data-group-id="{group_id}">{value}</span>'
