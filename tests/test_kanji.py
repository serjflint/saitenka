"""Stage 9: kanji lookup mode — kanji_bank ingestion, kanji_for, panel golden, `k` dispatch,
single-ideograph scan-cell fallback."""

import json
import zipfile

import dicthelp
from driver import Driver
from saitenka_tokenize.japanese import Token
from session_builder import build_session
from util import FakeIPC, assert_golden, keybind_registry

from saitenka.app.session.factory import SessionServices
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.subtitles import WordBox


def _make_dict_zip(path, title, terms=(), kanji=(), tags=()):
    """A minimal Yomitan v3 zip: term_bank + kanji_bank + tag_bank.

    ``terms``: [term, reading, glossary]; ``kanji``: [char, onyomi, kunyomi, tags, meanings, stats];
    ``tags``: [code, category, order, notes, score] — labels + sections the kanji-panel stats.
    """
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        if terms:
            bank = [[t, r, "", "", 0, g, i + 1, ""] for i, (t, r, g) in enumerate(terms)]
            zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
        if kanji:
            zf.writestr("kanji_bank_1.json", json.dumps(list(kanji), ensure_ascii=False))
        if tags:
            zf.writestr("tag_bank_1.json", json.dumps(list(tags), ensure_ascii=False))
    return str(path)


# A trimmed KANJIDIC tag_bank: the four stat categories Yomitan sections by (misc/class/code/index).
KANJI_TAGS = [
    ["strokes", "misc", 0, "Stroke count", 0],
    ["grade", "misc", 0, "Grade level", 0],
    ["jlpt", "misc", 0, "JLPT level", 0],
    ["skip", "class", 0, "SKIP code", 0],
    ["ucs", "code", 0, "Unicode hex code", 0],
    ["moro", "index", 0, "Daikanwajiten", 0],
]


KANJI_READ = [
    "読",
    "ドク トク",
    "よ.む",
    "jouyou",
    ["reading", "to read"],
    {"strokes": "14", "jlpt": "3", "grade": "2", "skip": "1-7-7", "ucs": "8aad", "moro": "35244"},
]
KANJI_HON = ["本", "ホン", "もと", "jouyou", ["book", "origin", "main"], {"strokes": "5"}]


def _fixture_ds(tmp_path, terms=(("読む", "よむ", ["to read"]),)):
    p = _make_dict_zip(
        tmp_path / "kd.zip",
        "KanjiDict",
        terms=terms,
        kanji=[KANJI_READ, KANJI_HON],
        tags=KANJI_TAGS,
    )
    return dicthelp.load_set([p])


# --- ingestion -------------------------------------------------------------------------------------


def test_kanji_bank_ingested_into_db(tmp_path):
    """The kanji_bank round-trips through import: readings, meanings and KANJIDIC stats all come back
    out, and a character the bank never held stays absent."""
    ds = dicthelp.load_set([_make_dict_zip(tmp_path / "k.zip", "K", kanji=[KANJI_READ])])

    entry = ds.kanji_for("読")

    assert entry is not None
    dumped = json.dumps(entry.defs[0].content, ensure_ascii=False)
    assert "ドク トク" in dumped and "よ.む" in dumped
    assert "reading" in dumped and "to read" in dumped
    assert "14" in dumped  # the strokes stat
    assert ds.kanji_for("犬") is None


# --- kanji_for -------------------------------------------------------------------------------------


def test_kanji_for_builds_entry(tmp_path):
    ds = _fixture_ds(tmp_path)
    e = ds.kanji_for("読")
    assert e is not None
    assert e.headword == ["読"]
    # Kanji stats are labeled + sectioned in the def body (Yomitan parity), not green pills.
    assert e.freqs == []
    body = json.dumps(e.defs[0].content, ensure_ascii=False)
    # on/kun + meanings (normal panel path — no new raster code)
    assert "ドク トク" in body and "よ.む" in body and "to read" in body
    # stats rendered under their tag_bank section titles + human labels, not bare codes
    assert "Statistics" in body and "Stroke count" in body and "JLPT level" in body
    assert "Codepoints" in body and "Unicode hex code" in body
    assert ds.kanji_for("犬") is None


