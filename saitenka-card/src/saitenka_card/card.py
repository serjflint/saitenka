"""What a dictionary lookup hands the card builder."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CardData:
    """The per-word content a mine turns into note fields.

    Produced by the application's dictionary lookup and consumed here, so it lives on the consumer's
    side: the builder is the one that must not change under it, and a lookup is free to be replaced.
    """

    expression: str
    reading: str
    glossary_html: str
    idseq: str = ""
    glosses: tuple[str, ...] = ()  # raw sense strings, for the card preview
