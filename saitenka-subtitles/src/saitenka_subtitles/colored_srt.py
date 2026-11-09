"""Insert token colors into SubRip while leaving mpv in charge of converted-track styling."""

from __future__ import annotations

import re
from dataclasses import dataclass

from saitenka_subtitles.colored_track import ColoredDocument, EventColors, Verbatim
from saitenka_subtitles.parsers import parse_srt
from saitenka_subtitles.subrip import dialogue_row

_STYLE = re.compile(r"</?[biu]>", re.IGNORECASE)
_BREAK = re.compile(r"(\r?\n[ \t]*\r?\n)")


@dataclass(frozen=True, slots=True)
class ColoredSrt:
    document: ColoredDocument
    painted_rows: tuple[str, ...]


def _plain(body: str) -> tuple[str, list[int]] | None:
    offsets: list[int] = []
    chars: list[str] = []
    cursor = 0
    while cursor < len(body):
        tag = _STYLE.match(body, cursor)
        if tag:
            cursor = tag.end()
            continue
        char = body[cursor]
        if char == "\r" and body[cursor : cursor + 2] == "\r\n":
            cursor += 1
            continue
        if char in "<>{}\\\r\t":
            return None
        chars.append(char)
        offsets.append(cursor)
        cursor += 1
    text = "".join(chars)
    # A font tag before leading spaces changes libavcodec's whitespace trimming.
    if any(line != line.strip() for line in text.split("\n")):
        return None
    return text, offsets


def _paint(body: str, text: str, offsets: list[int], colors: EventColors) -> str | None:
    found = colors(text)
    if not found or not found[0]:
        return None
    tokens, rgb = found
    end = 0
    spans: list[tuple[int, int, int]] = []
    for token in sorted(tokens, key=lambda token: token.text_start):
        start, stop = token.text_start, token.text_end
        if not end <= start < stop <= len(text) or "\n" in text[start:stop]:
            raise ValueError("unplaceable SubRip token")
        color = rgb[token.token_index]
        if not 0 <= color <= 0xFFFFFF:
            raise ValueError("invalid SubRip token color")
        spans.append((offsets[start], offsets[stop - 1] + 1, color))
        end = stop
    for start, stop, color in reversed(spans):
        body = f'{body[:start]}<font color="#{color:06X}">{body[start:stop]}</font>{body[stop:]}'
    return body


def color_document(source: str, colors: EventColors) -> ColoredSrt:
    """Color supported cues; retain every other block verbatim, including co-timed cues."""
    blocks = _BREAK.split(source)
    reasons: dict[Verbatim, int] = {}
    painted: list[str] = []
    for index in range(0, len(blocks), 2):
        block = blocks[index]
        header = block.split("\n", 2)
        if len(header) != 3 or not header[0].lstrip("\ufeff").strip().isdigit():
            continue
        cues = parse_srt(block)
        body = header[2].rstrip("\r\n")
        plain = _plain(body)
        reason = Verbatim.REFUSED
        if len(cues) == 1 and plain is not None and plain[0] == cues[0].text:
            try:
                rewritten = _paint(body, *plain, colors)
                row = dialogue_row(cues[0], rewritten) if rewritten is not None else None
                if rewritten is not None and row is not None:
                    blocks[index] = (
                        block[: len(block) - len(header[2])] + rewritten + header[2][len(body) :]
                    )
                    painted.append(row)
                    continue
                reason = Verbatim.NO_TOKENS
            except (ValueError, KeyError):
                pass
        reasons[reason] = reasons.get(reason, 0) + 1
    return ColoredSrt(ColoredDocument("".join(blocks), len(painted), reasons), tuple(painted))
