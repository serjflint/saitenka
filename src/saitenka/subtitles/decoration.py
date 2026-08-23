r"""Device 3 of the colour ladder: the reading state as a mark, when it cannot be the glyph.

Devices 1 and 2 both colour the word itself — one by redrawing it, one by tinting the coverage the
measurement already produced. Each has a precondition: a face mpv's OSD renderer can load, or a
mask kept from that render. When a token has neither, the colour has nowhere to go, and the choice
is between a mark that is *beside* the word and no colour at all.

This is the mark. It is drawn from the hit box alone — a `\p1` vector rectangle, which libass
rasterises without consulting a face — so it has no precondition left to fail. That is the point of
having a bottom rung: every rung above it can stand down for a reason of its own, and the reading
state still reaches the reader.

It deliberately does not try to look like the word. An underline says "this token is in this state"
and leaves mpv's typesetting untouched; a filled box behind the glyph would fight the authored
outline it was meant to preserve.
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
    """One token's hit box and the colour its reading state calls for."""

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


def _ass_colour(rgb: int) -> str:
    return f"&H{(rgb & 0xFF) << 16 | (rgb & 0x00FF00) | (rgb >> 16) & 0xFF:06X}&"


def event_line(rule: TokenRule) -> str:
    thickness = rule.thickness
    return (
        rf"{{\an7\pos({rule.x},{rule.y + rule.height + _GAP})"
        rf"\1c{_ass_colour(rule.rgb)}\bord0\shad0\p1}}"
        f"m 0 0 l {rule.width} 0 l {rule.width} {thickness} l 0 {thickness}"
    )


def payload(rules: list[TokenRule]) -> str:
    """The whole cue's rules as `ass-events` lines, or `""` when none is drawable."""
    return "\n".join(event_line(rule) for rule in rules if rule.drawable)
