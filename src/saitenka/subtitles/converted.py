"""The ASS document mpv builds for a track it converted, reconstructed.

mpv does not render a SubRip file: libavcodec converts it to ASS and mpv renders that, applying a
whole branch of handling it never applies to an authored track (`configure_ass`, `sd_ass.c:523-650`).
Measuring against the file on disk would therefore measure a document mpv is not drawing.

`sub-ass-extradata` reports *property unavailable* on a converted track, so the header cannot be
read back — it is reproduced here instead, from libavcodec's default block and a port of
`mp_ass_set_style`. Every constant below is mpv's or libavcodec's, and a divergence in any of them
is not a degraded box but a wrong one.
"""

from __future__ import annotations

from dataclasses import dataclass

#: `MP_ASS_FONT_PLAYRESX`/`Y` (`ass_mp.h:31`). libavcodec assumes them when converting to ASS, and
#: VSFilter uses them by default, which is why mpv's fixups are all expressed relative to them.
PLAYRES_X = 384
PLAYRES_Y = 288

#: libavcodec's `ff_ass_subtitle_header_default` output, minus its generator comment — a `;` line
#: libass ignores, and one that carries the writer's version, so reproducing it would pin us to a
#: build for no layout difference at all.
LAVC_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {{play_res_x}}
PlayResY: {PLAYRES_Y}
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{{style}}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass(frozen=True, slots=True)
class Color:
    """An mpv `--sub-color`-style color. Alpha is opacity, the way mpv states it."""

    r: int = 255
    g: int = 255
    b: int = 255
    a: int = 255

    def as_ass(self) -> int:
        """`MP_ASS_RGBA` (`ass_mp.h:34`): RGB in the high bytes, and alpha INVERTED — ASS stores
        transparency where mpv states opacity."""
        return (self.r << 24) | (self.g << 16) | (self.b << 8) | (0xFF - self.a)


@dataclass(frozen=True, slots=True)
class SubStyle:
    """The `osd_style_opts` fields `mp_ass_set_style` reads, with mpv's `sub_style_conf` defaults.

    Defaults matter: a user who has set none of these still gets these values, so a port that
    guessed them would be wrong for the common case rather than the rare one.
    """

    font: str = "sans-serif"
    font_size: float = 38.0
    color: Color = Color()
    outline_color: Color = Color(0, 0, 0, 255)
    back_color: Color = Color(0, 0, 0, 175)
    border_style: int = 1
    outline_size: float = 1.65
    shadow_offset: float = 0.0
    spacing: float = 0.0
    margin_x: int = 19
    margin_y: int = 34
    margin_y_offset: int = 0
    align_x: int = 0
    align_y: int = 1
    blur: float = 0.0
    bold: bool = False
    italic: bool = False
    justify: int = 0


@dataclass(frozen=True, slots=True)
class RenderSpace:
    """mpv's `mp_osd_res` for the frame the subtitle is composited onto."""

    width: int
    height: int
    margins: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def video_width(self) -> float:
        return self.width - (self.margins[2] + self.margins[3])

    @property
    def video_height(self) -> float:
        return self.height - (self.margins[0] + self.margins[1])


def libass_scale_height(space: RenderSpace, *, use_margins: bool) -> float:
    """`get_libass_scale_height` (`sd_ass.c:513-521`), the height `--sub-scale-with-window` undoes.

    Without margins the text scales with the video's visible size; with them, with the size the
    video would have if it were resized to fit the frame.
    """
    video_width = space.video_width
    video_height = space.video_height
    if not use_margins or video_width < 1.0:
        return video_height
    return min(space.height, space.width / video_width * video_height)