def test_kanji_stats_fall_back_to_codes_without_a_tag_bank(tmp_path):
    # A pre-#310 DB has no category/notes → every stat still renders (flat, keyed by its bare code),
    # rather than the old truncated 6-pill dump dropping data.
    p = _make_dict_zip(tmp_path / "notags.zip", "NoTags", kanji=[KANJI_READ])
    ds = dicthelp.load_set([p])
    e = ds.kanji_for("読")
    assert e is not None and e.freqs == []
    body = json.dumps(e.defs[0].content, ensure_ascii=False)
    assert "strokes" in body and "14" in body and "jlpt" in body  # bare codes, all present


def test_kanji_panel_golden(tmp_path):
    from saitenka.panel import render_panel

    ds = _fixture_ds(tmp_path)
    img = render_panel(ds.kanji_for("読"), width=384)
    assert_golden(img, "kanji_panel.png")


# --- stroke-order headword font (Part B) -----------------------------------------------------------


def test_stroke_order_toggle_sets_the_headword_font(tmp_path):
    from saitenka.fonts import STROKE_ORDER_FONT

    ds = _fixture_ds(tmp_path)
    assert ds.kanji_for("読", stroke_order=True).headword_font == STROKE_ORDER_FONT
    assert ds.kanji_for("読", stroke_order=False).headword_font is None
    assert ds.kanji_for("読").headword_font is None  # default off — opt-in via the toggle


def test_stroke_order_headword_renders_in_the_stroke_font(tmp_path):
    # Metamorphic oracle (no pixel golden): the big headword glyph resolves to the stroke-order face
    # when the toggle is on, and to the normal CJK chain when off — a negative control that bites.
    from saitenka.fonts import STROKE_ORDER_FONT
    from saitenka.model import Style
    from saitenka.render import layout
    from saitenka.render.sc_adapter import inline_flow

    ds = _fixture_ds(tmp_path)
    on = ds.kanji_for("読", stroke_order=True)
    flow_on = inline_flow(on.headword, Style(size=46, weight=700, font=on.headword_font))
    files_on = {t.file for t in layout.tokenize_rich(flow_on)}
    assert files_on == {STROKE_ORDER_FONT}

    off = ds.kanji_for("読", stroke_order=False)
    flow_off = inline_flow(off.headword, Style(size=46, weight=700, font=off.headword_font))
    assert STROKE_ORDER_FONT not in {t.file for t in layout.tokenize_rich(flow_off)}


def test_forced_font_falls_back_when_it_lacks_the_glyph():
    # A forced font that doesn't cover the char must NOT introduce tofu — it falls through to the chain.
    from saitenka.model import Span, Style
    from saitenka.render import layout

    toks = layout.tokenize_rich([Span("鳥", Style(size=46, font="NotoEmoji.ttf"))])
    assert all(t.file != "NotoEmoji.ttf" for t in toks)  # NotoEmoji lacks 鳥 → resolved elsewhere


def test_stroke_order_defaults_on_and_threads_to_the_reader(tmp_path):
    from dataclasses import replace

    from saitenka.app.config import ReaderOptions, TooltipOptions

    assert TooltipOptions().kanji_stroke_order is True  # on by default
    r = build_session(FakeIPC(), services=SessionServices(dictionaries=_fixture_ds(tmp_path)))
    assert r.graph.tooltip.visual.stroke_order is True
    off_opts = replace(ReaderOptions(), tooltip=TooltipOptions(kanji_stroke_order=False))
    off = build_session(
        FakeIPC(),
        services=SessionServices(
            dictionaries=_fixture_ds(tmp_path),
        ),
        options=off_opts,
    )
    assert off.graph.tooltip.visual.stroke_order is False


# --- `k` key: open / cycle the hovered word's kanji ------------------------------------------------


