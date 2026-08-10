"""Yomitan-style compact pitch-accent graph.

One dot per mora, connected high/low by the downstep number: 0 = heiban (LHH…, particle stays
high), 1 = atamadaka (HLL…), n = nakadaka/odaka (LH…H drop after mora n). A drop-line falls after
the accented mora, and a trailing OPEN dot shows the following particle's pitch — that open dot is
what visually distinguishes heiban [0] from odaka [n].
"""

from __future__ import annotations

import itertools

from PIL import Image, ImageDraw

from overlay.model import PitchAccent

_SMALL = set("ゃゅょぁぃぅぇぉャュョァィゥェォ")
PURPLE = (126, 96, 168, 255)  # matches the pitch pill hue


def morae(reading: str) -> list[str]:
    """Split kana into morae: small ゃゅょ (and small vowels) merge with the preceding kana; っ and
    ー count as their own morae."""
    out: list[str] = []
    for ch in reading:
        if out and ch in _SMALL:
            out[-1] += ch
        else:
            out.append(ch)
    return out


def _levels(n: int, downstep: int) -> tuple[list[bool], bool]:
    """(per-mora high/low, particle-high) for ``n`` morae with accent ``downstep``."""
    if downstep == 0:  # heiban: low start, rises, STAYS high (incl. the particle)
        return [i > 0 for i in range(n)], True
    if downstep == 1:  # atamadaka: high start, falls immediately
        return [i == 0 for i in range(n)], False
    #  nakadaka / odaka: low start, high through mora ``downstep``, low after
    return [0 < i < downstep for i in range(n)], False


def render_pitch_graph(
    reading: str,
    downstep: int | PitchAccent,
    *,
    dot: int = 4,
    step: int = 14,
    height: int = 22,
    color: tuple[int, int, int, int] = PURPLE,
    scale: float = 1.0,
) -> Image.Image:
    """Draw the compact graph for ``reading`` with accent ``downstep`` (premultiplied RGBA). ``scale``
    multiplies every metric so the graph tracks the tooltip's window-relative UI scale (mpv-style).

    ``downstep`` may be a bare position ``int`` (plain accent dict) or a :class:`PitchAccent` carrying
    NHK/Kanjium per-mora annotations (1-based indices): a devoiced mora's dot is drawn HOLLOW (○,
    Yomitan's voiceless mark) and a nasalised mora carries a small ゜ ring above its dot."""
    devoiced = set(downstep.devoice) if isinstance(downstep, PitchAccent) else set()
    nasalised = set(downstep.nasal) if isinstance(downstep, PitchAccent) else set()
    position = downstep.position if isinstance(downstep, PitchAccent) else downstep
    dot = max(1, round(dot * scale))
    step = max(1, round(step * scale))
    height = max(1, round(height * scale))
    lw = max(1, round(2 * scale))  # dot outline + contour line width
    edge = max(1, round(4 * scale))  # left inset + trailing-dot allowance
    nub = max(1, round(2 * scale))  # radius of the ゜ nasal ring above a mora
    ms = morae(reading)
    n = max(1, len(ms))
    highs, particle_high = _levels(n, position)
    # Only a ゜ needs headroom above the top row; without one the graph is byte-identical to before (so a
    # plain accent dict re-uses the existing golden — no re-bless). Hollow devoice dots reuse the box.
    top_pad = nub * 2 + 1 if nasalised else 0
    y_hi, y_lo = dot + 2 + top_pad, height - dot - 2
    w = step * n + dot * 2 + edge  # morae dots + the trailing particle dot
    img = Image.new("RGBA", (w, height + top_pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def xy(i: int, *, high: bool) -> tuple[int, int]:
        return edge + i * step, y_hi if high else y_lo

    pts = [xy(i, high=h) for i, h in enumerate(highs)]
    pts.append(xy(n, high=particle_high))  # the following-particle dot
    for a, b in itertools.pairwise(pts):  # adjacent dots connected by the H/L contour
        draw.line([a, b], fill=color, width=lw)
    for i, p in enumerate(pts[:-1]):  # mora dots (1-based index i+1 for devoice/nasal)
        box = [p[0] - dot, p[1] - dot, p[0] + dot, p[1] + dot]
        if (i + 1) in devoiced:
            draw.ellipse(box, outline=color, width=lw)  # voiceless → hollow ○
        else:
            draw.ellipse(box, fill=color)
        if (i + 1) in nasalised:  # ゜ ring above the mora
            cy = p[1] - dot - nub - 1
            draw.ellipse([p[0] - nub, cy - nub, p[0] + nub, cy + nub], outline=color, width=lw)
    px, py = pts[-1]  # particle: open dot
    draw.ellipse([px - dot, py - dot, px + dot, py + dot], outline=color, width=lw)
    return img
