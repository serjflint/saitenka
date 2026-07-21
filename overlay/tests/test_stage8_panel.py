"""Stage 8: render_panel() + chrome — reproduce the real 読む popup as the fidelity golden."""

from pathlib import Path

import numpy as np

from overlay.draw.chip import ChipStyle, render_chip
from overlay.panel import load_entry, render_panel
from util import assert_golden

FIX = Path(__file__).resolve().parent / "fixtures"


def test_chip_sprite_hugs_text():
    sp = render_chip("FreqB", ChipStyle(size=20))
    # sprite is snug: not absurdly tall, wider than tall for a 5-char label
    assert sp.height < 40
    assert sp.width > sp.height
    assert 0 < sp.baseline < sp.height


def test_two_tone_freq_pill_is_wider_and_shows_value():
    name = render_chip("FreqA", ChipStyle(size=20))
    pill = render_chip("FreqA", ChipStyle(size=20, value="8912, 143969"))
    assert pill.width > name.width * 1.5  # the value segment is attached (not floating text)
    assert pill.height >= name.height  # one connected pill, a touch taller for legibility


def test_entry_loads():
    e = load_entry(FIX / "yomu.json")
    assert len(e.tags) == 2
    assert len(e.freqs) == 3
    assert len(e.defs) == 2
    assert e.reading_label == ("MonoA", "よむ [1]")


def test_panel_not_blank_and_sized():
    img = render_panel(load_entry(FIX / "yomu.json"), width=384)
    assert img.width == 384
    assert img.height > 600  # the full entry is tall
    # meaningful ink: many non-background pixels
    arr = np.asarray(img.convert("RGB"), np.int16)
    bg = arr[0, 0]
    nonbg = (np.abs(arr - bg).sum(axis=2) > 24).mean()
    assert nonbg > 0.05


def test_panel_yomu_golden():
    img = render_panel(load_entry(FIX / "yomu.json"), width=384)
    assert_golden(img, "panel_yomu.png", tol=2.5)
