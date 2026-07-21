"""Tiny vector icons (the colour-emoji ones the fonts lack): puzzle tag, speaker, arrow.

Deliberately simple — recognisable placeholders, not artwork. Each returns an RGBA sprite.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

RGBA = tuple[int, int, int, int]

GREEN: RGBA = (91, 191, 106, 255)
SPEAKER: RGBA = (90, 90, 90, 255)


def puzzle(size: int, color: RGBA = GREEN) -> Image.Image:
    """A rounded square with two knobs — a puzzle-piece stand-in for grammar-tag bullets."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = round(size * 0.14)
    d.rounded_rectangle([m, m, size - m, size - m], radius=round(size * 0.18), fill=color)
    r = round(size * 0.16)
    cx = size // 2
    d.ellipse([cx - r, 0, cx + r, 2 * r], fill=color)  # top knob
    d.ellipse([size - 2 * r, cx - r, size, cx + r], fill=color)  # right knob
    return img


def plus(size: int, color: RGBA = GREEN) -> Image.Image:
    """A filled circle with a white ``+`` — the add-to-Anki button (Yomitan/SubMiner ⊕)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=color)
    c = (size - 1) / 2
    arm = size * 0.26
    lw = max(2, round(size * 0.13))
    white: RGBA = (255, 255, 255, 255)
    d.line([(c - arm, c), (c + arm, c)], fill=white, width=lw)
    d.line([(c, c - arm), (c, c + arm)], fill=white, width=lw)
    return img


def check(size: int, color: RGBA = GREEN) -> Image.Image:
    """A filled circle with a white ✓ — the 'already mined' state of the add button."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=color)
    lw = max(2, round(size * 0.12))
    white: RGBA = (255, 255, 255, 255)
    d.line(
        [(size * 0.28, size * 0.52), (size * 0.44, size * 0.68), (size * 0.74, size * 0.32)],
        fill=white,
        width=lw,
        joint="curve",
    )
    return img


def cross(size: int, color: RGBA = (150, 150, 150, 255)) -> Image.Image:
    """A thin ✕ — the preview's close button."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(2, round(size * 0.12))
    m = round(size * 0.28)
    d.line([(m, m), (size - m, size - m)], fill=color, width=lw)
    d.line([(size - m, m), (m, size - m)], fill=color, width=lw)
    return img


def speaker(size: int, color: RGBA = SPEAKER) -> Image.Image:
    """A speaker cone with two sound arcs."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = size, size
    # cone: small rectangle (neck) + triangle (flare)
    d.rectangle([w * 0.10, h * 0.38, w * 0.28, h * 0.62], fill=color)
    d.polygon(
        [(w * 0.28, h * 0.38), (w * 0.50, h * 0.20), (w * 0.50, h * 0.80), (w * 0.28, h * 0.62)],
        fill=color,
    )
    lw = max(1, round(size * 0.06))
    d.arc([w * 0.45, h * 0.28, w * 0.80, h * 0.72], start=-55, end=55, fill=color, width=lw)
    d.arc([w * 0.45, h * 0.16, w * 0.98, h * 0.84], start=-50, end=50, fill=color, width=lw)
    return img
