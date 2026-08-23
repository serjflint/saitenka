"""What libass actually resolved each family to, read back from mpv's log.

The measuring renderer is handed the same four font sources mpv's is (`subtitle_fonts`), but equal
candidate sets still leave the per-glyph fallback order to libass — and a substituted face is a
different advance, so the boxes come out the wrong width with nothing else moving.

libass narrates its own decisions at debug level, which mpv's `--log-file` captures and every report
already bundles. That is a log channel with no API guarantee: it can change wording or disappear in
a libass release. Losing it costs the verification, not the fonts — this only ever *checks* a
resolution `subtitle_fonts` already made possible.

mpv tags each line with which of its two libass instances produced it, so the subtitle renderer's
choices are separable from the OSD's. That distinction is the whole point here: the OSD library can
never receive a container attachment, so it substituting a face is expected, and the *subtitle*
renderer substituting one is the defect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `[  12.345][d][sub/ass] fontselect: (Family, 400, 0) -> /path/Face.ttc, -1, PostScriptName`
#: The prefix is mpv's; everything after `fontselect:` is libass's own format.
_LINE = re.compile(
    r"\[(?P<module>[a-z/]+)\]\s+fontselect: \((?P<family>.*), (?P<weight>\d+), (?P<italic>\d+)\)"
    r" -> (?P<path>.*), (?P<index>-?\d+), (?P<psname>.*)$",
    # A log is many lines and `$` without this anchors only the end of the whole string, so every
    # interior line silently fails to match — a parser that quietly reads nothing.
    re.MULTILINE,
)
#: libass says this immediately BEFORE the `fontselect:` line that names the substitute.
_FALLBACK = re.compile(
    r"\[(?P<module>[a-z/]+)\]\s+fontselect: failed to find any fallback with glyph "
    r"(?P<glyph>0x[0-9A-Fa-f]+) for font: \((?P<family>.*), (?P<weight>\d+), (?P<italic>\d+)\)"
)

#: mpv's log module for the SUBTITLE renderer's libass, as opposed to `osd/libass`.
SUBTITLE_RENDERER = "sub/ass"
OSD_RENDERER = "osd/libass"


@dataclass(frozen=True, slots=True)
class FontRequest:
    family: str
    weight: int
    italic: int


@dataclass(frozen=True, slots=True)
class ResolvedFace:
    path: str
    index: int
    psname: str


def parse(log: str, *, module: str = SUBTITLE_RENDERER) -> dict[FontRequest, ResolvedFace]:
    """Every family one libass instance resolved, latest answer per request.

    Latest rather than first: libass re-resolves after a track or size change, and an early answer
    from before the current track would name a face nothing is drawing with.
    """
    resolved: dict[FontRequest, ResolvedFace] = {}
    for match in _LINE.finditer(log):
        if match["module"] != module:
            continue
        request = FontRequest(match["family"], int(match["weight"]), int(match["italic"]))
        resolved[request] = ResolvedFace(match["path"], int(match["index"]), match["psname"])
    return resolved


def unmatched_glyphs(log: str, *, module: str = SUBTITLE_RENDERER) -> list[tuple[str, FontRequest]]:
    """Glyphs libass could not find in any candidate, with the family that wanted them.

    Not a failure on its own — a fallback is normal and both sides do it — but it is where a
    divergence starts, so it belongs in the same report line as the resolutions.
    """
    return [
        (match["glyph"], FontRequest(match["family"], int(match["weight"]), int(match["italic"])))
        for match in _FALLBACK.finditer(log)
        if match["module"] == module
    ]


@dataclass(frozen=True, slots=True)
class Divergence:
    request: FontRequest
    theirs: ResolvedFace | None
    ours: ResolvedFace | None


def compare(
    theirs: dict[FontRequest, ResolvedFace], ours: dict[FontRequest, ResolvedFace]
) -> list[Divergence]:
    """Families the two renderers resolved differently, or that only one of them asked for.

    A request only one side made is reported too: it means the two laid out different text, which is
    a larger disagreement than a substituted face and would otherwise read as agreement.
    """
    return [
        Divergence(request, theirs.get(request), ours.get(request))
        for request in sorted(theirs.keys() | ours.keys(), key=lambda r: (r.family, r.weight))
        if theirs.get(request) != ours.get(request)
    ]


def summary(log: str, ours: dict[FontRequest, ResolvedFace]) -> str:
    """One report line per side, then every divergence. Empty log reads as "not captured", not "agreed"."""
    theirs = parse(log)
    if not theirs:
        return "  font resolution: mpv's log carried no `fontselect:` line — nothing to verify"
    lines = [
        f"  font resolution: {len(theirs)} request(s) by mpv's subtitle renderer, {len(ours)} by ours"
    ]
    fallbacks = unmatched_glyphs(log)
    if fallbacks:
        lines.append(
            "    mpv fell back for "
            + ", ".join(f"{glyph} in {request.family}" for glyph, request in fallbacks[:4])
        )
    divergences = compare(theirs, ours)
    if not divergences:
        lines.append("    every family resolved to the same face on both sides")
    lines.extend(
        f"    ({item.request.family}, {item.request.weight}, {item.request.italic}): "
        f"mpv={item.theirs.psname if item.theirs else '-'} "
        f"ours={item.ours.psname if item.ours else '-'}"
        for item in divergences
    )
    return "\n".join(lines)
