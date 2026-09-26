"""Which families a set of font bytes supplies, and which ones a document carries inside itself.

Both answer one question: is *this* family one mpv's OSD library can also load? Its library is built
from `osd_style` plus `mpv-osd-symbols` and never sees an attachment, so a family that exists only in
the container or in an in-file ``[Fonts]`` section reaches the subtitle renderer alone. Drawing an
overprint for such a token through `osd-overlay` substitutes a face: right words, wrong glyph shapes.

Over-inclusion is the safe direction. A family named here that the system also provides costs the
color on those tokens; a family missed here paints them in the wrong shapes, and nothing on screen
says so. So every name a matcher might key on is collected, not just the typographic family.
"""

from __future__ import annotations

import logging
import struct
import unicodedata
from dataclasses import dataclass
from io import BytesIO

from fontTools.ttLib import TTCollection, TTFont, TTLibError

log = logging.getLogger(__name__)

#: libass keys a face on its family (1), its typographic family (16), and — in `ass_fontselect`'s
#: fallback pass — its full name (4). All three, because any of them can be what the style asked for.
_FAMILY_NAME_IDS = (1, 4, 16)

_MAX_FONT_BYTES = 64 * 1024 * 1024


def key(family: str) -> str:
    """How a family name is spelled when two sets of them are compared.

    One spelling, in one place, because the comparison is only sound if both sides normalise the
    same way — and the sides are far apart: a name read out of a font table, a `Fontname` from an
    ASS style, and an `\\fn` parsed back out of a payload we sent mpv. libass drops a leading `@`,
    the vertical-writing marker, before it looks a family up.
    """
    return family.strip().removeprefix("@").casefold()


def _names(font: TTFont) -> set[str]:
    try:
        records = font["name"].names
    except (TTLibError, KeyError, AssertionError):
        return set()
    found = set()
    for record in records:
        if record.nameID in _FAMILY_NAME_IDS:
            # `toStr` decodes per platform/encoding and raises on a malformed record; one bad record
            # must not cost the other names in the same table.
            try:
                found.add(record.toStr().strip().casefold())
            except (UnicodeDecodeError, ValueError):
                continue
    found.discard("")
    return found


def families(data: bytes) -> frozenset[str]:
    """Every family name the bytes advertise, case-folded. Unparseable bytes yield nothing."""
    if not data or len(data) > _MAX_FONT_BYTES:
        return frozenset()
    try:
        fonts = (
            TTCollection(BytesIO(data), lazy=True).fonts
            if data[:4] == b"ttcf"
            else [TTFont(BytesIO(data), lazy=True, fontNumber=0)]
        )
        return frozenset().union(*(_names(font) for font in fonts)) if fonts else frozenset()
    except (TTLibError, OSError, ValueError, KeyError, IndexError, struct.error) as error:
        log.debug("could not read a font's names: %s", error)
        return frozenset()


#: Enough glyphs to tell two faces apart, few enough that a probe line stays inside its frame.
_PROBE_GLYPHS = 24


@dataclass(frozen=True, slots=True)
class FaceProbe:
    """How to ask a font provider for one face by name, and text only that face would draw."""

    family: str
    names: frozenset[str]
    bold: bool
    italic: bool
    sample: str


def _sample(font: TTFont) -> str:
    """Characters this face covers, spread across its cmap rather than taken from the front."""
    try:
        cmap = font.getBestCmap() or {}
    except (TTLibError, KeyError, AssertionError, struct.error):
        return ""
    drawable = [
        chr(code)
        for code in sorted(cmap)
        if chr(code) not in "{}\\" and unicodedata.category(chr(code))[0] in "LNPS"
    ]
    step = max(1, len(drawable) // _PROBE_GLYPHS)
    return "".join(drawable[::step][:_PROBE_GLYPHS])


def _probe(font: TTFont) -> FaceProbe | None:
    try:
        table = font["name"]
        # The Windows record first: it is the one libass indexes, and a Mac record can differ
        # ("Hiragino Sans" against "Hiragino Sans W3") and name a family libass never finds.
        record = table.getName(1, 3, 1, 0x409) or table.getName(1, 3, 1)
        family = record.toUnicode() if record is not None else table.getDebugName(1)
        # fontTools builds table attributes at decompile time, so the stubs cannot name them.
        weight = getattr(font["OS/2"], "usWeightClass", 400) if "OS/2" in font else 400
        italic = bool(getattr(font["head"], "macStyle", 0) & 2)
    except (TTLibError, KeyError, AssertionError, struct.error):
        return None
    sample = _sample(font)
    if not family or not sample:
        return None
    return FaceProbe(family.strip(), frozenset(_names(font)), weight >= 600, italic, sample)


def probes(data: bytes) -> tuple[FaceProbe, ...]:
    """One probe per face in the bytes; a face without a family name or a drawable glyph has none."""
    if not data or len(data) > _MAX_FONT_BYTES:
        return ()
    try:
        fonts = (
            TTCollection(BytesIO(data), lazy=True).fonts
            if data[:4] == b"ttcf"
            else [TTFont(BytesIO(data), lazy=True, fontNumber=0)]
        )
        return tuple(probe for font in fonts if (probe := _probe(font)) is not None)
    except (TTLibError, OSError, ValueError, KeyError, IndexError, struct.error) as error:
        log.debug("could not read a font for probing: %s", error)
        return ()


def _decode_uu(payload: str) -> bytes:
    """libass's `decode_chars`: base-64 offset by 33, big-endian, 4 characters to 3 bytes.

    A trailing group of *n* characters yields *n-1* bytes, and a group of exactly one is what libass
    calls a bad encoded size — it refuses the whole font there, so this returns nothing too.
    """
    packed = "".join(payload.split())
    if len(packed) % 4 == 1:
        return b""
    out = bytearray()
    for start in range(0, len(packed), 4):
        group = packed[start : start + 4]
        value = 0
        for index, char in enumerate(group):
            value |= ((ord(char) - 33) & 63) << (6 * (3 - index))
        out.extend((value >> ((2 - index) * 8)) & 0xFF for index in range(len(group) - 1))
    return bytes(out)


def in_document(source: bytes) -> frozenset[str]:
    """The families an ``[Fonts]`` section inside the document supplies.

    Read from the text rather than from libass, which offers no way to enumerate what it extracted.
    A section that fails to decode yields nothing, which demotes those tokens — the same direction
    every other uncertainty here takes.
    """
    try:
        text = source.decode("utf-8-sig", errors="replace")
    except (UnicodeError, AttributeError):
        return frozenset()
    found: set[str] = set()
    payload: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            found |= families(_decode_uu("".join(payload)))
            payload = []
            in_section = stripped.casefold() == "[fonts]"
        elif in_section and stripped.casefold().startswith("fontname:"):
            found |= families(_decode_uu("".join(payload)))
            payload = []
        elif in_section and stripped:
            payload.append(stripped)
    return frozenset(found | families(_decode_uu("".join(payload))))
