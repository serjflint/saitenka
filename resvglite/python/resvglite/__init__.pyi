"""Type stub for the compiled resvglite extension (see src/lib.rs)."""

def render_svg(data: bytes, size_px: int) -> tuple[bytes, int, int]:
    """Rasterize SVG ``data`` to a PNG ``size_px`` tall (aspect-preserved). Returns ``(png, w, h)``.
    Raises ``ValueError`` on invalid/zero-size SVG or a non-positive ``size_px``."""
