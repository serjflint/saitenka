"""Stage 10: pitch-accent graph — Yomitan's compact morae graph drawn from (reading, downstep)."""

import json
import zipfile
from pathlib import Path

import numpy as np
from util import assert_golden

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


# --- morae splitting -------------------------------------------------------------------------------


def test_morae_splitting():
    from overlay.draw.pitch import morae

    assert morae("ほんめい") == ["ほ", "ん", "め", "い"]
    assert morae("きょう") == ["きょ", "う"]  # small ゃゅょ merge with the head kana
    assert morae("がっこう") == ["が", "っ", "こ", "う"]  # っ is its own mora
    assert morae("コーヒー") == ["コ", "ー", "ヒ", "ー"]  # long-vowel mark is a mora


# --- the graph drawing -----------------------------------------------------------------------------


def test_graph_patterns_heiban_vs_atamadaka_differ():
    from overlay.draw.pitch import render_pitch_graph

    heiban = render_pitch_graph("はし", 0)
    atama = render_pitch_graph("はし", 1)
    assert heiban.size == atama.size  # same morae count → same geometry
    assert (np.asarray(heiban) != np.asarray(atama)).any()  # …but different H/L drawing


def test_graph_width_scales_with_morae():
    from overlay.draw.pitch import render_pitch_graph

    two = render_pitch_graph("はし", 0)
    four = render_pitch_graph("ほんめい", 0)
    assert four.width > two.width


def test_graph_goldens_four_patterns():
    from PIL import Image

    from overlay.draw.pitch import render_pitch_graph

    cases = [
        ("heiban", "ほんめい", 0),  # LHHH, particle stays high
        ("atamadaka", "ほんめい", 1),  # HLLL
        ("nakadaka", "ほんめい", 2),  # LHLL, drop after mora 2
        ("odaka", "ほんめい", 4),  # LHHH, particle drops
    ]
    imgs = [render_pitch_graph(reading, pos) for _, reading, pos in cases]
    # one composite golden: the four canonical accent shapes side by side
    w = sum(i.width for i in imgs) + 12 * (len(imgs) - 1)
    h = max(i.height for i in imgs)
    strip = Image.new("RGBA", (w, h), (252, 252, 250, 255))
    x = 0
    for i in imgs:
        strip.alpha_composite(i, (x, 0))
        x += i.width + 12
    assert_golden(strip, "pitch_graphs.png")
    ARTIFACTS.mkdir(exist_ok=True)
    strip.save(ARTIFACTS / "pitch_graphs.png")  # artifact screenshot (plan Stage 10)


# --- positions threaded through Entry --------------------------------------------------------------


def _pitch_zip(path):
    entries = [["本命", "pitch", {"reading": "ほんめい", "pitches": [{"position": 0}]}]]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "PitchA", "format": 3}))
        zf.writestr("term_meta_bank_1.json", json.dumps(entries, ensure_ascii=False))
    return str(path)


def test_pitch_source_exposes_raw_positions(tmp_path):
    from overlay.app.wordlists import PitchSource

    ps = PitchSource.load(_pitch_zip(tmp_path / "p.zip"))
    got = ps.accents(("本命", "ほんめい"), "ほんめい")
    assert got == ("ほんめい", [0])
    assert ps.accents(("犬",), "いぬ") is None


def test_entry_carries_pitch_accents(tmp_path):
    import zipfile as _zf

    from overlay.app.dictionary import DictionarySet
    from overlay.app.tokenize import Token

    dz = tmp_path / "d.zip"
    with _zf.ZipFile(dz, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "D", "format": 3}))
        zf.writestr(
            "term_bank_1.json", json.dumps([["本命", "ほんめい", "", "", 0, ["favourite"], 1, ""]])
        )
    ds = DictionarySet.load([str(dz)], pitch_paths=[_pitch_zip(tmp_path / "p.zip")])
    tok = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
    e = ds.entry_for(tok)
    assert e.pitches == [("ほんめい", (0,))]
    # …and the purple pill fallback in the freq row is still there
    assert any(f.value == "ほんめい [0]" for f in e.freqs)


def test_panel_renders_pitch_graph_row(tmp_path):
    """An entry WITH pitches renders taller than the same entry without (the graph row), and the
    no-pitch panel is unchanged — existing goldens stay byte-identical."""
    from overlay.panel import Definition, Entry, render_panel

    base = Entry(headword=["本命"], defs=[Definition("D", ["favourite"])])
    with_pitch = Entry(
        headword=["本命"], defs=[Definition("D", ["favourite"])], pitches=[("ほんめい", (0,))]
    )
    a = render_panel(base, width=384)
    b = render_panel(with_pitch, width=384)
    assert b.height > a.height
