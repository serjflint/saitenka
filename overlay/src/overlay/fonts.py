"""Font loading, weight selection, and glyph-coverage fallback.

We vendor Noto Sans JP (variable, covers JP + Latin + all weights) and Noto Sans (Latin) so golden
images are reproducible across machines. A single font would cover "これは test 日本語。", but real
dictionary content mixes rare CJK, symbols, and Latin — so we build an explicit fallback chain and
split any string into runs by the first font that actually has each glyph (no tofu).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import cache

from fontTools.ttLib import TTFont
from PIL import ImageFont

from overlay import otel_metrics
from overlay.resources import asset

ASSETS = asset("fonts")  # importlib.resources so the wheel path works too

# Cap on distinct (file, size, weight) FreeTypeFont objects cached per thread. Sizes aren't from a
# small fixed set — ruby text is sized proportionally to its base (render/ruby.py), and structured-
# content nodes carry their own font sizes — so a long session touching varied dict content can
# otherwise accumulate an unbounded number of one-off FreeType faces (a memray leak-flamegraph found
# 308 distinct cached fonts / 172.7 MB retained from a single --stress run touching 33 entries).
_FONT_CACHE_MAX = 64
_WIDTH_CACHE_MAX = (
    20_000  # (font, text) → getlength memo; bounds the CJK working set + a few styles
)
_MASK_CACHE_MAX = 8192  # (font, text) → getmask2 alpha memo; heavier than widths, so a tighter cap

# Fallback order: JP first (it also carries Latin + most symbols, so mixed strings stay in one font
# and look consistent), Latin Noto as a secondary, monochrome Noto Emoji LAST — it only catches glyphs
# the Noto Sans faces lack (emoji, pictographs) so text is unaffected while icons/emoji stop being tofu.
FONT_FILES: tuple[str, ...] = ("NotoSansJP.ttf", "NotoSans.ttf", "NotoEmoji.ttf")


@dataclass(frozen=True)
class FontSpec:
    """A resolved font request: which vendored file, at what size and weight."""

    file: str
    size: int
    weight: int = 400  # variable-font wght axis (100..900); 400 regular, 700 bold


@cache
def _coverage(file: str) -> frozenset[int]:
    """Set of Unicode codepoints the font file has a glyph for (best cmap)."""
    tt = TTFont(str(ASSETS / file), lazy=True)
    cmap = tt.getBestCmap() or {}
    covered = frozenset(cmap.keys())
    tt.close()
    return covered


_tls = threading.local()  # FreeType faces aren't thread-safe → one font cache per thread


def load(spec: FontSpec) -> ImageFont.FreeTypeFont:
    """A PIL font for the given spec, with the variable weight axis applied. Cached **per thread** so
    the background prefetch workers can render concurrently with the main loop (a shared FreeType face
    used from two threads corrupts/crashes). LRU-bounded (``_FONT_CACHE_MAX``) — a long session touching
    varied structured-content font sizes must not grow this cache without limit."""
    cache: OrderedDict[FontSpec, ImageFont.FreeTypeFont] | None = getattr(_tls, "fonts", None)
    if cache is None:
        cache = _tls.fonts = OrderedDict()
    font = cache.get(spec)
    if font is not None:
        cache.move_to_end(spec)
        return font
    font = ImageFont.truetype(str(ASSETS / spec.file), spec.size)
    try:
        font.set_variation_by_axes([spec.weight])
    except (OSError, AttributeError):
        pass  # not a variable font / no wght axis — use as-is
    cache[spec] = font
    if len(cache) > _FONT_CACHE_MAX:
        cache.popitem(last=False)
    return font


def text_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    """Cached ``font.getlength(text)``. Per thread (a shared cache would race under free-threading;
    FreeType faces are already thread-local, see :func:`load`). Hot: layout measures every CJK char
    individually and the CJK set is small + heavily reused across entries, so this is near-all hits
    after warmup. Keyed on the font *object* (which encodes size+weight but not colour, so colour
    variants share) — a strong ref, so no ``id()`` reuse after eviction. LRU-bounded."""
    cache: OrderedDict[tuple[ImageFont.FreeTypeFont, str], float] | None = getattr(
        _tls, "widths", None
    )
    if cache is None:
        cache = _tls.widths = OrderedDict()
    key = (font, text)
    w = cache.get(key)
    if w is not None:
        cache.move_to_end(key)
        if otel_metrics.glyph_width_hits is not None:
            otel_metrics.glyph_width_hits.add(1)
        return w
    if otel_metrics.glyph_width_misses is not None:
        otel_metrics.glyph_width_misses.add(1)
    w = font.getlength(text)
    cache[key] = w
    if len(cache) > _WIDTH_CACHE_MAX:
        cache.popitem(last=False)
        if otel_metrics.glyph_width_evictions is not None:
            otel_metrics.glyph_width_evictions.add(1)
    return w


def glyph_mask(
    font: ImageFont.FreeTypeFont, text: str, mode: str, start: tuple[float, float]
) -> tuple[object, tuple[int, int]]:
    """Cached ``font.getmask2`` — the glyph's alpha bitmap core + placement offset — reproducing exactly
    the call ``ImageDraw.text`` makes (upright, no stroke), so ``draw_bitmap`` of the result is
    byte-identical to ``draw.text``.

    ``getmask2`` (FreeType rasterisation) is ~half the render CPU and is otherwise recomputed on every
    glyph draw; glyphs repeat massively across words, so a memo is near-all hits after warmup. The mask
    depends on the glyph, the font *object* (which encodes file+size+weight), the render ``mode``, and
    the **subpixel** ``start`` (``draw.text`` passes ``(frac(x), frac(y))`` — CJK's fixed advance keeps
    the phase set small, so the key space stays bounded). Colour is applied by ``draw_bitmap`` at paint
    time, not baked in — so colour variants share. Per thread, like :func:`text_width` (FreeType faces
    are thread-local — a shared cache would race and cross faces). LRU-bounded; the returned core is
    used read-only (``draw_bitmap`` treats it as a stencil)."""
    cache: OrderedDict[tuple, tuple[object, tuple[int, int]]] | None = getattr(_tls, "masks", None)
    if cache is None:
        cache = _tls.masks = OrderedDict()
    key = (font, text, mode, start)
    hit = cache.get(key)
    if hit is not None:
        cache.move_to_end(key)
        if otel_metrics.glyph_mask_hits is not None:
            otel_metrics.glyph_mask_hits.add(1)
        return hit
    if otel_metrics.glyph_mask_misses is not None:
        otel_metrics.glyph_mask_misses.add(1)
    # Positional signature mirrors ImageDraw.text's own getmask2 call (direction/features/language
    # unused here; stroke_width 0; ink 0 — for a non-"RGBA" mode ink only tints an embedded-colour
    # glyph, which this upright text path never has, so the alpha coverage is ink-independent).
    core, offset = font.getmask2(
        text, mode, None, None, None, 0, "ls", 0, start, stroke_filled=True
    )
    hit = (core, offset)
    cache[key] = hit
    if len(cache) > _MASK_CACHE_MAX:
        cache.popitem(last=False)
        if otel_metrics.glyph_mask_evictions is not None:
            otel_metrics.glyph_mask_evictions.add(1)
    return hit


def covers(file: str, ch: str) -> bool:
    return ord(ch) in _coverage(file)


def font_for_char(ch: str) -> str:
    """First vendored file in the fallback chain that has this glyph (falls back to primary)."""
    for f in FONT_FILES:
        if covers(f, ch):
            return f
    return FONT_FILES[0]


@dataclass(frozen=True)
class ShapedRun:
    """A maximal substring that renders from a single font file."""

    text: str
    file: str


def resolve_runs(text: str) -> list[ShapedRun]:
    """Split text into maximal runs, each covered by one font file (fallback resolution)."""
    runs: list[ShapedRun] = []
    for ch in text:
        f = font_for_char(ch)
        if runs and runs[-1].file == f:
            runs[-1] = ShapedRun(runs[-1].text + ch, f)
        else:
            runs.append(ShapedRun(ch, f))
    return runs


def missing_glyphs(text: str) -> list[str]:
    """Characters no vendored font covers (would render as tofu). Excludes whitespace/control."""
    out: list[str] = []
    for ch in text:
        if ch.isspace() or ord(ch) < 0x20:
            continue
        if not any(covers(f, ch) for f in FONT_FILES):
            out.append(ch)
    return out
