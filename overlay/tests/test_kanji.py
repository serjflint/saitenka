"""Stage 9: kanji lookup mode — kanji_bank ingestion, kanji_for, panel golden, `k` dispatch,
single-ideograph scan-cell fallback."""

import json
import zipfile

import dicthelp
from util import FakeIPC, assert_golden

from overlay.app.controller import Reader
from overlay.app.subtitles import WordBox
from overlay.app.tokenize import Token


def _make_dict_zip(path, title, terms=(), kanji=()):
    """A minimal Yomitan v3 zip: term_bank + kanji_bank.

    ``terms``: [term, reading, glossary]; ``kanji``: [char, onyomi, kunyomi, tags, meanings, stats].
    """
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        if terms:
            bank = [[t, r, "", "", 0, g, i + 1, ""] for i, (t, r, g) in enumerate(terms)]
            zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
        if kanji:
            zf.writestr("kanji_bank_1.json", json.dumps(list(kanji), ensure_ascii=False))
    return str(path)


KANJI_READ = [
    "読",
    "ドク トク",
    "よ.む",
    "jouyou",
    ["reading", "to read"],
    {"strokes": "14", "jlpt": "3", "grade": "2"},
]
KANJI_HON = ["本", "ホン", "もと", "jouyou", ["book", "origin", "main"], {"strokes": "5"}]


def _fixture_ds(tmp_path, terms=(("読む", "よむ", ["to read"]),)):
    p = _make_dict_zip(tmp_path / "kd.zip", "KanjiDict", terms=terms, kanji=[KANJI_READ, KANJI_HON])
    return dicthelp.load_set([p])


# --- ingestion -------------------------------------------------------------------------------------


def test_kanji_bank_ingested_into_db(tmp_path):
    p = _make_dict_zip(tmp_path / "k.zip", "K", kanji=[KANJI_READ])
    d = dicthelp.load_dict(p)
    k = d.kanji_lookup("読")
    assert k is not None
    assert k["onyomi"] == "ドク トク"
    assert k["kunyomi"] == "よ.む"
    assert k["meanings"] == ["reading", "to read"]
    assert k["stats"]["strokes"] == "14"
    assert d.kanji_lookup("犬") is None


# --- kanji_for -------------------------------------------------------------------------------------


def test_kanji_for_builds_entry(tmp_path):
    ds = _fixture_ds(tmp_path)
    e = ds.kanji_for("読")
    assert e is not None
    assert e.headword == ["読"]
    # stroke count + stats as pills
    pills = {(f.name, f.value) for f in e.freqs}
    assert ("画数", "14") in pills
    assert any(name == "jlpt" for name, _ in pills)
    # on/kun + meanings in the def body (normal panel path — no new raster code)
    body = json.dumps(e.defs[0].content, ensure_ascii=False)
    assert "ドク トク" in body and "よ.む" in body and "to read" in body
    assert ds.kanji_for("犬") is None


def test_kanji_panel_golden(tmp_path):
    from overlay.panel import render_panel

    ds = _fixture_ds(tmp_path)
    img = render_panel(ds.kanji_for("読"), width=384)
    assert_golden(img, "kanji_panel.png")


# --- `k` key: open / cycle the hovered word's kanji ------------------------------------------------


def _kanji_reader(tmp_path):
    ds = _fixture_ds(tmp_path)
    r = Reader(FakeIPC(), dict_set=ds)
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("読本", "読本", "とくほん", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def test_k_key_opens_first_kanji_and_cycles(monkeypatch, tmp_path):
    r = _kanji_reader(tmp_path)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.hover = 0
    r._handle("saitenka-kanji")
    assert r._nest.state is not None
    assert r._nest.word == "読"  # first kanji of the hovered word
    r._handle("saitenka-kanji")
    assert r._nest.word == "本"  # repeat cycles to the next kanji
    r._handle("saitenka-kanji")
    assert r._nest.word == "読"  # …and wraps around


def test_k_key_bound_globally():
    ipc = FakeIPC()
    Reader(ipc)._register_keybinds()
    binds = {c[1]: c[2] for c in ipc.commands if c and c[0] == "keybind"}
    assert "k" in binds and binds["k"].startswith("script-message ")


def test_k_key_without_kanji_or_hover_is_safe(monkeypatch, tmp_path):
    r = _kanji_reader(tmp_path)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    toasts = []
    monkeypatch.setattr(r, "_toast", lambda text, kind="ok", seconds=2.8: toasts.append(text))
    r._handle("saitenka-kanji")  # nothing hovered → no crash, no popup
    assert r._nest.state is None
    r.tokens = [Token("よむ", "よむ", "よむ", "動詞", 0, 2)]
    r.hover = 0
    r._handle("saitenka-kanji")  # kana-only word → warn toast
    assert r._nest.state is None and toasts


# --- single-ideograph scan cell with no term match falls back to the kanji entry -------------------


def test_scan_cell_click_falls_back_to_kanji(monkeypatch, tmp_path):
    # the def body contains 本 (an ideograph that IS in the kanji bank but the tokenized cell has
    # no useful term context) — clicking its scan cell opens the KANJI entry in the nested popup.
    ds = _fixture_ds(tmp_path, terms=(("読む", "よむ", ["本のことだ。"]),))
    r = Reader(FakeIPC(), dict_set=ds)
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("読む", "読む", "よむ", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    # find the scan cell whose tail starts with 本
    sb = next(b for b in r._tip_state.lazy.scan_boxes if b.text.startswith("本"))
    # make the term lookup miss so the fallback triggers (本 has no term entry in this fixture… it
    # actually might tokenize to 本 with a lemma the dict lacks — force the miss deterministically)
    monkeypatch.setattr(type(ds), "has_term", lambda self, *forms: False)
    sx, sy = r._tip_xy
    ipc = r.ipc
    ipc.props["mouse-pos"] = {
        "hover": True,
        "x": sx + sb.x + sb.w / 2,
        "y": sy + (sb.y - r._tip_scroll) + sb.h / 2,
    }
    r.on_click()
    assert r._nest.state is not None
    assert r._nest.word == "本"  # the kanji entry, via the nested-popup route
    assert r._nest.token is None  # a kanji panel has no minable token
