"""Font loading cache: LRU-bounded per-thread FreeTypeFont cache (fonts.py)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from overlay import fonts
from overlay.model import Style
from overlay.render.layout import Token, _font, draw_token

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextlib.contextmanager
def _telemetry() -> Iterator[None]:
    """Register an in-memory OTel reader so the glyph memo counters can be sampled."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from overlay import otel_metrics

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        yield
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def _clear_cache():
    fonts._tls.fonts = None


def test_load_reuses_cached_font_for_same_spec():
    _clear_cache()
    spec = fonts.FontSpec(fonts.FONT_FILES[0], 24)
    a = fonts.load(spec)
    b = fonts.load(spec)
    assert a is b


def test_load_cache_is_bounded():
    """A long session touching many distinct sizes (e.g. proportionally-scaled ruby text, or
    structured-content nodes with their own font sizes) must not grow the cache without limit."""
    _clear_cache()
    for size in range(fonts._FONT_CACHE_MAX + 20):
        fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], 10 + size))
    assert len(fonts._tls.fonts) == fonts._FONT_CACHE_MAX


def test_load_cache_evicts_oldest_not_most_recently_used(monkeypatch):
    _clear_cache()
    monkeypatch.setattr(fonts, "_FONT_CACHE_MAX", 3)
    s0, s1, s2 = (fonts.FontSpec(fonts.FONT_FILES[0], sz) for sz in (10, 11, 12))
    fonts.load(s0)
    fonts.load(s1)
    fonts.load(s2)
    fonts.load(s0)  # touch s0 again → s1 becomes the least-recently-used
    s3 = fonts.FontSpec(fonts.FONT_FILES[0], 13)
    fonts.load(s3)  # forces an eviction
    assert s1 not in fonts._tls.fonts  # LRU, not FIFO by insertion order
    assert s0 in fonts._tls.fonts
    assert len(fonts._tls.fonts) == 3


# --- glyph mask cache (getmask2 memo behind draw_token) ------------------------------------------


def _cjk_token(ch: str = "考", color=(230, 230, 230, 255)) -> Token:
    return Token(ch, fonts.FONT_FILES[0], "cjk", 0.0, Style(size=24, color=color))


def _draw_token_img(tok: Token, x: float, baseline: float = 30.0) -> Image.Image:
    img = Image.new("RGBA", (80, 50), (0, 0, 0, 0))
    draw_token(img, ImageDraw.Draw(img), x, baseline, tok)
    return img


def _draw_text_img(tok: Token, x: float, baseline: float = 30.0) -> Image.Image:
    """The pre-split reference: raw draw.text, the golden-source primitive draw_token replaced."""
    img = Image.new("RGBA", (80, 50), (0, 0, 0, 0))
    ImageDraw.Draw(img).text(
        (x, baseline), tok.text, font=_font(tok.file, tok.style), fill=tok.style.color, anchor="ls"
    )
    return img


def test_draw_token_is_byte_identical_to_draw_text_across_subpixel_phases():
    """The atlas split must reproduce draw.text exactly — at integer AND fractional pens (draw.text
    bakes the subpixel phase into the mask), or goldens/windowed bands drift."""
    fonts._tls.masks = None
    tok = _cjk_token()
    for x in (10.0, 10.3, 10.5, 10.7, 11.9):
        assert _draw_token_img(tok, x).tobytes() == _draw_text_img(tok, x).tobytes()


def test_glyph_mask_memoises_and_colour_variants_share_the_key():
    fonts._tls.masks = None
    _draw_token_img(_cjk_token(), 10.0)
    n_after_first = len(fonts._tls.masks)
    _draw_token_img(_cjk_token(), 10.0)  # same glyph+phase → hit, no growth
    _draw_token_img(
        _cjk_token(color=(200, 0, 0, 255)), 10.0
    )  # colour is applied at blit → same key
    assert len(fonts._tls.masks) == n_after_first == 1


def test_glyph_mask_counters_record_miss_then_hit():
    from overlay.app import telemetry

    fonts._tls.masks = None
    font = _font(fonts.FONT_FILES[0], Style(size=24))
    with _telemetry():
        fonts.glyph_mask(font, "考", "L", (0.0, 0.0))  # miss (rasters + populates)
        fonts.glyph_mask(font, "考", "L", (0.0, 0.0))  # hit
        sampled = telemetry._sample_counters()
    assert sampled.get("glyph_mask.misses", 0) >= 1
    assert sampled.get("glyph_mask.hits", 0) >= 1


def test_glyph_width_counters_record_miss_then_hit():
    from overlay.app import telemetry

    fonts._tls.widths = None
    font = _font(fonts.FONT_FILES[0], Style(size=24))
    with _telemetry():
        fonts.text_width(font, "考")  # miss (measures + populates)
        fonts.text_width(font, "考")  # hit
        sampled = telemetry._sample_counters()
    assert sampled.get("glyph_width.misses", 0) >= 1
    assert sampled.get("glyph_width.hits", 0) >= 1


def test_glyph_mask_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(fonts, "_MASK_CACHE_MAX", 4)
    fonts._tls.masks = None
    for i in range(20):
        fonts.glyph_mask(
            _font(fonts.FONT_FILES[0], Style(size=24)), chr(0x4E00 + i), "L", (0.0, 0.0)
        )
    assert len(fonts._tls.masks) == 4
