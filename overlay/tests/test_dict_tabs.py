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
    r.osd = (
        1920,
        1080,
    )  # REF_H → UI scale 1.0, so tab geometry matches the default-theme panel calls
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


# --- tabs are BASE-only: nested popups carry no strip/reserve; the strip is configurable -----------


def test_nested_popup_has_no_tab_strip_or_reserve(monkeypatch):
    """A nested scan popup deliberately carries NO dict-tab strip and NO reserved band even for a
    multi-dict word — it stays compact so the deep-dive gets its full height. (The base tooltip keeps
    its strip.)"""
    from overlay.app.controller import NESTED_ID

    r = _shown(monkeypatch, _reader())  # _MultiDS → 3 dict sections for every word
    assert r._tab_rects, "base tooltip should still have its dict tabs"
    boxes = r._tip_state.lazy.scan_boxes
    assert boxes, "no scan boxes to open a nested popup on"

    captured: dict = {}
    orig = r._blit_panel

    def spy(bgra, scroll, view_h, xy, oid, header=None):
        if oid == NESTED_ID:
            captured["header"] = header
        return orig(bgra, scroll, view_h, xy, oid, header=header)

    monkeypatch.setattr(r, "_blit_panel", spy)
    r._show_nested(boxes[len(boxes) // 2])
    assert r._nest.state is not None, "nested popup didn't open"
    assert r._nest.state.lazy.top_reserve == 0  # no reserved band → no blank padding
    assert "header" in captured and captured["header"] is None  # nested render passes no tab strip


def test_show_dict_tabs_false_hides_base_strip_and_reserve(monkeypatch):
    """show_dict_tabs=False turns the base tooltip's dict-tab strip off entirely — no clickable tabs
    and no reserved band (so the pills can be disabled per the user's preference)."""
    from overlay.app.config import ReaderOptions, TooltipOptions

    r = Reader(
        FakeIPC(),
        dict_set=_MultiDS(),
        options=ReaderOptions(tooltip=TooltipOptions(show_dict_tabs=False)),
    )
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    assert r._tab_rects == []  # no clickable tabs
    assert r._tip_reserve() == 0  # …and no reserved band above the header


def test_multi_dict_reserves_space_so_header_clears_the_tab_strip(monkeypatch):
    # Regression: the sticky tab strip must NOT cover the reading / ⊕ / 🔊. Multi-dict entries
    # reserve EXACTLY the (possibly wrapped) strip's height above the header so those sit below it.
    from overlay.panel import header_add_rect, header_speaker_rect, tab_strip_height

    r = _shown(monkeypatch, _reader())  # 3 dicts → tabs
    reserve = r._tip_reserve()
    assert reserve == tab_strip_height(
        ["MonoC", "MonoB", "MonoD"], r.tip_width
    )  # matches real strip
    for rect in (
        header_add_rect(r.tip_width, top_reserve=reserve),
        header_speaker_rect(r.tip_width, top_reserve=reserve),
    ):
        assert rect[1] >= reserve  # icon's panel-y is below the reserved strip → not covered


def test_tab_strip_wraps_many_dicts_onto_multiple_rows():
    # A many-dict word must show ALL tabs — the strip wraps instead of clipping past the width
    # (regression: only ~4 of 10 tabs were visible).
    from overlay.panel import render_tab_row, tab_row_height, tab_strip_height

    names = [f"Dict{i:02d}" for i in range(10)]
    width = 384
    img, rects = render_tab_row(names, active=0, width=width)
    assert len(rects) == len(names)  # every tab is laid out (none dropped)
    ys = {y for _x, y, _w, _h in rects}
    assert len(ys) >= 2  # wrapped onto multiple rows
    assert all(x + w <= width for x, _y, w, _h in rects)  # nothing clipped past the right edge
    assert img.height == tab_strip_height(names, width) > tab_row_height()  # taller than one row


def test_add_button_gated_on_live_anki(monkeypatch):
    r = _reader()
    r.anki = object()  # mining configured
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    # Anki closed → ⊕ not shown / not hittable
    monkeypatch.setattr("overlay.app.anki.anki_reachable", lambda *a, **k: False)
    r._anki_cache = (0.0, False)
    assert r._anki_ok() is False
    r.set_hover(0)
    assert r._hit_header_add(999, 999) is False
    # Anki reopened → the live check flips (past the TTL) so the ⊕ comes back
    monkeypatch.setattr("overlay.app.anki.anki_reachable", lambda *a, **k: True)
    r._anki_cache = (0.0, False)  # force a re-check rather than wait out the ~3s TTL
    assert r._anki_ok() is True


def test_header_add_rect_takes_speaker_slot_when_tts_hidden():
    from overlay.panel import header_add_rect

    with_spk = header_add_rect(400)
    without = header_add_rect(400, speak_button=False)
    assert without[0] > with_spk[0]  # ⊕ moves right into the 🔊 slot when the speaker is hidden


def test_speaker_gated_off_without_tts(monkeypatch):
    import overlay.app.controller as ctrl

    monkeypatch.setattr(ctrl, "tts_available", lambda: False)  # no JA voice → 🔊 hidden
    ipc = FakeIPC()
    r = _reader()
    r.ipc = ipc
    assert r._tts_ok is False
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    assert r._hit_header_speaker(999, 999) is False  # never hittable
    a_binds = [c for c in ipc.commands if c and c[0] == "keybind" and c[1] == "a"]
    assert a_binds == []  # the 'a' TTS key is not even bound


def test_single_dict_reserves_nothing(monkeypatch):
    class _OneDS:
        def entry_for(self, tok, inflected=None):
            return Entry(headword=[tok.surface], defs=[Definition("MonoC", ["短い。"])])

    r = _shown(monkeypatch, _reader(_OneDS()))
    assert r._tip_reserve() == 0  # no tabs → no wasted top space


def test_tab_click_scrolls_to_section(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc, dict_set=_MultiDS())
    r.osd = (1920, 1080)  # REF_H → UI scale 1.0 (reference tab geometry)
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
    maxs = max(0, r._tip_bgra.shape[0] - r._tip_view_h)
    assert r._tip_scroll == min(
        maxs, max(0, third - r._tab_h)
    )  # …to the third section (clamped to bottom)


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
