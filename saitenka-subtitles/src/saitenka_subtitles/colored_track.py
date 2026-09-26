"""A colored copy of an authored ASS document, for mpv to render as the track itself.

Each token is wrapped in `\\1c` and the authored color is restored after it, so mpv's own libass
paints the colors on the video clock — the first frame of a cue, after a seek, with no overlay to
race the frame draw. Only the Text field of a colored `Dialogue:` row changes, and only by blocks
`strip_colors` removes again: every consumer that reads the track (`sub-text`, the cue index, the
hit map) sees the authored document.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka_subtitles.ass import (
    UnsupportedAssEvent,
    decode_ass_event,
    parse_ass_event_line,
    parse_ass_styles,
    qualify_prepared_paint,
    rewrite_ass_event,
)
from saitenka_subtitles.document import AnnotatedSubtitleEvent, SubtitleTrackId
from saitenka_subtitles.geometry import PaintQualification

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from saitenka_subtitles.document import TokenAnnotation

GENERATOR_VERSION = 1
#: How much of a copy a reader looks at to find its header; generation refuses to place it later.
HEAD_BYTES = 8192
HEADER = "; saitenka-colored: "
#: Exactly what `rewrite_ass_event` inserts, and nothing an author is refused for writing: a document
#: that already contains one cannot be stripped back to itself, so it is not colored at all.
INJECTED = re.compile(r"\{\\1c&H[0-9A-F]{1,8}&\\2c&H[0-9A-F]{1,8}&\}")
_TEXT_FIELD = 9
_TRACK = SubtitleTrackId("colored")

#: Decoded event text → the tokens to color and each one's 24-bit RGB, or `None` for none.
type EventColors = Callable[[str], tuple[tuple[TokenAnnotation, ...], Mapping[int, int]] | None]


class Verbatim(StrEnum):
    """Why an event was copied without colors. Such an event keeps the live paint path."""

    UNPARSED = "unparsed"
    PAINT_GATE = "paint-gate"
    NO_TOKENS = "no-tokens"
    REFUSED = "rewrite-refused"


@dataclass(frozen=True, slots=True)
class ColoredDocument:
    text: str
    colored: int
    verbatim: Mapping[Verbatim, int]


def _lines(text: str) -> list[str]:
    # Not `splitlines`: libass breaks rows on `\n` only, and a form feed or U+2028 inside an event
    # would otherwise split it and leave its tail uncolored.
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def strip_colors(text: str) -> str:
    """The authored document a copy was generated from, byte for byte."""
    kept = (line for line in _lines(text) if not line.startswith(HEADER))
    return INJECTED.sub("", "".join(kept))


def colored_origin(text: str) -> str | None:
    """The origin a copy records in its header, or `None` for a document that is not a copy."""
    for line in _lines(text)[:64]:
        if line.startswith(HEADER):
            line = line.rstrip("\r\n")
            version, _, origin = line.removeprefix(HEADER).partition(" ")
            return origin if version == str(GENERATOR_VERSION) and origin else None
    return None


def _rgb_to_bgr(rgb: int) -> int:
    bgr = ((rgb & 0xFF) << 16) | (rgb & 0x00FF00) | ((rgb >> 16) & 0xFF)
    # The rewriter reserves 0 for "no color"; black is one step off it.
    return bgr or 0x010101


def _text_offset(line: str) -> int:
    index = line.index(":") + 1
    for _ in range(_TEXT_FIELD):
        index = line.index(",", index) + 1
    return index


class _Events:
    def __init__(self, source: str, colors: EventColors) -> None:
        self._catalog = parse_ass_styles(source.encode("utf-8"))
        self._colors = colors
        self.reasons: Counter[Verbatim] = Counter()
        self.colored = 0
        self._order = 0

    def row(self, line: str) -> str:
        """`line` with its tokens colored, or unchanged with the reason counted."""
        self._order += 1
        try:
            event = parse_ass_event_line(line.lstrip(), _TRACK, self._order - 1)
            offset = _text_offset(line)
            gate = qualify_prepared_paint(event, self._catalog)
        except (UnsupportedAssEvent, ValueError):
            return self._verbatim(line, Verbatim.UNPARSED)
        if line[offset:] != event.raw_text:
            return self._verbatim(line, Verbatim.UNPARSED)
        if gate is not PaintQualification.STATIC:
            return self._verbatim(line, Verbatim.PAINT_GATE)
        decoded = decode_ass_event(event)
        try:
            found = self._colors(decoded.text)
        except ValueError:
            return self._verbatim(line, Verbatim.REFUSED)
        if not found or not found[0]:
            return self._verbatim(line, Verbatim.NO_TOKENS)
        annotations, rgb = found
        try:
            rewrite = rewrite_ass_event(
                AnnotatedSubtitleEvent(decoded, annotations),
                {token.token_index: _rgb_to_bgr(rgb[token.token_index]) for token in annotations},
                self._catalog,
            )
        except (UnsupportedAssEvent, ValueError, KeyError):
            return self._verbatim(line, Verbatim.REFUSED)
        self.colored += 1
        return line[:offset] + rewrite.event.raw_text

    def _verbatim(self, line: str, reason: Verbatim) -> str:
        self.reasons[reason] += 1
        return line


def _refuse_uncarriable(source: str, origin: str) -> None:
    if not origin or "\n" in origin or "\r" in origin:
        raise UnsupportedAssEvent("the origin cannot be recorded on one header line")
    if INJECTED.search(source) or any(line.startswith(HEADER) for line in source.splitlines()):
        raise UnsupportedAssEvent("the document already carries what stripping removes")


def color_document(source: str, origin: str, colors: EventColors) -> ColoredDocument:
    """Color every event `colors` tokenizes; copy the rest of `source` verbatim.

    `colors` maps an event's decoded text to its token annotations and their RGB. Raises
    `UnsupportedAssEvent` for a document the copy cannot carry losslessly — the caller then has no
    copy, not a damaged one.
    """
    _refuse_uncarriable(source, origin)
    events = _Events(source, colors)
    out: list[str] = []
    in_events = False
    header_written = False
    for raw_line in _lines(source):
        line = raw_line.rstrip("\r\n")
        ending = raw_line[len(line) :]
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = stripped.casefold() == "[events]"
            out.append(raw_line)
            if stripped.casefold() == "[script info]" and not header_written:
                out.append(f"{HEADER}{GENERATOR_VERSION} {origin}{ending or chr(10)}")
                header_written = True
            continue
        if in_events and line.lstrip().startswith("Dialogue:"):
            out.append(events.row(line) + ending)
            continue
        out.append(raw_line)
    if not header_written:
        raise UnsupportedAssEvent("the document has no [Script Info] section to record its origin")
    text = "".join(out)
    if strip_colors(text) != source:
        raise UnsupportedAssEvent("the colored copy does not strip back to its origin")
    head = text.encode("utf-8")[:HEAD_BYTES].decode("utf-8", errors="replace")
    if colored_origin(head) != origin:
        raise UnsupportedAssEvent("the origin header is too far in to be found")
    return ColoredDocument(text, events.colored, dict(events.reasons))
