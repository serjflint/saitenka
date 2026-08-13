"""Dictionary-entry values consumed by the panel renderer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.model import RGBA, PitchAccent

type SCNode = str | dict | list


@dataclass
class Freq:
    name: str
    value: str
    color: RGBA


@dataclass
class Definition:
    dict_name: str
    content: SCNode  # structured-content node
    tags: list[str] = field(default_factory=list)  # defTags: ★, priority form, …
    # {img path: image bytes}, preloaded from the DB at Entry-build so the render thread never queries
    # SQLite (#283). Empty on a default install → inline img renders as ▢.
    media: dict[str, bytes] = field(default_factory=dict)


@dataclass
class EntryGroup:
    """One Yomitan-style stacked entry: a distinct (term, reading) with its own ruby'd headword and
    per-dictionary definitions, drawn as its own block with its own ⊕ mine button. ``card_index``
    indexes ``DictionarySet.cards_for(token)`` so the button mines exactly this entry."""

    headword: object  # structured-content node (ruby'd)
    reading: str
    defs: list[Definition] = field(default_factory=list)
    card_index: int = 0


@dataclass
class Entry:
    headword: object  # structured-content node (ruby'd)
    tags: list[str] = field(default_factory=list)
    freqs: list[Freq] = field(default_factory=list)
    reading_label: tuple[str, str] | None = None  # (dict_name, text)
    defs: list[Definition] = field(default_factory=list)
    inflection_chain: list[str] = field(default_factory=list)  # 🧩 -て « -いる « -た
    reading: str = ""  # dictionary-form kana reading (for TTS: 習う → ならう, not ならわ)
    # Distinct pitch accents as (reading, PitchAccents) — drawn as compact graphs in a header-area row;
    # the purple text pill in the freq row stays as the compact fallback.
    pitches: list[tuple[str, tuple[PitchAccent, ...]]] = field(default_factory=list)
    # Yomitan-style stacked entries: when a headword has ≥2 distinct readings (退く = のく / しりぞく),
    # one EntryGroup per reading, each rendered as its own block with its own ⊕. Empty for the common
    # single-entry case — the fused header path above is unchanged (goldens preserved).
    groups: list[EntryGroup] = field(default_factory=list)
    # Force a vendored font file for the big headword glyph (kanji panel → stroke-order font); None =
    # the normal coverage chain. Purely visual — set by the caller from the tooltip toggle.
    headword_font: str | None = None


def _hex(s: str) -> RGBA:
    from saitenka.render.sc_adapter import _parse_color

    return _parse_color(s, (90, 122, 160, 255))


def _load_defs(items: list) -> list[Definition]:
    return [Definition(d["dict"], d["content"], tags=d.get("tags", [])) for d in items]


def load_entry(path: str | Path) -> Entry:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Entry(
        headword=data["headword"],
        tags=[t["text"] for t in data.get("tags", [])],
        freqs=[Freq(f["name"], f["value"], _hex(f["color"])) for f in data.get("freqs", [])],
        reading_label=(
            tuple(data["reading_label"].values()) if data.get("reading_label") else None
        ),
        defs=_load_defs(data.get("defs", [])),
        reading=data.get("reading", ""),
        # Yomitan-style stacked entries (退く = のく / しりぞく): one block per reading, each with its
        # own ruby'd headword + ⊕. Absent in single-entry fixtures → the fused header path.
        groups=[
            EntryGroup(
                headword=g["headword"],
                reading=g.get("reading", ""),
                defs=_load_defs(g.get("defs", [])),
                card_index=g.get("card_index", i),
            )
            for i, g in enumerate(data.get("groups", []))
        ],
    )