def font_scale(
    space: RenderSpace,
    *,
    sub_scale: float = 1.0,
    use_margins: bool = True,
    scale_with_window: bool = True,
    scale_by_window: bool = True,
) -> float:
    """What `ass_set_font_scale` gets on the converted branch (`configure_ass`, `sd_ass.c:551-568`).

    NOT 1. `converted` forces mpv's override branch, which reads `--sub-scale-with-window` and
    `--sub-scale-by-window` — both default `true` (`options/options.c:344-365`) — rather than the
    `sub-ass-*` variants an authored track uses. On a 2.39:1 file in a 16:9 window the multiplier is
    well below 1, so a measuring renderer that assumed 1 lays every box out around 30% too small,
    uniformly, with every existing meter reading green.
    """
    scale = sub_scale
    if scale_with_window:
        scale *= space.height / max(libass_scale_height(space, use_margins=use_margins), 1)
    if not scale_by_window:
        factor = space.height / 720.0
        if factor != 0.0:
            scale /= factor
    return scale


def play_res_x(space: RenderSpace) -> int:
    """The DAR-corrected `PlayResX` mpv writes onto a converted track (`sd_ass.c:625-628`).

    libavcodec's conversion has a fixed `PlayResX` of 384 at a 4:3 aspect. Since libass f08f8ea5
    `PlayResX` affects border and shadow widths, so mpv rewrites it from the display aspect; the
    height stays at libavcodec's 288.
    """
    return round(PLAYRES_Y * space.video_width / max(space.video_height, 1))


def _alignment(style: SubStyle) -> int:
    """`mp_ass_set_style`: `1 + (align_x + 1) + (align_y + 2) % 3 * 4`."""
    return 1 + (style.align_x + 1) + (style.align_y + 2) % 3 * 4


def style_row(style: SubStyle, res_y: float, space: RenderSpace, *, scale: float) -> str:
    """A `Style:` line carrying what `mp_ass_set_style` writes, with mpv's converted-track fixups.

    Two rescalings, and they are not the same one. `mp_ass_set_style` (`ass_mp.c:39`) translates
    every size from the reference `PlayResY` of 720 to this track's; then the converted branch
    rescales the horizontal margins by how far `PlayResX` moved, and the vertical margin by the font
    scale — mpv's own asymmetry (`sd_ass.c:630-635`), not a transcription slip.
    """
    reference = res_y / 720.0
    margin_x = round(style.margin_x * reference)
    margin_v = round((style.margin_y + style.margin_y_offset) * reference)
    fix_margins = play_res_x(space) / PLAYRES_X
    fields = (
        "Default",
        style.font,
        _fmt(style.font_size * reference),
        _color(style.color),
        _color(style.color),  # SecondaryColour is a copy of the primary
        _color(style.outline_color),
        _color(style.back_color),
        str(int(style.bold)),
        str(int(style.italic)),
        "0",  # Underline
        "0",  # StrikeOut
        "100",  # ScaleX, as a percentage of the 1.0 mp_ass_set_style writes
        "100",  # ScaleY
        _fmt(style.spacing * reference),
        "0",  # Angle
        str(style.border_style),
        _fmt(style.outline_size * reference),
        _fmt(style.shadow_offset * reference),
        str(_alignment(style)),
        str(round(margin_x * fix_margins)),
        str(round(margin_x * fix_margins)),
        str(round(margin_v * scale)),
        # `--sub-vsfilter-bidi-compat` off (the default) sweeps every style to Encoding -1
        # (`sd_ass.c:610-613`), which is libass's neutral base direction rather than VSFilter's LTR.
        "-1",
    )
    return "Style: " + ",".join(fields)


def _fmt(value: float) -> str:
    return f"{value:g}"


def _color(color: Color) -> str:
    return f"&H{color.as_ass():08X}"


def document(
    events: str,
    space: RenderSpace,
    *,
    style: SubStyle | None = None,
    scale: float = 1.0,
) -> bytes:
    """The whole document mpv is rendering for a converted track: its header, its style, its events.

    `events` are `Dialogue:` rows. For the cue on screen they come verbatim from
    `sub-text/ass-full`, which is mpv's own conversion — so that part cannot differ from mpv's by
    construction, and only the header around them is reproduced.
    """
    header = LAVC_HEADER.format(
        play_res_x=play_res_x(space),
        style=style_row(style or SubStyle(), PLAYRES_Y, space, scale=scale),
    )
    return (header + events.strip("\n") + "\n").encode()
