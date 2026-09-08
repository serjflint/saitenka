r"""Where a token lands when it is drawn on its own, instead of inside its line.

The overprint redraws one token as its own ``\an7\pos``-ed event. That is not the layout the token
was measured in, and three things change:

* **The anchor.** ``\an7`` places the *line box* at the position given; the measured rectangle is the
  glyphs' *ink*. Handing the ink origin to ``\pos`` plants the redraw an ascent-gap low — about a
  sixth of the font size, so it grows with the cue.
* **The extent.** A token inside a run is shaped with its neighbours and may take glyphs from more
  than one face through fallback. Alone, it is shaped by itself in the one face we recorded, and can
  come out a different width.
* **The shaping runs.** This one is not ours. mpv draws the cue through one libass instance and our
  overprint through another, and the two segment shaping runs differently: libass starts a new run
  at every glyph of a run that carries letter spacing, but only while ``whole_text_layout`` is off,
  and that flag is derived from the style's ``Encoding``. mpv's subtitle style keeps the authored
  encoding — LTR, so the flag is off and runs split per glyph. mpv's OSD style is ``Encoding = -1``
  (``sub/osd_libass.c``), which resolves to ``FRIBIDI_PAR_ON`` and turns the flag implicitly *on*,
  so the OSD shapes the whole token as one run. Whole-run and per-glyph shaping give different
  advances — 1.4px and 1.8px on the first two glyphs of one four-glyph token, and 0 on the rest, so
  there is no uniform correction to apply.

The answer to the third is to stop handing the OSD a run to shape: a token whose run has spacing is
drawn one **event per glyph**, and a lone glyph is a single shaping run in either instance. That
needs each glyph's own offset, which is what `Fragment.glyph_dx` carries.

Neither of the first two is recoverable from the measured rectangle, and libass reports no baseline
to derive the anchor from. So this asks the question directly: render the fragmented layout — the
exact events the overprint would send — and read back where each part's ink actually lands.

An offset is a correction to apply, and that is what the overprint uses. A size difference is not
correctable — it means the token cannot be redrawn faithfully in isolation and would belong on the
raster device instead — but nothing routes on it yet; `Fragment.matches` exists so that decision has
a measurement to stand on when it is made.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Anchors are laid one per row down a tall column, far enough apart that no two tokens' ink can
#: touch and be attributed to the wrong one. The column starts inset so a glyph with a negative left
#: bearing still lands inside the frame and is measured rather than clipped.
_ANCHOR_X = 64
ROW_PITCH = 4


def run_tags(spacing: float, scale_x: float, *, bold: bool = False, italic: bool = False) -> str:
    r"""``\fsp``/``\fscx``/``\b``/``\i`` for a run, omitting whichever holds libass's default.

    Omitted rather than always emitted so a cue that overrides none produces the same payload bytes
    it did before these were carried — the calibration keys its cache on that string.
    """
    tags = "" if not spacing else f"\\fsp{spacing:g}"
    if scale_x != 100.0:
        tags += f"\\fscx{scale_x:g}"
    if bold:
        tags += "\\b1"
    if italic:
        tags += "\\i1"
    return tags


@dataclass(frozen=True, slots=True)
class Fragment:
    """One token as it comes out when drawn alone."""

    token_index: int
    #: Ink origin minus the anchor asked for. Subtract from the measured rectangle's origin to get
    #: the position that puts the redraw's ink where the token actually is.
    dx: int
    dy: int
    width: int
    height: int
    #: Per glyph, what to add to the token's own anchor to place that glyph as its own event. Empty
    #: when the token is drawn whole — a run without letter spacing, where both libass instances
    #: shape it identically and one event is faithful.
    glyph_dx: tuple[int, ...] = ()
    glyph_dy: tuple[int, ...] = ()

    def anchor_for(self, x: int, y: int) -> tuple[int, int]:
        return (x - self.dx, y - self.dy)

    def matches(self, width: int, height: int, *, slack: int = 1) -> bool:
        """Whether drawing this token alone reproduces the extent it had inside its line.

        ``slack`` is one pixel per axis: the two renders round their ink bounds independently, and a
        glyph's anti-aliased edge can fall either side of the threshold. A token that fragments
        badly is out by a sixth of its size or more, which is what this separates.
        """
        return abs(self.width - width) <= slack and abs(self.height - height) <= slack


@dataclass(frozen=True, slots=True)
class FragmentRequest:
    token_index: int
    text: str
    font_name: str
    font_size: float
    #: The run metrics the token was laid out with. The probe emits them for the same reason it
    #: emits the face: it must reproduce the overprint's event exactly, and letter spacing moves
    #: every glyph after the first — an anchor measured without them corrects for the wrong run.
    spacing: float = 0.0
    scale_x: float = 100.0
    bold: bool = False
    italic: bool = False

    @property
    def per_glyph(self) -> bool:
        r"""Whether this token has to be redrawn glyph by glyph.

        Only a multi-glyph run with letter spacing: that is the one condition under which mpv's two
        libass instances segment shaping runs differently. Without it they agree, and the token
        stays a single event — which keeps both the payload and this probe as cheap as they were.

        Positive spacing only, though libass's own condition (`info->hspacing`) is truthy either
        way. Negative spacing pulls glyphs into each other, and the probe measures each one by
        drawing it in its own color: overlapping ink makes a pixel ambiguous, which the backend
        refuses for the whole render rather than the one token. Tight typesetting would therefore
        cost every token in the batch its anchor. Taking the single-event path leaves such a token
        where it was before any of this, which is a fraction of a pixel out, not uncorrected.
        """
        return self.spacing > 0 and len(self.text) > 1


@dataclass(frozen=True, slots=True)
class _Slot:
    """One coloured draw in the probe document, and what it measures."""

    request_index: int
    glyph_index: int
    #: Drawn on a row of its own rather than inside the token's row.
    alone: bool
    anchor: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ProbeLayout:
    document: str
    #: One entry per colour in the document, in the order the colours were consumed.
    slots: tuple[_Slot, ...] = ()


def probe_colour_count(requests: Sequence[FragmentRequest]) -> int:
    """How many distinct colours `probe_document` needs for these requests."""
    return sum(2 * len(item.text) if item.per_glyph else 1 for item in requests)


def probe_rows(requests: Sequence[FragmentRequest]) -> int:
    """How many rows the probe lays out, so the caller can size the frame it renders into."""
    return sum(1 + len(item.text) if item.per_glyph else 1 for item in requests)


def _colour(rgb: int) -> str:
    """A colour override as its own block: the body sits OUTSIDE the event's opening block, so a
    bare tag there would be drawn as literal text and leave the glyph in the style's colour."""
    return f"{{\\1c&H{(rgb & 0xFF) << 16 | (rgb & 0x00FF00) | (rgb >> 16) & 0xFF:06X}&}}"


