"""The per-token colour Saitenka paints over mpv's own subtitle pixels.

mpv keeps drawing the cue; this draws each token again, in its own face at its own size and place,
in the colour the reading state calls for. Glyphs only — the authored outline and shadow are left
where mpv drew them, so they keep framing the coloured glyph instead of being reproduced slightly
wrong.

Two things make this safe to send. It goes to mpv's **OSD** libass through `osd-overlay ass-events`,
and a probe against that renderer found per-token `\\pos`-ed events agree with our own layout
exactly — width, height and both origins — because per-glyph placement never accumulates an advance
the way a single run does. And every token that cannot be drawn faithfully is dropped rather than
approximated: a token with no measured face or size is simply not coloured.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Placement is top-left (`\\an7`) at the token's measured origin, so the payload never depends on
#: the OSD track's own alignment or margins.
_PREAMBLE = r"\an7"


@dataclass(frozen=True, slots=True)
class TokenPaint:
    """One token, ready to draw: where it is, what it is, and what colour it should be."""

    text: str
    x: int
    y: int
    font_name: str
    #: In the same units as `x`/`y` — the frame the overlay declares, not the document's script res.
    font_size: float
    #: 0xRRGGBB. The reading state's colour for this token.
    rgb: int
    #: Our own hairline border, in the same units. Not the authored one: it exists to swallow the
    #: antialiased fringe of the glyph underneath, which would otherwise show as a coloured halo's
    #: negative. Sized by the caller; zero disables it.
    border: float = 0.0

    @property
    def drawable(self) -> bool:
        r"""Whether this token can be drawn faithfully rather than approximately.

        A missing face or a non-positive size means the measurement did not resolve one, and drawing
        at a guess puts the wrong glyph shape over the right word — worse than leaving it uncoloured,
        because the user cannot tell it is wrong.

        Text containing ASS syntax is refused for the same reason rather than escaped. `{` opens an
        override block, and a backslash begins a tag: escaping either changes what libass lays out,
        and an overprint whose advances differ from mpv's is a coloured smear beside the word.
        """
        return (
            bool(self.text.strip())
            and bool(self.font_name)
            and self.font_size > 0
            and not (set(self.text) & set("{}\\\n"))
        )


def _ass_colour(rgb: int) -> str:
    """`\\1c` wants BGR, and only the three colour bytes."""
    return f"&H{(rgb & 0xFF) << 16 | (rgb & 0x00FF00) | (rgb >> 16) & 0xFF:06X}&"


def event_line(paint: TokenPaint) -> str:
    return (
        f"{{{_PREAMBLE}\\pos({paint.x},{paint.y})"
        f"\\fn{paint.font_name}\\fs{paint.font_size:g}"
        f"\\1c{_ass_colour(paint.rgb)}\\bord{paint.border:g}\\shad0}}{paint.text}"
    )


def payload(paints: list[TokenPaint]) -> str:
    """One `ass-events` payload for the whole cue, or `""` when nothing can be drawn.

    Empty rather than partial-with-a-marker: an empty payload clears the slot, which is exactly what
    "this cue has no overprint" has to look like. The caller sends it either way, so a cue that
    cannot be coloured removes the previous cue's colour instead of leaving it on screen.
    """
    return "\n".join(event_line(paint) for paint in paints if paint.drawable)
