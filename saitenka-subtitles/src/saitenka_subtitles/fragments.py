r"""Where a token lands when it is drawn on its own, instead of inside its line.

The overprint redraws one token as its own ``\an7\pos``-ed event. That is not the layout the token
was measured in, and two things change:

* **The anchor.** ``\an7`` places the *line box* at the position given; the measured rectangle is the
  glyphs' *ink*. Handing the ink origin to ``\pos`` plants the redraw an ascent-gap low — about a
  sixth of the font size, so it grows with the cue.
* **The extent.** A token inside a run is shaped with its neighbours and may take glyphs from more
  than one face through fallback. Alone, it is shaped by itself in the one face we recorded, and can
  come out a different width.

Neither is recoverable from the measured rectangle, and libass reports no baseline to derive the
first from. So this asks the question directly: render the fragmented layout — the exact events the
overprint would send — and read back where each token's ink actually lands and how big it is.

An offset is a correction to apply, and that is what the overprint uses today. A size difference is
not correctable — it means the token cannot be redrawn faithfully in isolation and would belong on
the raster device instead — but nothing routes on it yet; `Fragment.matches` exists so that decision
has a measurement to stand on when it is made.
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


def probe_document(
    requests: Sequence[FragmentRequest], palette_rgb: Sequence[int], frame: tuple[int, int]
) -> tuple[str, tuple[tuple[int, int], ...]]:
    r"""An ASS document drawing each token alone, and the anchor each was drawn at.

    One event per token, each in its own colour so the measurement can tell them apart, using the
    same ``\an7\pos`` form the overprint emits — the point is to reproduce that layout, not to
    improve on it.
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
    rows = []
    anchors = []
    for index, (request, rgb) in enumerate(zip(requests, palette_rgb, strict=True)):
        y = index * pitch
        anchors.append((_ANCHOR_X, y))
        bgr = (rgb & 0xFF) << 16 | (rgb & 0x00FF00) | (rgb >> 16) & 0xFF
        rows.append(
            f"Dialogue: 0,0:00:00.00,9:00:00.00,P,,0,0,0,,"
            f"{{\\an7\\pos({_ANCHOR_X},{y})\\fn{request.font_name}\\fs{request.font_size:g}"
            f"\\1c&H{bgr:06X}&\\bord0\\shad0}}{request.text}"
        )
    return header + "\n".join(rows) + "\n", tuple(anchors)


def fragments_from(
    tokens: Sequence[tuple[int, int, int, int, int]],
    requests: Sequence[FragmentRequest],
    anchors: Sequence[tuple[int, int]],
) -> dict[int, Fragment]:
    """Turn measured ink rectangles from the probe render into per-token corrections.

    ``tokens`` is ``(token_index, x, y, width, height)`` as the probe rendered them; a token the
    probe could not recover is simply absent, and its caller keeps whatever it would have done
    without a correction.
    """
    by_index = {token[0]: token for token in tokens}
    out: dict[int, Fragment] = {}
    for request, (anchor_x, anchor_y) in zip(requests, anchors, strict=True):
        measured = by_index.get(request.token_index)
        if measured is None:
            continue
        _index, x, y, width, height = measured
        out[request.token_index] = Fragment(
            request.token_index, x - anchor_x, y - anchor_y, width, height
        )
    return out
