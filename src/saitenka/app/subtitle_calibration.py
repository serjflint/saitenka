"""Does mpv's OSD renderer lay the overprint out where our measurement said it would?

Device 1 draws each token through mpv's **OSD** libass, while the hit boxes come from our own
measuring renderer. Which families the two agree on is inferred statically — from mpv's source, in
`subtitle_fonts.OsdReach` — and an inference can be wrong in a way nothing on screen shows: the
color lands on substitute glyph shapes and reads as a font choice.

`osd-overlay … compute_bounds` is the one channel that can tell. mpv renders the payload through its
OSD libass, hidden, and returns the bounding box. Comparing that with the union of the boxes we
measured is a direct check of the only claim device 1 makes.

It measures **and now decides**, on an epsilon chosen as a separator rather than as a tolerance. Two
classes have been measured: renderers that agree read exactly 0, and the substituted-face case reads
−29 px. Any boundary strictly inside that gap classifies both the same way, so the verdict does not
turn on the exact number — which is what makes acting on a two-point sample defensible where a
tolerance pulled from the air would not be.

The asymmetry is the other half of the argument. Demoting wrongly costs appearance: the token falls
to a device that colors it correctly and more plainly. Clearing wrongly costs correctness: the
color lands on substitute glyph shapes and reads as a font choice, which no user can report. So the
epsilon sits nearer the agreeing class than the disagreeing one.

Cost: `compute_bounds` makes mpv do a full render and flush its cache, on its core thread. So it runs
only while paused, and only once per distinct payload shape per surface — never during playback and
never per cue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka_subtitles import font_names

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from saitenka.app.subtitles import WordBox

#: The face and size of each drawn token, which is what decides whether the two renderers can agree.
#: Positions are deliberately excluded: they are ours in both renders, so a cue that moved but kept
#: its faces is the same question and must not pay for a second stall.
_FACE = re.compile(r"\\fn(?P<family>[^\\}]*)\\fs(?P<size>[0-9.]+)")

#: Frame pixels on any one edge. A separator between the two measured classes (0 and 29), not a
#: tolerance derived from one of them — see the module docstring for why the exact value does not
#: carry the verdict. Well above what whole-pixel rounding on each edge can contribute, and far
#: below a substituted face's error, which is a layout difference and grows with the run.
DRIFT_EPSILON_PX = 4.0


@dataclass(frozen=True, slots=True)
class Drift:
    """How far mpv's OSD layout is from ours, in frame pixels, on each edge of the bounding box."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def worst(self) -> float:
        return max(abs(self.left), abs(self.top), abs(self.right), abs(self.bottom))

    @property
    def agrees(self) -> bool:
        return self.worst <= DRIFT_EPSILON_PX


def payload_families(payload: str) -> frozenset[str]:
    """Every family the payload asked for, as the reachability sets spell a family name.

    All of them, because the measurement cannot say which one drifted: `compute_bounds` answers with
    one box for the whole payload. Demoting the set is the conservative reading, and conservative is
    the safe direction here — every device below draws the token correctly, just more plainly.
    """
    return frozenset(
        family
        for match in _FACE.finditer(payload)
        if (family := font_names.key(match.group("family")))
    )


def payload_signature(payload: str, osd: tuple[int, int]) -> str | None:
    """What makes one calibration answer reusable, or `None` when the payload draws no text.

    The surface is in it because `compute_bounds` recomputes into the resolution it is given, and a
    resize is the one thing that can move the answer without the faces changing.
    """
    faces = sorted({match.group(0) for match in _FACE.finditer(payload)})
    return f"{osd[0]}x{osd[1]}:{'|'.join(faces)}" if faces else None


def measured_bounds(
    boxes: Sequence[WordBox], *, drifting: frozenset[str] = frozenset()
) -> tuple[int, int, int, int] | None:
    """The union of the tokens device 1 drew, in frame pixels — our side of the comparison.

    Only the tokens it drew: a token the text device stood down on is not in the payload, so
    including its rect would report a drift that is really a difference in what was asked for. That
    holds for a family an earlier measurement already demoted, which is why `drifting` is here.
    """
    drawn = [
        box
        for box in boxes
        if box.font_name and box.font_size > 0 and font_names.key(box.font_name) not in drifting
    ]
    if not drawn:
        return None
    return (
        min(box.x for box in drawn),
        min(box.y for box in drawn),
        max(box.x + box.w for box in drawn),
        max(box.y + box.h for box in drawn),
    )


def drift_of(
    measured: tuple[int, int, int, int], reported: Mapping[str, object], *, border: float
) -> Drift | None:
    """Compare our union with mpv's reported box, or `None` when the reply is not one.

    `border` comes back out because `mp_ass_get_bb` (`ass_mp.c:157-179`) unions the outline and
    shadow images too, and our payload asks for a hairline border that our own measuring render did
    not draw. Leaving it in would report that fixed inflation as drift on every single cue.

    mpv's box is bottom-exclusive, like ours, so no off-by-one correction belongs here.
    """
    try:
        x0 = float(reported["x0"]) + border  # type: ignore[arg-type]
        y0 = float(reported["y0"]) + border  # type: ignore[arg-type]
        x1 = float(reported["x1"]) - border  # type: ignore[arg-type]
        y1 = float(reported["y1"]) - border  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return None
    return Drift(x0 - measured[0], y0 - measured[1], x1 - measured[2], y1 - measured[3])
