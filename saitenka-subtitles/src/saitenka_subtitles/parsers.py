"""Pure adapters from common subtitle formats to Saitenka cues."""

from __future__ import annotations

import re
from pathlib import PurePath

import pysubs2

from saitenka_subtitles.model import Cue

_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>\n]*>")
_FORMATS = {"ass": "ass", "srt": "srt", "ssa": "ssa", "vtt": "vtt"}


def _parse(content: str, format_: str) -> list[pysubs2.SSAEvent]:
    try:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        return list(pysubs2.SSAFile.from_string(normalized, format_=format_))
    except (KeyError, TypeError, ValueError):
        return []


def _cue(event: pysubs2.SSAEvent, text: str) -> Cue | None:
    clean = text.strip()
    if not clean:
        return None
    return Cue(event.start / 1000, event.end / 1000, clean)


def parse_srt(content: str) -> list[Cue]:
    """Parse SRT text into cues, normalizing its markup and line breaks."""
    return [cue for event in _parse(content, "srt") if (cue := _cue(event, event.plaintext))]


def parse_ass(content: str) -> list[Cue]:
    """Parse ASS or SSA text into cues while preserving ``\\N`` line breaks."""
    return [
        cue
        for event in _parse(content, "ass")
        if (cue := _cue(event, _HTML_TAG.sub("", _ASS_OVERRIDE.sub("", event.text))))
    ]


def _parse_format(content: str, format_: str) -> list[Cue]:
    if format_ in {"ass", "ssa"}:
        return parse_ass(content)
    if format_ == "srt":
        return parse_srt(content)
    return [cue for event in _parse(content, format_) if (cue := _cue(event, event.plaintext))]


def parse_cues(content: str, filename: str) -> list[Cue]:
    """Parse using the filename hint, falling back across supported formats."""
    extension = PurePath(filename).suffix.lower().lstrip(".")
    cues = _parse_format(content, _FORMATS[extension]) if extension in _FORMATS else []
    if not cues:
        candidates = (_parse_format(content, format_) for format_ in ("ass", "srt", "vtt"))
        cues = max(candidates, key=len)
    cues.sort(key=lambda cue: cue.start)
    return cues
