"""RGBA → premultiplied-BGRA conversion for mpv's ``overlay-add`` — a pure PIL/numpy leaf.

Lives at the ``overlay`` root (not under ``mpvio``) so both the OSD upload (``mpvio.osd``) and the
windowed compositor (``render.banded``, which converts each band once — #138) can use it without a
``render → mpvio`` import cycle. Same relocation pattern as ``overlay.otel_metrics``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL import Image

# Precomputed premultiply table — _PREMUL_LUT[alpha, value] == value * alpha // 255.
# One flat np.take replaces the uint16 widen+multiply+divide per pixel (~64 KB, fits in L2).
_PREMUL_LUT = (
    np.arange(256, dtype=np.uint16)[:, None] * np.arange(256, dtype=np.uint16)[None, :] // 255
).astype(np.uint8)
_PREMUL_FLAT = np.ascontiguousarray(_PREMUL_LUT.ravel())


def to_bgra_array(img: Image.Image, *, premultiply: bool = True) -> np.ndarray:
    """RGBA image → a contiguous premultiplied **BGRA** array (H, W, 4) for mpv's ``overlay-add``.

    Exposed so callers can convert a tall panel ONCE and then upload scrolled viewport *slices* of it
    without re-converting (fast scrolling). Premultiply is a 256×256 LUT gather (byte-identical to
    the reference ``value * alpha // 255``)."""
    arr = np.asarray(img.convert("RGBA"))
    if premultiply:
        idx = arr[:, :, 3:4].astype(np.uint16) * 256 + arr[:, :, :3]
        rgb = _PREMUL_FLAT.take(idx)
        arr = np.dstack([rgb, arr[:, :, 3]])
    return np.ascontiguousarray(arr[:, :, [2, 1, 0, 3]])


def to_bgra(img: Image.Image, *, premultiply: bool = True) -> tuple[bytes, int, int, int]:
    """Convert an RGBA image to the (data, w, h, stride) mpv's ``overlay-add bgra`` expects."""
    bgra = to_bgra_array(img, premultiply=premultiply)
    return bgra.tobytes(), img.width, img.height, img.width * 4


def scale_bgra(bgra: np.ndarray, scale: float) -> np.ndarray:
    """Bilinear-resize a premultiplied-BGRA viewport by ``scale`` — the tooltip's reference→display
    factor (the panel is composited at the 1920×1080 reference then upscaled to the live OSD at upload).
    Premultiplied alpha is the correct space to interpolate in, so glyph edges stay clean; the per-channel
    resize is agnostic to BGRA-vs-RGBA channel order. Returns a contiguous BGRA array."""
    from PIL import Image

    h, w = bgra.shape[0], bgra.shape[1]
    out = Image.fromarray(bgra).resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BILINEAR
    )
    return np.ascontiguousarray(out)