def _kanji_reader(tmp_path):
    ds = _fixture_ds(tmp_path)
    r = build_session(FakeIPC(), services=SessionServices(dictionaries=ds))
    r.graph.screen.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.graph.subtitle_presentation.cue.replace_geometry(origin=(0, 0))
    r.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("読本", "読本", "とくほん", "名詞", 0, 2)]
    )
    r.graph.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 100, 300, 40, 40)])
    return r


def test_k_key_opens_first_kanji_and_cycles(monkeypatch, tmp_path):
    r = _kanji_reader(tmp_path)
    monkeypatch.setattr(r.graph.subtitle_presentation, "renderer", NullRenderer())
    r.graph.tooltip.select(0)
    r.command("saitenka-kanji")
    assert r.graph.tooltip.hover_view().nested.shown
    assert r.graph.tooltip.hover_view().nested.word == "読"  # first kanji of the hovered word
    r.command("saitenka-kanji")
    assert r.graph.tooltip.hover_view().nested.word == "本"  # repeat cycles to the next kanji
    r.command("saitenka-kanji")
    assert r.graph.tooltip.hover_view().nested.word == "読"  # …and wraps around


def test_k_key_bound_globally():
    ipc = FakeIPC()
    build_session(ipc).graph.commands.install_input()
    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}
    assert "k" in binds and binds["k"].startswith("script-message ")


def test_k_key_without_kanji_or_hover_is_safe(monkeypatch, tmp_path):
    r = _kanji_reader(tmp_path)
    monkeypatch.setattr(r.graph.subtitle_presentation, "renderer", NullRenderer())
    toasts = []
    monkeypatch.setattr(
        r.graph.notifications, "show", lambda text, _kind="ok", _seconds=2.8: toasts.append(text)
    )
    r.command("saitenka-kanji")  # nothing hovered → no crash, no popup
    assert not r.graph.tooltip.hover_view().nested.shown
    r.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("よむ", "よむ", "よむ", "動詞", 0, 2)]
    )
    r.graph.tooltip.select(0)
    r.command("saitenka-kanji")  # kana-only word → warn toast
    assert not r.graph.tooltip.hover_view().nested.shown and toasts


# --- single-ideograph scan cell with no term match falls back to the kanji entry -------------------


def test_scan_cell_click_falls_back_to_kanji(monkeypatch, tmp_path):
    # the def body contains 本 (an ideograph that IS in the kanji bank but the tokenized cell has
    # no useful term context) — clicking its scan cell opens the KANJI entry in the nested popup.
    ds = _fixture_ds(tmp_path, terms=(("読む", "よむ", ["本のことだ。"]),))
    r = build_session(FakeIPC(), services=SessionServices(dictionaries=ds))
    r.graph.screen.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.graph.subtitle_presentation.cue.replace_geometry(origin=(0, 0))
    r.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("読む", "読む", "よむ", "動詞", 0, 2)]
    )
    r.graph.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 100, 300, 40, 40)])
    monkeypatch.setattr(r.graph.subtitle_presentation, "renderer", NullRenderer())
    ui = Driver(r)
    ui.move_to_word(0)  # base tooltip through hit-testing, not a poke
    # find the scan cell whose tail starts with 本
    sb = next(
        b
        for b in r.graph.tooltip.surface_state().view.state.windowed.scan_boxes()
        if b.text.startswith("本")
    )
    # make the term lookup miss so the fallback triggers (本 has no term entry in this fixture… it
    # actually might tokenize to 本 with a lemma the dict lacks — force the miss deterministically)
    monkeypatch.setattr(type(ds), "has_term", lambda _self, *_forms: False)
    sx, sy = r.graph.tooltip.surface_state().view.xy
    ui.move(
        sx + sb.x + sb.w / 2,
        sy + (sb.y - r.graph.tooltip.surface_state().view.scroll) + sb.h / 2,
    ).click()
    assert r.graph.tooltip.hover_view().nested.shown
    assert (
        r.graph.tooltip.hover_view().nested.word == "本"
    )  # the kanji entry, via the nested-popup route
    assert not r.graph.tooltip.hover_view().nested.has_token  # a kanji panel has no minable token