def _event(anchor: tuple[int, int], request: FragmentRequest, body: str) -> str:
    tags = run_tags(request.spacing, request.scale_x, bold=request.bold, italic=request.italic)
    return (
        f"Dialogue: 0,0:00:00.00,9:00:00.00,P,,0,0,0,,"
        f"{{\\an7\\pos({anchor[0]},{anchor[1]})\\fn{request.font_name}"
        f"\\fs{request.font_size:g}{tags}\\bord0\\shad0}}{body}"
    )


def probe_document(
    requests: Sequence[FragmentRequest], palette_rgb: Sequence[int], frame: tuple[int, int]
) -> ProbeLayout:
    r"""An ASS document drawing each token the way the overprint will, and what each colour measures.

    Every token gets a row in the same ``\an7\pos`` form the overprint emits — the point is to
    reproduce that layout, not to improve on it. A token that must be drawn glyph by glyph also gets
    one colour per glyph on that row (where the glyph sits *inside* the token) and one extra row per
    glyph drawn alone (where it sits as its own event). The difference between those two is exactly
    the offset the payload needs to place the glyph's event.
    """
    pitch = max(int(max((request.font_size for request in requests), default=0)) + ROW_PITCH, 1)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {frame[0]}\nPlayResY: {frame[1]}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        "Style: P,sans-serif,20,&H00FFFFFF,&H00000000,&H00000000,0,0,1,0,0,7,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    rows: list[str] = []
    slots: list[_Slot] = []
    colours = iter(palette_rgb)
    row = 0
    for index, request in enumerate(requests):
        anchor = (_ANCHOR_X, row * pitch)
        row += 1
        if not request.per_glyph:
            slots.append(_Slot(index, 0, alone=False, anchor=anchor))
            rows.append(_event(anchor, request, _colour(next(colours)) + request.text))
            continue
        body = ""
        for glyph_index, character in enumerate(request.text):
            slots.append(_Slot(index, glyph_index, alone=False, anchor=anchor))
            body += _colour(next(colours)) + character
        rows.append(_event(anchor, request, body))
        for glyph_index, character in enumerate(request.text):
            alone_anchor = (_ANCHOR_X, row * pitch)
            row += 1
            slots.append(_Slot(index, glyph_index, alone=True, anchor=alone_anchor))
            rows.append(_event(alone_anchor, request, _colour(next(colours)) + character))
    return ProbeLayout(header + "\n".join(rows) + "\n", tuple(slots))


