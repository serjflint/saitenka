"""Subtitle language roles — the two states the primary track can show, as named constants.

A tiny leaf (no ``app`` imports) so any module can name a language role without an import cycle. The
string VALUES ("jp"/"en") stay stable — they're persisted (session stats, backlog) and matched
against mpv track tags — while call sites read by ROLE. This is the seam a future release makes
configurable: which concrete language is main vs second becomes a setting sourced here.
"""

from __future__ import annotations

from typing import Literal

Language = Literal["jp", "en"]

MAIN_LANG: Language = "jp"  # target language: tokenized, annotated, colored, hoverable
SECOND_LANG: Language = "en"  # known/translation language: drawn plain, non-interactive
