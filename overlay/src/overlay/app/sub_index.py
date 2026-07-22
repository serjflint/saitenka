"""Parse an external subtitle file (.srt/.vtt/.ass) into a time-ordered cue index.

Subtitle navigation (Alt+←/→/↓ → mpv ``sub-seek``) lags because ``sub-seek`` performs a *video* seek —
demux + decode to the new timestamp (worse on HEVC / large keyframe gaps). The picture and the text
arrive together, late. This index decouples the two: given the current cue we can compute the target
cue purely in Python and render it in the overlay INSTANTLY, then let mpv's seek catch the picture up
behind it (the controller still issues the real ``sub-seek``; mpv's ``sub-text`` stays the source of
truth once the seek settles).

The parser is a faithful port of SubMiner's ``subtitle-cue-parser.ts`` (srt/vtt share one parser; ass
is separate) plus its ``findActiveSubtitleCueIndex`` resolver — a battle-tested reference. Pure/no I/O
below ``load_index`` so the parse + "which cue for time/text" logic is unit-testable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# When the current position sits in a gap, treat a cue starting within this window as the active one
# (matches SubMiner's ACTIVE_CUE_LOOKAHEAD_SEC) so "next" from just before a cue lands on that cue.
ACTIVE_CUE_LOOKAHEAD_SEC = 0.5

_HTML_TAG = re.compile(r"</?[A-Za-z][^>\n]*>")  # <i>, </i>, <font …>, …
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")  # {\an8}, {\pos(…)}, …
# SRT/VTT: optional-hours, `,` or `.` millis separator, on the "-->" timing line
_SRT_TIMING = re.compile(
    r"^\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{1,3})"
)
_ASS_TIMING = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{1,2})$")  # H:MM:SS.cc (centiseconds)


@dataclass(frozen=True)
class SubCue:
    start: float  # seconds
    end: float
    text: str  # display text, override/HTML tags stripped; \N / \n preserved as line breaks


def _srt_ts(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    return (
        int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000
    )


def _sanitize(text: str) -> str:
    return _HTML_TAG.sub("", _ASS_OVERRIDE.sub("", text)).strip()


def parse_srt(content: str) -> list[SubCue]:
    """Parse SRT (and WebVTT, which shares the block/timing shape) → cues."""
    cues: list[SubCue] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        m = _SRT_TIMING.match(lines[i])
        if not m:
            i += 1
            continue
        start = _srt_ts(m[1], m[2], m[3], m[4])
        end = _srt_ts(m[5], m[6], m[7], m[8])
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(lines[i])
            i += 1
        text = _sanitize("\n".join(text_lines))
        if text:
            cues.append(SubCue(start, end, text))
    return cues


def _ass_ts(raw: str) -> float | None:
    m = _ASS_TIMING.match(raw.strip())
    if not m:
        return None
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4].ljust(2, "0")) / 100


def parse_ass(content: str) -> list[SubCue]:
    """Parse ASS/SSA: read the ``[Events]`` ``Format:`` line for field order, then the ``Dialogue:``
    rows (the Text field is last and may itself contain commas → re-join the tail)."""
    cues: list[SubCue] = []
    in_events = False
    start_i = end_i = text_i = -1
    for line in content.splitlines():
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            in_events = t.lower() == "[events]"
            if not in_events:
                start_i = end_i = text_i = -1
            continue
        if not in_events:
            continue
        if t.startswith("Format:"):
            fields = [f.strip().lower() for f in t[len("Format:") :].split(",")]
            start_i, end_i = _index_of(fields, "start"), _index_of(fields, "end")
            text_i = _index_of(fields, "text")
            continue
        if not t.startswith("Dialogue:"):
            continue
        if start_i < 0 or end_i < 0 or text_i < 0:
            continue
        fields = t[len("Dialogue:") :].split(",")
        if start_i >= len(fields) or end_i >= len(fields) or text_i >= len(fields):
            continue
        start = _ass_ts(fields[start_i])
        end = _ass_ts(fields[end_i])
        if start is None or end is None:
            continue
        text = _sanitize(",".join(fields[text_i:]))
        if text:
            cues.append(SubCue(start, end, text))
    return cues


def _index_of(fields: list[str], name: str) -> int:
    try:
        return fields.index(name)
    except ValueError:
        return -1


def parse_cues(content: str, filename: str) -> list[SubCue]:
    """Parse by the filename's extension; if that yields nothing, try BOTH parsers and keep whichever
    found more (mislabelled/extension-less files). Returned cues are sorted by start time."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in ("srt", "vtt"):
        cues = parse_srt(content)
    elif ext in ("ass", "ssa"):
        cues = parse_ass(content)
    else:
        cues = []
    if not cues:
        ass_cues, srt_cues = parse_ass(content), parse_srt(content)
        cues = ass_cues if len(ass_cues) >= len(srt_cues) else srt_cues
    cues.sort(key=lambda c: c.start)
    return cues