def fragments_from(
    measured: Sequence[tuple[int, int, int, int, int]],
    requests: Sequence[FragmentRequest],
    layout: ProbeLayout,
) -> dict[int, Fragment]:
    """Turn measured ink rectangles from the probe render into per-token corrections.

    ``measured`` is ``(slot, x, y, width, height)`` as the probe rendered them. A token missing any
    of its slots is dropped entirely rather than corrected from a partial measurement: half a
    per-glyph layout would place some of its glyphs and leave the rest behind.
    """
    by_slot = {item[0]: item for item in measured}
    inside: dict[int, dict[int, tuple[int, int]]] = {}
    alone: dict[int, dict[int, tuple[int, int]]] = {}
    extent: dict[int, list[int]] = {}
    anchors: dict[int, tuple[int, int]] = {}
    for ordinal, slot in enumerate(layout.slots):
        item = by_slot.get(ordinal)
        if item is None:
            continue
        _ordinal, x, y, width, height = item
        offset = (x - slot.anchor[0], y - slot.anchor[1])
        if slot.alone:
            alone.setdefault(slot.request_index, {})[slot.glyph_index] = offset
            continue
        inside.setdefault(slot.request_index, {})[slot.glyph_index] = offset
        anchors[slot.request_index] = slot.anchor
        box = extent.setdefault(slot.request_index, [x, y, x + width, y + height])
        box[0], box[1] = min(box[0], x), min(box[1], y)
        box[2], box[3] = max(box[2], x + width), max(box[3], y + height)

    out: dict[int, Fragment] = {}
    for index, request in enumerate(requests):
        measured_box = extent.get(index)
        offsets = inside.get(index, {})
        wanted = len(request.text) if request.per_glyph else 1
        if measured_box is None or len(offsets) != wanted:
            continue
        box = measured_box
        anchor = anchors[index]
        glyph_dx: tuple[int, ...] = ()
        glyph_dy: tuple[int, ...] = ()
        if request.per_glyph:
            solo = alone.get(index, {})
            if len(solo) != len(request.text):
                continue
            glyph_dx = tuple(offsets[g][0] - solo[g][0] for g in range(len(request.text)))
            glyph_dy = tuple(offsets[g][1] - solo[g][1] for g in range(len(request.text)))
        out[index] = Fragment(
            request.token_index,
            box[0] - anchor[0],
            box[1] - anchor[1],
            box[2] - box[0],
            box[3] - box[1],
            glyph_dx,
            glyph_dy,
        )
    return out
