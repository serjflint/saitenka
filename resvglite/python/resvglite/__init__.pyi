"""Type stub for the compiled resvglite extension (see src/lib.rs)."""

from collections.abc import Sequence

def render_svg(
    data: bytes,
    size_px: int,
    fonts: Sequence[bytes] | None = None,
    load_system_fonts: bool = False,
) -> tuple[bytes, int, int]:
    """Rasterize SVG ``data`` to a PNG ``size_px`` tall (aspect-preserved). Returns ``(png, w, h)``.

    ``fonts`` is a sequence of font-file bytes (TTF/OTF) loaded so ``<text>`` glyphs render — some gaiji
    (大辞林's 漢/呉 badges) are ``<text>`` SVGs that otherwise rasterize to an empty box. ``load_system_fonts``
    additionally pulls the host's fonts (standalone use). Raises ``ValueError`` on invalid/zero-size SVG or
    a non-positive ``size_px``."""
