r"""The JLPT level underline, drawn under a token's hit box.

The level is not the reading state and does not replace it: `app/scoring.py` produces a color and an
underline per token, and a word can be both due for review and N3. The standard renderer has always
drawn both, so this is what stops the level from disappearing whenever native-visible mode is on.

Drawn from the hit box alone — a `\p1` vector rectangle, which libass rasterises without consulting
a face — so unlike the color devices it has no font precondition to fail.

It is deliberately *not* used to stand in for a color a token could not get. That mark would sit
where this one sits and mean something else entirely, and the reader has no way to tell two
underlines apart. An uncolorable token is left uncolored; it keeps its box, its tooltip and its
mining, and says nothing it cannot say truthfully.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A rule thinner than this disappears on a small cue, and one thicker than this reads as a
#: highlight rather than an underline. The proportion between them is the token's own height.
_MIN_RULE = 1
_MAX_RULE = 3
_RULE_DIVISOR = 12

#: Between the glyph's baseline box and the rule, so a descender does not sit on it.
_GAP = 1


@dataclass(frozen=True, slots=True)
class TokenRule:
    """One token's hit box and the color of the level underline it carries."""

    x: int
    y: int
    width: int
    height: int
    #: 0xRRGGBB.
    rgb: int

    @property
    def drawable(self) -> bool:
        return self.width > 0 and self.height > 0 and 0 <= self.rgb <= 0xFFFFFF

    @property
    def thickness(self) -> int:
        return max(_MIN_RULE, min(_MAX_RULE, self.height // _RULE_DIVISOR))


def _ass_color(rgb: int) -> str:
    return f"&H{(rgb & 0xFF) << 16 | (rgb & 0x00FF00) | (rgb >> 16) & 0xFF:06X}&"


def event_line(rule: TokenRule) -> str:
    thickness = rule.thickness
    return (
        rf"{{\an7\pos({rule.x},{rule.y + rule.height + _GAP})"
        rf"\1c{_ass_color(rule.rgb)}\bord0\shad0\p1}}"
        f"m 0 0 l {rule.width} 0 l {rule.width} {thickness} l 0 {thickness}"
    )


def payload(rules: list[TokenRule]) -> str:
    """The whole cue's rules as `ass-events` lines, or `""` when none is drawable."""
    return "\n".join(event_line(rule) for rule in rules if rule.drawable)
