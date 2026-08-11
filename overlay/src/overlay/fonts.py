"""Font loading, weight selection, and glyph-coverage fallback.

We vendor Noto Sans JP (variable, covers JP + Latin + all weights) and Noto Sans (Latin) so golden
images are reproducible across machines. A single font would cover "これは test 日本語。", but real
dictionary content mixes rare CJK, symbols, and Latin — so we build an explicit fallback chain and
split any string into runs by the first font that actually has each glyph (no tofu).
"""

from __future__ import annotations

import os
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import cache
from pathlib import Path

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

# The numbered stroke-order kanji font (BSD-3, © Ulrich Apel / AAAA / Wadoku). Deliberately NOT in the
# fallback chain — it only renders via an explicit Style.font override (the kanji-panel headword), so
# ordinary CJK text never picks up its diagram glyphs.
STROKE_ORDER_FONT = "KanjiStrokeOrders.ttf"


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

# Persistent glyph mask atlas (#149 Tier-1), opt-in and default-OFF here: a process that enables it
# (set_mask_atlas) gets cross-session getmask2 reuse. _ATLAS_MEM is a SHARED read-only dict (bulk-loaded
# once at startup — free-threading-safe after load) consulted on a per-thread cache miss; _ATLAS_WRITE
# is the store a live miss writes back to. Both None → the hot path is byte-for-byte the pre-atlas path.
_ATLAS_MEM: dict | None = None
_ATLAS_WRITE = None


def set_mask_atlas(mem: dict | None, write) -> None:
    """Install the shared in-memory mask atlas (read) + the write-back store, or clear with ``(None,
    None)``. Called once at startup (see the mask-atlas wiring); the ``mem`` dict is read-only afterward."""
    global _ATLAS_MEM, _ATLAS_WRITE
    _ATLAS_MEM, _ATLAS_WRITE = mem, write


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
    # stable id for the mask atlas key, stamped on the font object (getattr'd back in glyph_mask);
    # setattr, not `font.x =`, so the type checkers don't flag an attr FreeTypeFont doesn't declare.
    setattr(font, "_satk_font_id", f"{spec.file}:{spec.size}:{spec.weight}")  # noqa: B010
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
    gid = getattr(font, "_satk_font_id", None) if (_ATLAS_MEM is not None or _ATLAS_WRITE) else None
    # Persistent atlas HIT (bulk-loaded at startup): reuse the disk mask, no getmask2 this session.
    if gid is not None:
        amask = _atlas_read(gid, text, mode, start)
        if amask is not None:
            cache[key] = amask
            _evict_masks(cache)
            return amask
    # Positional signature mirrors ImageDraw.text's own getmask2 call (direction/features/language
    # unused here; stroke_width 0; ink 0 — for a non-"RGBA" mode ink only tints an embedded-colour
    # glyph, which this upright text path never has, so the alpha coverage is ink-independent).
    core, offset = font.getmask2(
        text, mode, None, None, None, 0, "ls", 0, start, stroke_filled=True
    )
    hit = (core, offset)
    cache[key] = hit
    if gid is not None:
        _atlas_write(gid, text, mode, start, hit)  # persist for a later session (build the atlas)
    _evict_masks(cache)
    return hit


def _atlas_read(gid: str, text: str, mode: str, start: tuple[float, float]):
    """A persistent-atlas lookup — the mask, or None if the atlas is off / misses. Counts the atlas
    hit:miss ratio (the "was the prewarm worth it?" telemetry). Two sources: a bulk-loaded ``_ATLAS_MEM``
    dict (prewarm/tests) if present, else a LAZY per-glyph ``get_one`` against the write-back atlas — the
    per-thread ``glyph_mask`` LRU fronts this, so a session queries each glyph at most once per thread and
    never bulk-loads the whole atlas into RAM."""
    if _ATLAS_MEM is not None:
        from overlay.mask_atlas import mem_key

        amask = _ATLAS_MEM.get(mem_key(gid, text, mode, start))
    elif _ATLAS_WRITE is not None:
        amask = _ATLAS_WRITE.get_one(gid, text, mode, start)
    else:
        return None
    if amask is not None:
        if otel_metrics.mask_atlas_hits is not None:
            otel_metrics.mask_atlas_hits.add(1)
        return amask
    if otel_metrics.mask_atlas_misses is not None:
        otel_metrics.mask_atlas_misses.add(1)  # atlas on, but this glyph/phase wasn't stored
    return None


def _atlas_write(gid: str, text: str, mode: str, start: tuple[float, float], hit) -> None:
    """Persist a freshly rasterised mask to the write-back atlas (builds it for a later session)."""
    if _ATLAS_WRITE is None:
        return
    _ATLAS_WRITE.put(gid, text, mode, start, hit)
    if otel_metrics.mask_atlas_writebacks is not None:
        otel_metrics.mask_atlas_writebacks.add(1)


def _evict_masks(cache: OrderedDict) -> None:
    if len(cache) > _MASK_CACHE_MAX:
        cache.popitem(last=False)
        if otel_metrics.glyph_mask_evictions is not None:
            otel_metrics.glyph_mask_evictions.add(1)


def covers(file: str, ch: str) -> bool:
    return ord(ch) in _coverage(file)


