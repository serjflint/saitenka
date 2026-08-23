r"""The ASS event libavcodec makes of a SubRip cue, predicted ahead of mpv reporting it.

A converted track has no document to read ahead in: its events exist only as the rows mpv reports
for the cue currently on screen, so every cue is a cache miss and the lookahead window is empty. This
module fills that in by doing libavcodec's `srtdec` conversion ourselves, from the `.srt` on disk.

**A wrong prediction cannot produce a wrong box.** The geometry cache key is derived from the event
rows, so a row that does not match the one mpv reports on arrival simply misses the cache and the cue
is rebuilt from mpv's own rows exactly as it is today. That is what makes predicting safe at all —
the verification is structural rather than a check someone has to remember to write.

What the prediction can waste is a render, so this declines rather than guesses. `srtdec` does things
no one would design on purpose — ``a < b > c`` becomes ``a {\b1} c`` because ` b ` parses as a tag,
a second ``{\an}`` is dropped, an unknown tag is deleted, ``&amp;`` is left alone — and a cue holding
any construct outside the well-behaved set is left to the runtime instead.

Every rule below was read off real output: `ffmpeg -c:s ass` for the conversion, and mpv's own
`sub-text/ass-full` for the row it builds around it. Where the two differ, mpv wins — it truncates
centiseconds where ffmpeg's muxer rounds them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from saitenka.subtitles.model import Cue

#: Every `<…>` in the text must match this, or the cue is declined. `srtdec` treats anything between
#: angle brackets as a tag, including a stray comparison, so "looks like a tag" is not a safe test —
#: "is one of the four we reproduce" is.
_TAG = re.compile(r"<(?P<close>/?)(?P<name>[A-Za-z][A-Za-z0-9]*)(?P<rest>[^>]*)>")
#: Only a six-digit hex colour. `srtdec` also accepts the CSS colour names, from a table this would
#: have to copy and keep true; a named colour declines instead.
_FONT_COLOUR = re.compile(r'^\s+color\s*=\s*"?#(?P<hex>[0-9A-Fa-f]{6})"?\s*$')
_STYLE_TAGS = {"i": "i", "b": "b", "u": "u"}
_POSITION = re.compile(r"\{\\an[1-9]\}")


def _tag_ass(match: re.Match[str]) -> str | None:
    """One SubRip tag as its ASS override, or `None` when it is not one this reproduces.

    Deliberately stateless, because `srtdec` is: `</b>` with no `<b>` before it emits `{\\b0}` all
    the same, and `<b>` left unclosed emits nothing at the end.
    """
    name = match.group("name").casefold()
    closing = bool(match.group("close"))
    if name in _STYLE_TAGS:
        return (
            f"{{\\{_STYLE_TAGS[name]}{0 if closing else 1}}}" if not match.group("rest") else None
        )
    if name != "font":
        return None
    if closing:
        return r"{\c}" if not match.group("rest") else None
    colour = _FONT_COLOUR.match(match.group("rest"))
    if colour is None:
        return None
    value = int(colour.group("hex"), 16)
    bgr = (value & 0xFF) << 16 | (value & 0x00FF00) | (value >> 16) & 0xFF
    return f"{{\\c&H{bgr:X}&}}"


def event_text(text: str) -> str | None:
    """`text` as libavcodec's ASS event text, or `None` when it will not be predicted.

    `text` is the cue's SubRip body with its markup intact — the cue index carries the plain text,
    which has already lost what this has to reproduce.
    """
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    joined = "\\N".join(lines)
    # One `{\an}` passes through wherever it sits; a second is dropped rather than copied.
    if len(_POSITION.findall(joined)) > 1:
        return None
    out: list[str] = []
    cursor = 0
    for match in _TAG.finditer(joined):
        override = _tag_ass(match)
        if override is None:
            return None
        out.append(joined[cursor : match.start()])
        out.append(override)
        cursor = match.end()
    out.append(joined[cursor:])
    rendered = "".join(out)
    # A `<` or `>` that survived is one `_TAG` did not claim, and `srtdec` would have eaten it.
    return None if "<" in rendered or ">" in rendered else rendered


def _timestamp(seconds: float) -> str:
    """`H:MM:SS.cc`, truncated. mpv truncates the centiseconds where ffmpeg's muxer rounds them, and
    the row this has to match is mpv's."""
    total = max(0, int(seconds * 1_000))
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    return f"{hours}:{minutes:02d}:{rest // 1000:02d}.{rest % 1000 // 10:02d}"


def dialogue_row(cue: Cue, markup: str) -> str | None:
    """The whole `Dialogue:` line mpv builds for this cue, or `None` when it will not be predicted.

    The style is libavcodec's `Default` and every other field is its constant: `srtdec` carries no
    layer, actor, margin or effect through, so there is nothing here to get wrong but the text.
    """
    text = event_text(markup)
    if text is None:
        return None
    return f"Dialogue: 0,{_timestamp(cue.start)},{_timestamp(cue.end)},Default,,0,0,0,,{text}"


def markup_by_cue(content: str) -> dict[tuple[int, int], str]:
    """Each cue's SubRip body with its markup, keyed by its millisecond span.

    Keyed by timing rather than by order because the caller matches against the cue index, and the
    index drops empty cues — so the two orderings are not the same sequence.
    """
    found: dict[tuple[int, int], str] = {}
    for block in re.split(r"\n[ \t]*\n", content.replace("\r\n", "\n").replace("\r", "\n")):
        span = _span(block.split("\n"))
        if span is not None:
            found[span[0]] = span[1]
    return found


_SPAN = re.compile(
    r"(?P<sh>\d+):(?P<sm>\d{1,2}):(?P<ss>\d{1,2})[,.](?P<sms>\d{1,3})"
    r"\s*-->\s*"
    r"(?P<eh>\d+):(?P<em>\d{1,2}):(?P<es>\d{1,2})[,.](?P<ems>\d{1,3})"
)


def _span(lines: Iterable[str]) -> tuple[tuple[int, int], str] | None:
    rows = [line for line in lines if line.strip()]
    for index, line in enumerate(rows):
        match = _SPAN.match(line.strip())
        if match is None:
            continue
        start = _ms(match.group("sh"), match.group("sm"), match.group("ss"), match.group("sms"))
        end = _ms(match.group("eh"), match.group("em"), match.group("es"), match.group("ems"))
        body = "\n".join(rows[index + 1 :])
        return ((start, end), body) if body.strip() else None
    return None


def _ms(hours: str, minutes: str, seconds: str, fraction: str) -> int:
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(fraction.ljust(3, "0"))
    )
