"""Pure adapters from common subtitle formats to Saitenka cues."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

import pysubs2

from saitenka_subtitles.ass import ass_soft_break, decode_ass_text
from saitenka_subtitles.model import Cue

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


@dataclass(frozen=True)
class CueProjection:
    cues: list[Cue]
    authored: list[Cue]
    owners: tuple[int, ...]


_CLIP = re.compile(r"\\i?clip\([^)]*\)")
_ANIMATION = re.compile(r"\\(?:t\(|move\(|(?:kt|kf|ko|k|K)\d)")
_OVERRIDES = re.compile(r"\{[^}]*\}")


def _metadata(event: pysubs2.SSAEvent) -> tuple:
    return (
        event.type,
        event.layer,
        event.style,
        event.name,
        event.marginl,
        event.marginr,
        event.marginv,
        event.effect,
    )


def _animated_run(events: list[pysubs2.SSAEvent]) -> bool:
    if len(events) < 3:
        return False
    commands = ["".join(_OVERRIDES.findall(event.text)) for event in events]
    if all(
        _ANIMATION.search(command) or event.effect == "Karaoke"
        for command, event in zip(commands, events, strict=True)
    ):
        return True
    clips = [tuple(_CLIP.findall(command)) for command in commands]
    return all(clips) and len(set(clips)) > 1


def _ass_rows(content: str) -> list[tuple[pysubs2.SSAEvent, Cue]]:
    soft_break = ass_soft_break(content)
    return [
        (event, cue)
        for event in _parse(content, "ass")
        if (
            cue := _cue(
                event, _HTML_TAG.sub("", decode_ass_text(event.text, soft_break=soft_break))
            )
        )
    ]


def _ass_projection(content: str) -> CueProjection:
    rows = sorted(_ass_rows(content), key=lambda row: row[0].start)
    groups: dict[int, list[int]] = {}
    exact: dict[tuple, int] = {}
    runs: dict[tuple, list[int]] = {}

    def finish(run: list[int]) -> None:
        if _animated_run([rows[position][0] for position in run]):
            for position in run[1:]:
                groups[run[0]].extend(groups.pop(position))

    for position, (event, cue) in enumerate(rows):
        signature = (*_metadata(event), event.start, event.end, event.text)
        if signature in exact:
            groups[exact[signature]].append(position)
            continue
        exact[signature] = position
        groups[position] = [position]
        key = (*_metadata(event), cue.text, _CLIP.sub("", event.text))
        run = runs.get(key, [])
        if run:
            previous = rows[run[-1]][0]
            if previous.end != event.start or not 0 < previous.end - previous.start <= 100:
                finish(run)
                run = []
        run.append(position)
        runs[key] = run
    for run in runs.values():
        finish(run)
    cues: list[Cue] = []
    owners = [0] * len(rows)
    for first, members in groups.items():
        for member in members:
            owners[member] = len(cues)
        cues.append(
            Cue(rows[first][1].start, max(rows[i][1].end for i in members), rows[first][1].text)
        )
    return CueProjection(cues, [cue for _, cue in rows], tuple(owners))


def parse_ass(content: str, *, collapse: bool = True) -> list[Cue]:
    """Parse ASS study rows; ``collapse=False`` retains authored rows for retiming."""
    return _ass_projection(content).cues if collapse else [cue for _, cue in _ass_rows(content)]


def _parse_format(content: str, format_: str) -> list[Cue]:
    if format_ in {"ass", "ssa"}:
        return parse_ass(content)
    if format_ == "srt":
        return parse_srt(content)
    return [cue for event in _parse(content, format_) if (cue := _cue(event, event.plaintext))]


def parse_projection(content: str, filename: str) -> CueProjection:
    """Parse study rows with an explicit owner for every authored row."""
    extension = PurePath(filename).suffix.lower().lstrip(".")

    def parse(format_: str) -> CueProjection:
        if format_ in {"ass", "ssa"}:
            return _ass_projection(content)
        cues = sorted(_parse_format(content, format_), key=lambda cue: cue.start)
        return CueProjection(cues, cues, tuple(range(len(cues))))

    result = parse(_FORMATS[extension]) if extension in _FORMATS else None
    if result is None or not result.cues:
        result = max(
            (parse(format_) for format_ in ("ass", "srt", "vtt")),
            key=lambda item: len(item.authored),
        )
    return result


def parse_cues(content: str, filename: str) -> list[Cue]:
    """Parse study cues using the filename hint, falling back across supported formats."""
    return parse_projection(content, filename).cues
