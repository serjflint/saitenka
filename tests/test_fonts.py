"""Font loading cache: LRU-bounded per-thread FreeTypeFont cache (fonts.py)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from saitenka import fonts
from saitenka.model import Style
from saitenka.render.layout import Token, _font, draw_token

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextlib.contextmanager
def _telemetry() -> Iterator[None]:
    """Register an in-memory OTel reader so the glyph memo counters can be sampled."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

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


def test_load_cache_is_bounded_and_evicts_the_least_recently_used(monkeypatch):
    _clear_cache()
    monkeypatch.setattr(fonts, "_FONT_CACHE_MAX", 3)
    s0, s1, s2 = (fonts.FontSpec(fonts.FONT_FILES[0], sz) for sz in (10, 11, 12))
    f0 = fonts.load(s0)
    f1 = fonts.load(s1)
    fonts.load(s2)
    assert fonts.load(s0) is f0
    s3 = fonts.FontSpec(fonts.FONT_FILES[0], 13)
    fonts.load(s3)
    assert fonts.load(s0) is f0
    assert fonts.load(s1) is not f1


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


def test_glyph_mask_memoises_colour_variants_and_counts_hits():
    from saitenka.app import telemetry

    fonts._tls.masks = None
    with _telemetry():
        first = _draw_token_img(_cjk_token(), 10.0)
        again = _draw_token_img(_cjk_token(), 10.0)
        red = _draw_token_img(_cjk_token(color=(200, 0, 0, 255)), 10.0)
        sampled = telemetry._sample_counters()
    assert first.tobytes() == again.tobytes()
    assert first.tobytes() != red.tobytes()
    assert sampled.get("glyph_mask.misses", 0) == 1
    assert sampled.get("glyph_mask.hits", 0) == 2


def test_tokenize_picks_a_whole_run_font_that_covers_every_glyph():
    """Regression (French IPA `/ma.ɡa.zɛ̃/`): 'z' is in NotoSansJP but 'ɛ' + the combining tilde are ONLY
    in the Latin NotoSans. Choosing the word font by the first char rendered the IPA glyphs as tofu; the
    whole run must use one font that covers EVERY char. Invariant: every glyph in a token is covered by
    that token's font."""
    from saitenka.render.layout import _tokenize_span

    toks = _tokenize_span("/ma.ɡa.zɛ̃/", Style(size=20))
    for t in toks:
        for ch in t.text:
            if ch.isspace() or ord(ch) < 0x20:
                continue
            assert fonts.covers(t.file, ch), f"{ch!r} U+{ord(ch):04X} not covered by {t.file}"
    # the whole mixed run 'zɛ̃' renders in the one Latin font that covers all of it, not the JP primary
    assert any(t.text == "zɛ̃" and t.file == "NotoSans.ttf" for t in toks)


def test_system_font_tier_drives_rendering_and_missing_glyph_diagnostics(monkeypatch):
    ch = "한"  # outside the vendored JP/Latin subsets
    assert not any(fonts.covers(f, ch) for f in fonts.FONT_FILES)
    monkeypatch.setattr(
        fonts, "_system_font_for_char", lambda c: "/sys/Broad.ttf" if c == ch else None
    )
    assert fonts.font_for_char(ch) == "/sys/Broad.ttf"
    assert fonts.missing_glyphs(ch) == []
    monkeypatch.setattr(fonts, "_system_font_for_char", lambda _c: None)
    assert fonts.font_for_char(ch) == fonts.FONT_FILES[0]
    assert fonts.missing_glyphs(ch) == [ch]


def test_tokenize_keeps_a_single_font_word_whole_for_the_atlas():
    """The common case — an all-one-font word (Latin covered by NotoSansJP) — must stay ONE token, so
    the split doesn't fragment the atlas working set (or shift existing goldens)."""
    from saitenka.render.layout import _tokenize_span

    toks = _tokenize_span("parapluies", Style(size=20))
    assert len(toks) == 1
    assert toks[0].text == "parapluies" and toks[0].file == "NotoSansJP.ttf"


def test_glyph_mask_cache_is_bounded_and_counts_capacity_evictions(monkeypatch):
    # An eviction is a CAPACITY miss (bigger cap would have kept it), distinct from a cold first-see.
    # Over a cap of 4 with 20 distinct glyphs, 16 fall out — the signal for whether raising the cap helps.
    from saitenka.app import telemetry

    monkeypatch.setattr(fonts, "_MASK_CACHE_MAX", 4)
    fonts._tls.masks = None
    font = _font(fonts.FONT_FILES[0], Style(size=24))
    with _telemetry():
        for i in range(20):
            fonts.glyph_mask(font, chr(0x4E00 + i), "L", (0.0, 0.0))
        fonts.glyph_mask(font, chr(0x4E00), "L", (0.0, 0.0))
        fonts.glyph_mask(font, chr(0x4E00 + 19), "L", (0.0, 0.0))
        sampled = telemetry._sample_counters()
    assert sampled.get("glyph_mask.misses", 0) == 21
    assert sampled.get("glyph_mask.hits", 0) == 1
    assert sampled.get("glyph_mask.evictions", 0) == 17


def test_glyph_width_cache_counts_hits_misses_and_capacity_evictions(monkeypatch):
    from saitenka.app import telemetry

    monkeypatch.setattr(fonts, "_WIDTH_CACHE_MAX", 4)
    fonts._tls.widths = None
    font = _font(fonts.FONT_FILES[0], Style(size=24))
    with _telemetry():
        for i in range(20):
            fonts.text_width(font, chr(0x4E00 + i))
        fonts.text_width(font, chr(0x4E00 + 19))
        sampled = telemetry._sample_counters()
    assert sampled.get("glyph_width.misses", 0) == 20
    assert sampled.get("glyph_width.hits", 0) == 1
    assert sampled.get("glyph_width.evictions", 0) == 16


def test_notosans_leads_european_scripts_and_falls_back_for_cjk():
    fonts.set_primary_font("NotoSans.ttf")
    assert fonts.font_for_run("rain") == "NotoSans.ttf"
    assert fonts.font_for_run("дождь") == "NotoSans.ttf"
    assert fonts.primary_font() == "NotoSans.ttf"
    assert fonts.font_for_char("a") == "NotoSans.ttf"
    assert fonts.font_for_char("考") == "NotoSansJP.ttf"


def test_default_font_lead_is_the_universal_jp_font_so_goldens_are_unchanged():
    fonts.set_primary_font(None)
    assert fonts.font_for_run("rain") == "NotoSansJP.ttf"
    assert fonts.primary_font() == "NotoSansJP.ttf"
    assert fonts.font_order() == fonts.FONT_FILES
