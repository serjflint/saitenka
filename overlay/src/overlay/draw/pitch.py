"""Yomitan-style compact pitch-accent graph.

One dot per mora, connected high/low by the downstep number: 0 = heiban (LHH…, particle stays
high), 1 = atamadaka (HLL…), n = nakadaka/odaka (LH…H drop after mora n). A drop-line falls after
the accented mora, and a trailing OPEN dot shows the following particle's pitch — that open dot is
what visually distinguishes heiban [0] from odaka [n].
"""

from __future__ import annotations

import itertools

from PIL import Image, ImageDraw

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
    downstep: int,
    *,
    dot: int = 4,
    step: int = 14,
    height: int = 22,
    color: tuple[int, int, int, int] = PURPLE,
) -> Image.Image:
    """Draw the compact graph for ``reading`` with accent ``downstep`` (premultiplied RGBA)."""
    ms = morae(reading)
    n = max(1, len(ms))
    highs, particle_high = _levels(n, downstep)
    y_hi, y_lo = dot + 2, height - dot - 2
    w = step * n + dot * 2 + 4  # morae dots + the trailing particle dot
    img = Image.new("RGBA", (w, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def xy(i: int, high: bool) -> tuple[int, int]:
        return 4 + i * step, y_hi if high else y_lo

    pts = [xy(i, h) for i, h in enumerate(highs)]
    pts.append(xy(n, particle_high))  # the following-particle dot
    for a, b in itertools.pairwise(pts):  # adjacent dots connected by the H/L contour
        draw.line([a, b], fill=color, width=2)
    for p in pts[:-1]:
        draw.ellipse([p[0] - dot, p[1] - dot, p[0] + dot, p[1] + dot], fill=color)
    px, py = pts[-1]  # particle: open dot
    draw.ellipse([px - dot, py - dot, px + dot, py + dot], outline=color, width=2)
    return img
