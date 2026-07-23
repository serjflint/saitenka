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

from overlay.resources import asset

ASSETS = asset("fonts")  # importlib.resources so the wheel path works too

# Cap on distinct (file, size, weight) FreeTypeFont objects cached per thread. Sizes aren't from a
# small fixed set — ruby text is sized proportionally to its base (render/ruby.py), and structured-
# content nodes carry their own font sizes — so a long session touching varied dict content can
# otherwise accumulate an unbounded number of one-off FreeType faces (a memray leak-flamegraph found
# 308 distinct cached fonts / 172.7 MB retained from a single --stress run touching 33 entries).
_FONT_CACHE_MAX = 64

# Fallback order: JP first (it also carries Latin, so mixed strings stay in one font and look
# consistent), Latin Noto as a secondary. Add more (emoji, symbols) here later.
FONT_FILES: tuple[str, ...] = ("NotoSansJP.ttf", "NotoSans.ttf")


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
