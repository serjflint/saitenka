"""Stage 11: per-dictionary tabs (sticky row, click → scroll to section, active tracks scroll)
+ tooltip-scoped keyboard nav (LEFT/RIGHT sections, UP/DOWN scroll, ESC close) with strict
register/unregister pairing — a leaked bind would steal mpv's arrows."""

from util import FakeIPC

from overlay.app.controller import Reader
from overlay.app.subtitles import WordBox
from overlay.app.tokenize import Token
from overlay.panel import Definition, Entry, LazyPanel, panel_rows

WIDTH = 384

TIP_KEYS = {"LEFT", "RIGHT", "UP", "DOWN", "ESC"}


class _MultiDS:
    """Three dictionary sections, each tall enough to scroll to."""

    def entry_for(self, tok, inflected=None):
        para = "とても長い定義の本文でありスクロールが必要になるほど縦に伸びます。" * 4
        return Entry(
            headword=[tok.surface],
            reading="ほんめい",
            defs=[Definition(name, [para]) for name in ("MonoC", "MonoB", "MonoD")],
        )


def _reader(ds=None):
    r = Reader(FakeIPC(), dict_set=ds or _MultiDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


# --- section offsets from panel composition --------------------------------------------------------


def test_rows_carry_section_names_and_offsets():
    e = _MultiDS().entry_for(Token("本命", "本命", "ほんめい", "名詞", 0, 2))
    rows = panel_rows(e, WIDTH)
    sections = [r.section for r in rows if r.section]
    assert sections == ["MonoC", "MonoB", "MonoD"]
    lp = LazyPanel(rows, WIDTH)
    lp.finish()
    offs = lp.section_offsets()
    assert [name for name, _ in offs] == ["MonoC", "MonoB", "MonoD"]
    ys = [y for _, y in offs]
    assert ys == sorted(ys) and ys[0] > 0  # increasing, below the header


# --- the tab row -----------------------------------------------------------------------------------


def test_render_tab_row_chips_and_rects():
    import numpy as np

    from overlay.panel import render_tab_row

    img0, rects0 = render_tab_row(["MonoC", "MonoB", "MonoD"], active=0, width=WIDTH)
    img1, _rects1 = render_tab_row(["MonoC", "MonoB", "MonoD"], active=1, width=WIDTH)
    assert len(rects0) == 3
    assert img0.width == WIDTH
    xs = [r[0] for r in rects0]
    assert xs == sorted(xs)  # chips laid out left→right
    assert (np.asarray(img0) != np.asarray(img1)).any()  # active chip is highlighted


# --- tabs on the tooltip: click → scroll target, active tracks scroll ------------------------------


def _shown(monkeypatch, r):
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    return r


def test_tooltip_shows_tabs_for_multi_dict_entry(monkeypatch):
    r = _shown(monkeypatch, _reader())
    assert len(r._tab_rects) == 3  # one clickable chip per dictionary
    assert len(r._tab_offsets) == 3


def test_single_dict_entry_has_no_tabs(monkeypatch):
    class _OneDS:
        def entry_for(self, tok, inflected=None):
            return Entry(headword=[tok.surface], defs=[Definition("MonoC", ["短い。"])])

    r = _shown(monkeypatch, _reader(_OneDS()))
    assert r._tab_rects == []


def test_multi_dict_reserves_space_so_header_clears_the_tab_strip(monkeypatch):
    # Regression: the sticky tab strip must NOT cover the reading / ⊕ / 🔊. Multi-dict entries
    # reserve the strip's height above the header so those sit below it.
    from overlay.panel import header_add_rect, header_speaker_rect, tab_row_height

    r = _shown(monkeypatch, _reader())  # 3 dicts → tabs
    reserve = r._tip_reserve()
    assert reserve >= tab_row_height()  # space reserved for the strip
    for rect in (
        header_add_rect(r.tip_width, top_reserve=reserve),
        header_speaker_rect(r.tip_width, top_reserve=reserve),
    ):
        assert rect[1] >= tab_row_height()  # icon's panel-y is below the strip → not covered


def test_single_dict_reserves_nothing(monkeypatch):
    class _OneDS:
        def entry_for(self, tok, inflected=None):
            return Entry(headword=[tok.surface], defs=[Definition("MonoC", ["短い。"])])

    r = _shown(monkeypatch, _reader(_OneDS()))
    assert r._tip_reserve() == 0  # no tabs → no wasted top space


def test_tab_click_scrolls_to_section(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc, dict_set=_MultiDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    _shown(monkeypatch, r)
    assert r._tip_scroll == 0
    x, y, w, h = r._tab_rects[2]  # click the LAST dict's chip
    ipc.props["mouse-pos"] = {"hover": True, "x": x + w / 2, "y": y + h / 2}
    r.on_click()
    assert r._tip_scroll > 0  # viewport jumped…
    third = r._tab_offsets[2]
    assert abs(r._tip_scroll - max(0, third - r._tab_h)) <= 1  # …to the third section


def test_active_tab_tracks_scroll(monkeypatch):
    r = _shown(monkeypatch, _reader())
    assert r._active_section() == 0
    r._tip_scroll = r._tab_offsets[1] + 2
    assert r._active_section() == 1
    r._tip_scroll = r._tab_offsets[2] + 2
    assert r._active_section() == 2


# --- tooltip-scoped keyboard: register/unregister pairing ------------------------------------------


def _tip_binds(ipc):
    """(bound, unbound) key sets from the recorded keybind commands for the tooltip keys."""
    bound, unbound = [], []
    for c in ipc.commands:
        if c and c[0] == "keybind" and c[1] in TIP_KEYS:
            (unbound if c[2] == "" else bound).append(c[1])
    return bound, unbound


def test_tip_keys_bind_on_show_and_unbind_on_hide(monkeypatch):
    ipc = FakeIPC()
    r = _reader()
    r.ipc = ipc
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    bound, unbound = _tip_binds(ipc)
    assert set(bound) == TIP_KEYS  # registered on show, single-string commands
    assert unbound == []
    for c in ipc.commands:  # every bind uses the one-string convention
        if c and c[0] == "keybind" and c[1] in TIP_KEYS and c[2]:
            assert c[2].startswith("script-message ")
    r.set_hover(-1)
    bound, unbound = _tip_binds(ipc)
    assert set(unbound) == TIP_KEYS  # released on hide — mpv gets its arrows back


def test_tip_keys_unbind_on_cue_change_no_leak(monkeypatch):
    ipc = FakeIPC()
    r = _reader()
    r.ipc = ipc
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    r.set_subtitle("別の字幕です")  # cue change while the tooltip is up
    bound, unbound = _tip_binds(ipc)
    assert sorted(bound) == sorted(unbound)  # strict pairing: nothing leaks


def test_tip_keys_not_bound_twice_on_word_switch(monkeypatch):
    ipc = FakeIPC()
    r = _reader()
    r.ipc = ipc
    r.tokens = [
        Token("本命", "本命", "ほんめい", "名詞", 0, 2),
        Token("読む", "読む", "よむ", "動詞", 2, 4),
    ]
    r.boxes = [WordBox(0, 100, 300, 40, 40), WordBox(1, 500, 300, 40, 40)]
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    r.set_hover(1)  # switch words — tooltip stays visible
    bound, _ = _tip_binds(ipc)
    assert len(bound) == len(TIP_KEYS)  # bound exactly once, not re-bound per word


def test_keyboard_dispatch_sections_scroll_close(monkeypatch):
    r = _shown(monkeypatch, _reader())
    # RIGHT → next section
    r._handle("saitenka-tab-next")
    assert abs(r._tip_scroll - max(0, r._tab_offsets[1] - r._tab_h)) <= 1
    # LEFT → back to the first
    r._handle("saitenka-tab-prev")
    assert r._tip_scroll == 0
    # DOWN/UP → plain scroll
    r._handle("saitenka-tip-down")
    assert r._tip_scroll > 0
    r._handle("saitenka-tip-up")
    assert r._tip_scroll == 0
    # ESC → close
    r._handle("saitenka-tip-close")
    assert r.hover == -1 and r._tip_rect is None
