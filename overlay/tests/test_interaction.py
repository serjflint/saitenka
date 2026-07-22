"""L1 interface tests — moves/clicks/wheel driven through the REAL mouse-pos / hit-test path.

These use the :class:`Driver` (tests/driver.py) so they read as interaction scripts and go through
``_hit`` / ``on_click`` / ``_scan_hit`` — the same code a real cursor drives — rather than calling
``set_hover`` / ``_show_tooltip`` directly. (Live real-mpv input injection is L3: tests/test_live_mpv.py.)
"""

from __future__ import annotations

from driver import Driver
from util import FakeIPC

from overlay.app.controller import Reader
from overlay.panel import Definition, Entry


class _FakeDS:
    def entry_for(self, tok, inflected=None):
        para = "とても長い定義の本文で" * 8  # tall + dense → scrollable, yields scan cells
        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition(f"辞書{i}", [para]) for i in range(3)],
        )

    def has_term(self, *forms):
        return True


def _reader(monkeypatch):
    r = Reader(FakeIPC(), dict_set=_FakeDS())
    r.osd = (1280, 720)
    r._finish_available = lambda: True  # render full panels (scan cells present)
    monkeypatch.setattr(r, "_draw_subtitle", r._draw_subtitle)  # keep real subtitle boxes
    r.set_subtitle("本命を読む")  # → 本命 / を / 読む, with real per-word boxes
    return r


def _content_word(r) -> int:
    from overlay.app.controller import SKIP_POS

    return next(i for i, t in enumerate(r.tokens) if t.is_content and t.pos not in SKIP_POS)


def test_move_over_word_shows_tooltip_and_switching_words(monkeypatch):
    r = _reader(monkeypatch)
    ui = Driver(r)
    i = _content_word(r)
    ui.move_to_word(i)
    assert ui.hover == i and ui.tip_shown, "moving the cursor onto a word must show its tooltip"
    j = next(k for k in range(len(r.tokens)) if k != i and r.tokens[k].is_content)
    ui.move_to_word(j)
    assert ui.hover == j, "resting on a different word must switch the tooltip to it"


def test_move_off_words_does_not_hover(monkeypatch):
    r = _reader(monkeypatch)
    ui = Driver(r)
    ui.move(5, 5)  # top-left corner — no word there
    assert ui.hover == -1


def test_move_inside_tooltip_opens_nested_scan_popup(monkeypatch):
    r = _reader(monkeypatch)
    ui = Driver(r)  # instant → scan_delay 0
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown
    ui.move_into_tip(0.5, 0.6)  # rest on a word INSIDE the tooltip body
    assert ui.nested_shown, "hovering a word inside the tooltip must open a nested scan popup"


def test_empty_body_click_does_nothing(monkeypatch):
    r = _reader(monkeypatch)
    r.anki = object()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown
    events: list[str] = []
    monkeypatch.setattr(r, "mine_current", lambda: events.append("mine"))
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    # click low in the body, away from the ⊕/🔊 header buttons
    x, y, w, h = r._tip_rect
    ui.move(x + w * 0.5, y + h - 6).click()
    assert events == [], "a click in an empty body area must not mine or speak"


def test_wheel_scrolls_the_tooltip(monkeypatch):
    r = _reader(monkeypatch)
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    ui.move_into_tip(0.5, 0.5)  # cursor over the tip so the wheel routes to it
    before = r._tip_scroll
    ui.wheel(1)  # one notch down
    assert r._tip_scroll > before, "wheeling over a scrollable tooltip must scroll it down"


# --- L2: golden-pin the rendered bitmap that a hover produces ----------------------------------------


def test_golden_base_vs_nested_layout(monkeypatch):
    """L2: pin the panels an interaction produces — the BASE tooltip (with the reserved dict-tab band)
    vs the NESTED popup (compact, no band). A geometry regression (e.g. the reserve leaking into the
    nested popup, or the band vanishing from the base) shows up as a golden diff."""
    from util import assert_golden

    r = _reader(monkeypatch)
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert r._tip_state is not None and r._tip_state.image is not None
    assert r._tip_state.lazy.top_reserve > 0  # base reserves the dict-tab band
    assert_golden(r._tip_state.image, "interaction_base_tooltip.png", tol=3.0)

    ui.move_into_tip(0.5, 0.6)  # open the nested scan popup
    assert r._nest.state is not None and r._nest.state.image is not None
    assert r._nest.state.lazy.top_reserve == 0  # nested is compact — no reserved band
    assert_golden(r._nest.state.image, "interaction_nested_popup.png", tol=3.0)
