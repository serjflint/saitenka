"""Mined-card construction: the note's shape, its field maps, and its `{marker}` templates."""

from __future__ import annotations

from saitenka_card.card import CardData
from saitenka_card.clip import AnimatedClip
from saitenka_card.markers import (
    CATALOG,
    MARKERS,
    Marker,
    MarkerContext,
    build_markers,
    markers_in,
    render_card_format,
)
from saitenka_card.note import (
    CARD_KINDS,
    FRENCH_FIELDS,
    KNOWN_ENTITIES,
    KNOWN_MARKERS,
    LAPIS_FIELDS,
    PRESETS,
    CardContent,
    MineConfig,
    bold_word,
    build_note,
    strip_field_html,
)

__all__ = [
    "CARD_KINDS",
    "CATALOG",
    "FRENCH_FIELDS",
    "KNOWN_ENTITIES",
    "KNOWN_MARKERS",
    "LAPIS_FIELDS",
    "MARKERS",
    "PRESETS",
    "AnimatedClip",
    "CardContent",
    "CardData",
    "Marker",
    "MarkerContext",
    "MineConfig",
    "bold_word",
    "build_markers",
    "build_note",
    "markers_in",
    "render_card_format",
    "strip_field_html",
]