def _norm(text: str) -> str:
    """Collapse whitespace/line breaks for tolerant text matching (mpv's sub-text may re-wrap)."""
    return re.sub(r"\s+", " ", text.replace("\\N", "\n")).strip()


class SubIndex:
    """A sorted cue list with the two lookups navigation needs (both pure)."""

    def __init__(self, cues: list[SubCue]):
        self.cues = sorted(cues, key=lambda c: c.start)

    def __len__(self) -> int:
        return len(self.cues)

    def locate(
        self,
        *,
        text: str | None = None,
        sub_start: float | None = None,
        time_pos: float | None = None,
        preferred: int = -1,
    ) -> int:
        """Index of the cue currently in view, or -1.

        The displayed ``text`` is the most reliable signal — it's literally what's on screen, and it
        stays valid even while a video seek is in flight and ``sub-start``/``time-pos`` are stale
        (the chaining case: after a nav render ``sub_text`` is the cue we just drew). So text wins;
        on duplicate lines it's disambiguated by the ``preferred`` hint (last jump) then ``sub-start``
        timing. Only when the text gives nothing (a gap, or mpv re-wrapped it) do we fall back to the
        exact ``sub-start`` timing, then ``time-pos`` (active-or-upcoming, else the next cue) — the
        same signals SubMiner's findActiveSubtitleCueIndex uses."""
        cues = self.cues
        if not cues:
            return -1
        if text:
            norm = _norm(text)
            matches = [i for i, c in enumerate(cues) if _norm(c.text) == norm]
            if len(matches) == 1:
                return matches[0]
            if matches:
                if preferred >= 0:
                    return min(matches, key=lambda i: abs(i - preferred))
                if sub_start is not None:
                    for i in matches:
                        if cues[i].start <= sub_start < cues[i].end:
                            return i
                return matches[0]
        if sub_start is not None:
            for i, c in enumerate(cues):
                if c.start <= sub_start < c.end:
                    return i
        if time_pos is not None:
            for i, c in enumerate(cues):
                if c.end > time_pos and c.start <= time_pos + ACTIVE_CUE_LOOKAHEAD_SEC:
                    return i
            for i, c in enumerate(cues):
                if c.end > time_pos:
                    return i
        return -1

    def target(self, current: int, delta: int, inside: bool = True) -> int:
        """The cue index reached by stepping ``delta`` (-1 prev / 0 replay / +1 next) from
        ``current`` (as returned by :meth:`locate`), or -1 when out of range.

        ``inside`` (default True) says a cue is actually on screen NOW, so prev/next straddle it
        (``current ± 1``). When False, ``current`` is merely the *upcoming* cue in a gap — so "next"
        must land ON it (not skip past, which is what mpv's ``sub-seek 1`` does), "prev" on the cue
        just before the gap, and "replay" is ambiguous (defer to mpv → out of range). ``current < 0``
        (nothing current, e.g. before the first cue): "next" opens the first cue; prev/replay have
        nothing to show."""
        if current < 0:
            tgt = 0 if delta > 0 else -1
        elif inside:
            tgt = current + delta
        elif delta > 0:
            tgt = current  # next → the upcoming cue itself (matches mpv's sub-seek 1 from a gap)
        elif delta < 0:
            tgt = current - 1  # prev → the cue shown just before this gap
        else:
            tgt = -1  # replay from a gap is ambiguous → let mpv's sub-seek decide
        return tgt if 0 <= tgt < len(self.cues) else -1


def load_index(path: str | Path) -> SubIndex | None:
    """Read + parse a subtitle file into a :class:`SubIndex`; return None (logged) on any failure or
    if no cues parse, so a bad/embedded/unsupported sub never breaks navigation (plain sub-seek)."""
    p = Path(path)
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log.debug("sub index: cannot read %s", p, exc_info=True)
        return None
    cues = parse_cues(content, p.name)
    if not cues:
        log.debug("sub index: no cues parsed from %s", p)
        return None
    log.info("sub index: %d cues from %s", len(cues), p.name)
    return SubIndex(cues)
