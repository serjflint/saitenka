"""Font loading cache: LRU-bounded per-thread FreeTypeFont cache (fonts.py)."""

from __future__ import annotations

from overlay import fonts


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
