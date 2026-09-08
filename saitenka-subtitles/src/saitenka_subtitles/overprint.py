"""The per-token color Saitenka paints over mpv's own subtitle pixels.

mpv keeps drawing the cue; this draws each token again, in its own face at its own size and place,
in the color the reading state calls for. Glyphs only — the authored outline and shadow are left
where mpv drew them, so they keep framing the colored glyph instead of being reproduced slightly
wrong.

Two things make this safe to send. It goes to mpv's **OSD** libass through `osd-overlay ass-events`,
and a probe against that renderer found per-token `\\pos`-ed events agree with our own layout
exactly — width, height and both origins — because per-glyph placement never accumulates an advance
the way a single run does. And every token that cannot be drawn faithfully is dropped rather than
approximated: a token with no measured face or size is simply not colored.
"""

from __future__ import annotations

from dataclasses import dataclass

from saitenka_subtitles.fragments import run_tags

#: Placement is top-left (`\\an7`) at the token's measured origin, so the payload never depends on
#: the OSD track's own alignment or margins.
_PREAMBLE = r"\an7"


@dataclass(frozen=True, slots=True)
class TokenPaint:
    """One token, ready to draw: where it is, what it is, and what color it should be."""

    text: str
    x: int
    y: int
    font_name: str
    #: In the same units as `x`/`y` — the frame the overlay declares, not the document's script res.
    font_size: float
    #: 0xRRGGBB. The reading state's color for this token.
    rgb: int
    #: Our own hairline border, in the same units. Not the authored one: it exists to swallow the
    #: antialiased fringe of the glyph underneath, which would otherwise show as a colored halo's
    #: negative. Sized by the caller; zero disables it.
    border: float = 0.0
    #: The run's letter spacing (same units as `font_size`) and horizontal scale (percent). The face
    #: and size place the token; these place every glyph after its first, and dropping them walks the
    #: colour left across the token — 79% coverage on a shipped `Spacing: 4` cue.
    spacing: float = 0.0
    scale_x: float = 100.0
    #: Weight and slant. Not metrics: libass resolves these to a different FACE, so omitting them
    #: draws the right word in the wrong glyphs.
    bold: bool = False
    italic: bool = False
    #: Per-glyph offsets from `x`/`y`. Non-empty means this token is emitted one event per glyph,
    #: because mpv's OSD renderer would otherwise shape the whole run and place its glyphs a little
    #: differently from the subtitle renderer that drew the cue — see `saitenka_subtitles.fragments`.
    glyph_dx: tuple[int, ...] = ()
    glyph_dy: tuple[int, ...] = ()

    @property
    def drawable(self) -> bool:
        r"""Whether this token can be drawn faithfully rather than approximately.

        A missing face or a non-positive size means the measurement did not resolve one, and drawing
        at a guess puts the wrong glyph shape over the right word — worse than leaving it uncolored,
        because the user cannot tell it is wrong.

        Text containing ASS syntax is refused for the same reason rather than escaped. `{` opens an
        override block, and a backslash begins a tag: escaping either changes what libass lays out,
        and an overprint whose advances differ from mpv's is a colored smear beside the word.
        """
        return (
            bool(self.text.strip())
            and bool(self.font_name)
            and self.font_size > 0
            and not (set(self.text) & set("{}\\\n"))
        )


def _ass_color(rgb: int) -> str:
    """`\\1c` wants BGR, and only the three color bytes."""
    return f"&H{(rgb & 0xFF) << 16 | (rgb & 0x00FF00) | (rgb >> 16) & 0xFF:06X}&"


def _one_event(paint: TokenPaint, text: str, x: int, y: int, *, spacing: float) -> str:
    return (
        f"{{{_PREAMBLE}\\pos({x},{y})"
        f"\\fn{paint.font_name}\\fs{paint.font_size:g}"
        f"{run_tags(spacing, paint.scale_x, bold=paint.bold, italic=paint.italic)}"
        f"\\1c{_ass_color(paint.rgb)}\\bord{paint.border:g}\\shad0}}{text}"
    )


def event_lines(paint: TokenPaint) -> list[str]:
    r"""The events that draw this token — one, or one per glyph.

    Per glyph when the measurement supplied offsets, which happens exactly when the run carries
    letter spacing. A lone glyph is a single shaping run in both of mpv's libass instances, so
    splitting the token is what makes the redraw agree with the cue; the spacing then lives in the
    positions rather than in `\fsp`, and re-emitting it would apply it twice.
    """
    if not paint.glyph_dx:
        return [_one_event(paint, paint.text, paint.x, paint.y, spacing=paint.spacing)]
    return [
        _one_event(
            paint,
            character,
            paint.x + paint.glyph_dx[index],
            paint.y + paint.glyph_dy[index],
            spacing=0.0,
        )
        for index, character in enumerate(paint.text)
    ]


def event_line(paint: TokenPaint) -> str:
    """Every event for this token as one payload fragment."""
    return "\n".join(event_lines(paint))


def payload(paints: list[TokenPaint]) -> str:
    """One `ass-events` payload for the whole cue, or `""` when nothing can be drawn.

    Empty rather than partial-with-a-marker: an empty payload clears the slot, which is exactly what
    "this cue has no overprint" has to look like. The caller sends it either way, so a cue that
    cannot be colored removes the previous cue's color instead of leaving it on screen.
    """
    return "\n".join(event_line(paint) for paint in paints if paint.drawable)