# Best-effort system-font tier (appended AFTER the vendored chain). Goldens are built against the
# EMBEDDED fonts only — deterministic across machines — so they never reach this tier (their content is
# vendored-covered). Everything else (an exotic script / rare IPA the subset lacks) is best-effort: we
# consult the OS's own fonts so a real glyph renders instead of tofu, accepting that the exact pixels
# then depend on the machine. Empty on a box with none of these dirs → behaviour is exactly the old
# vendored-only path.
def _system_font_dirs() -> tuple[Path, ...]:
    home = Path.home()
    plat = str(
        sys.platform
    )  # via a local so the type-checker (pinned platform) keeps all branches live
    if plat == "darwin":
        return (
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
            home / "Library/Fonts",
        )
    if plat.startswith("win"):
        return (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",)
    return (
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        home / ".local/share/fonts",
        home / ".fonts",
    )


@cache
def _system_font_files() -> tuple[str, ...]:
    """Discovered OS font files (``.ttf``/``.otf`` only — ``.ttc`` collections are skipped: cmap probing
    a face index is unreliable). Broad-coverage families (Arial Unicode, Noto, DejaVu, *Symbol*) are
    tried first so a lookup usually matches on the first probe. Enumerated once, cached for the session."""
    found: list[str] = []
    for d in _system_font_dirs():
        if not d.is_dir():
            continue
        try:
            found.extend(str(p) for p in d.rglob("*") if p.suffix.lower() in {".ttf", ".otf"})
        except OSError:
            continue

    def _priority(path: str) -> int:
        name = path.rsplit("/", 1)[-1].lower()
        broad = ("arial unicode", "notosans", "noto sans", "dejavusans", "symbol", "unifont")
        return 0 if any(b in name for b in broad) else 1

    return tuple(sorted(set(found), key=lambda p: (_priority(p), p)))


@cache
def _system_font_for_char(ch: str) -> str | None:
    """First OS font (by :func:`_system_font_files` order) whose cmap has ``ch``, or ``None``. Cached per
    char — only ever consulted for a glyph NO vendored font covers, so it's off the hot path."""
    cp = ord(ch)
    for f in _system_font_files():
        try:
            if cp in _coverage(f):
                return f
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable/odd font — skip, best-effort
    return None


# The vendored fallback chain LEADS with the font best suited to the active profile's primary script:
# NotoSans for European scripts (crisp Latin/Cyrillic/Greek letterforms + a proper word space), the
# universal NotoSansJP for Japanese — and always as the trailing fallback, so a glyph the lead lacks
# (CJK in a French gloss) still resolves. A read-mostly module VALUE (a font file, not a Latin/non-Latin
# bit — Cyrillic/Greek lead with NotoSans too), set once per profile activation by the reader from its
# language. Unset → the JP-universal default, so JP renders — and every JP golden — stay byte-identical.
_DEFAULT_PRIMARY = FONT_FILES[0]  # NotoSansJP — universal coverage
_active_primary: str = _DEFAULT_PRIMARY


def set_primary_font(file: str | None) -> None:
    """Set the vendored font that LEADS the fallback chain (``None`` → the JP-universal default). The
    reader chooses it from the active profile's script (:func:`overlay.app.profiles.primary_font_for`);
    glyphs the lead lacks still fall through the rest of the chain."""
    global _active_primary
    _active_primary = file or _DEFAULT_PRIMARY


def primary_font() -> str:
    """The chain's lead = the default/space font (replaces bare ``FONT_FILES[0]`` at the
    space/newline/empty-line sites so those track the active script too)."""
    return _active_primary


def font_order() -> tuple[str, ...]:
    """The fallback chain, led by :func:`primary_font` with the rest trailing (NotoSansJP always trails
    when it isn't the lead, so CJK still resolves). Equals ``FONT_FILES`` under the JP default."""
    if _active_primary == _DEFAULT_PRIMARY:
        return FONT_FILES
    return (_active_primary, *(f for f in FONT_FILES if f != _active_primary))


def font_for_char(ch: str) -> str:
    """The font file that renders this glyph: first the vendored fallback chain (deterministic), then a
    best-effort OS-font tier for a glyph none of the vendored subsets carry. Falls back to the vendored
    primary (tofu) only when even the system has nothing."""
    for f in font_order():
        if covers(f, ch):
            return f
    return _system_font_for_char(ch) or primary_font()


def _covers_all(file: str, text: str) -> bool:
    cov = _coverage(file)
    return all(ord(c) in cov for c in text)


def _system_font_covering(text: str) -> str | None:
    """First OS font whose cmap has EVERY char of ``text`` (a whole word run in an exotic script), or
    ``None``. Not cached (word strings are unbounded) — only reached for a run no vendored font covers,
    and ``_coverage`` per file is cached, so cost is bounded by the system-font count."""
    for f in _system_font_files():
        try:
            if _covers_all(f, text):
                return f
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def font_for_run(text: str) -> str:
    """The single font for a whole word run: the first in the vendored chain covering EVERY char — so the
    word renders in ONE consistent font and is byte-identical to the pre-split path when a vendored font
    covers it all — then a best-effort system font covering the whole run, else the vendored primary.
    Fixes the tofu where a word's FIRST char resolved to a font that lacks a LATER glyph (a Latin 'z' +
    an IPA 'ɛ' the Latin Noto has but the JP one doesn't) WITHOUT fragmenting a coverable word."""
    for f in font_order():
        if _covers_all(f, text):
            return f
    return _system_font_covering(text) or primary_font()


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
    """Characters that would truly render as tofu — no vendored font AND no best-effort system font
    covers them. Excludes whitespace/control. (Genuine tofu, so it accounts for the OS-font tier.)"""
    out: list[str] = []
    for ch in text:
        if ch.isspace() or ord(ch) < 0x20:
            continue
        if not any(covers(f, ch) for f in FONT_FILES) and _system_font_for_char(ch) is None:
            out.append(ch)
    return out
