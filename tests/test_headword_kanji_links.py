"""Phase B: every headword kanji is a click-to-open link (Yomitan parity). Clicking 読 in the headword
読む navigates the base tooltip IN PLACE to 読's kanji entry (Esc/back returns) — a click must never spawn
a nested popup, which is hover-governed and dismisses itself unless the cursor chases it. The header
produced no hit cells at all before, so clicking a headword kanji was dead. Golden-free: the links are a
pure out-list (ScanBox/LinkBox), never touching pixels, so an existing header golden is byte-identical."""

from __future__ import annotations

import json
import zipfile

import dicthelp
from driver import Driver
from util import FakeIPC

from saitenka.app.session.controller import SessionController
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.subtitles import WordBox
from saitenka.app.tokenize import Token
from saitenka.panel import Definition, Entry, panel_rows
from saitenka.render.banded import WindowedPanel

WIDTH = 384


def _kanji_links(entry: Entry) -> list:
    """The ``kanji:`` LinkBoxes the header emits for ``entry`` — rendered through the real panel so the
    hit rects are the ones a click would land on."""
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport(0, 200)  # render the head (the header row)
    return [lb for lb in wp.link_boxes() if lb.query.startswith("kanji:")]


def test_each_headword_kanji_becomes_a_kanji_link():
    links = _kanji_links(Entry(headword=["勉強"], defs=[Definition("d", ["x"])]))
    assert [lb.query for lb in links] == ["kanji:勉", "kanji:強"]
    for lb in links:
        assert lb.w > 0 and lb.h > 0  # a real glyph rect, not a zero-size box


def test_kana_headword_has_no_kanji_links():
    # only ideographs are linkable — kana/punctuation in the headword yield no box
    assert _kanji_links(Entry(headword=["する"], defs=[Definition("d", ["x"])])) == []


def test_astral_headword_kanji_is_linkable():
    # #99 guard: a supplementary-plane ideograph (𠮟 U+20B9F) must STILL become a kanji link — the
    # BMP-only range checks this path replaces would miss it entirely.
    links = _kanji_links(Entry(headword=["𠮟"], defs=[Definition("d", ["x"])]))
    assert [lb.query for lb in links] == ["kanji:𠮟"]


def _fixture_ds(tmp_path):
    """A minimal Yomitan dict with a term (読む) whose headword kanji 読 is in the kanji bank."""
    kread = ["読", "ドク", "よ.む", "jouyou", ["to read"], {"strokes": "14"}]
    p = tmp_path / "kd.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "KanjiDict", "format": 3}))
        zf.writestr(
            "term_bank_1.json",
            json.dumps([["読む", "よむ", "", "", 0, ["to read"], 1, ""]], ensure_ascii=False),
        )
        zf.writestr("kanji_bank_1.json", json.dumps([kread], ensure_ascii=False))
    return dicthelp.load_set([str(p)])


def test_clicking_a_headword_kanji_opens_its_kanji_entry(monkeypatch, tmp_path):
    r = SessionController(FakeIPC(), dict_set=_fixture_ds(tmp_path))
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("読む", "読む", "よむ", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())
    ui = Driver(r)
    ui.move_to_word(0)  # base tooltip for 読む, through hit-testing rather than a poke

    lb = next(b for b in r.tip.view.state.windowed.link_boxes() if b.query == "kanji:読")
    sx, sy = r.tip.view.xy
    base = r.tip.view.state
    ui.move(sx + lb.x + lb.w / 2, sy + (lb.y - r.tip.view.scroll) + lb.h / 2).click()

    # A click must NEVER spawn a nested popup (hover-governed → self-dismisses unless the cursor chases
    # it); a headword kanji navigates the base tooltip IN PLACE, Yomitan-style.
    assert r.hover_view().nested.state is None
    # Content swapped to 読's kanji entry, previous view pushed for back. len==1 is only reached when
    # kanji_for('読') resolved and installed — a dead/missing kanji would leave the stack empty.
    assert r.tip.view.state is not None and r.tip.view.state is not base
    assert len(r.interaction.tip_nav.back) == 1
    # A navigated view is keyless — not a subtitle token, so scroll won't rebuild it from a token.
    assert r.tip.view.key is None and r.tip.tip_tok is None
    # Reversible: back restores the base 読む tooltip.
    from saitenka.app.features.tooltip import tooltip

    assert tooltip.tip_back(r.tip_ports) is True
    assert r.tip.view.state is base
