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
    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        para = "とても長い定義の本文で" * 8  # tall + dense → scrollable, yields scan cells
        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition(f"辞書{i}", [para]) for i in range(3)],
        )

    def has_term(self, *forms):
        # Only the individual subtitle words are terms — never a multi-token concatenation — so the
        # phrase-merge probe stays off and these geometry goldens keep their single-token hover.
        return any(f in {"本命", "を", "読む", "命", "ほんめい", "よむ"} for f in forms)


def _reader(monkeypatch):
    # Pin tip_max_frac so the fixed hit-points and layout goldens below are independent of the product
    # default: a change to the default tooltip height must not silently move these interaction goldens.
    # osd = 1080p so the UI scale is 1.0 (REF_H) — the goldens capture the reference (unscaled) layout.
    # dict tabs default OFF now; force ON so the base-vs-nested reserve/band goldens still exercise it.
    r = Reader(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5, show_dict_tabs=True)
    r.osd = (1920, 1080)
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


def test_tooltip_keeps_lease_over_occluded_word(monkeypatch):
    """A tooltip drawn over another subtitle word keeps the lease: resting the cursor on the covered
    word (inside the tip rect) must NOT hijack the tooltip onto it. Regression for the two-line cue
    where the lower line's tooltip is drawn up over the upper line. `instant` zeroes hover_switch_delay,
    so the pre-fix code (which only *delayed* the hijack) would switch immediately."""
    r = _reader(monkeypatch)
    ui = Driver(r)
    i = _content_word(r)
    ui.move_to_word(i)
    assert ui.hover == i and ui.tip_shown
    j = next(k for k in range(len(r.tokens)) if k != i and r.tokens[k].is_content)
    # simulate a subtitle word (j) sitting UNDER the shown tooltip: _hit reports j everywhere now
    monkeypatch.setattr(r, "_hit", lambda *_a: j)
    ui.move_into_tip(0.5, 0.5)  # cursor over the tip — and, per _hit, over word j beneath it
    assert ui.hover == i, (
        "cursor over the tooltip must keep its lease, not switch to the covered word"
    )
    ui.move(
        5, 5
    )  # off the tooltip (top-left) — the same _hit now DOES switch, proving the lease held it
    assert ui.hover == j, "off the tooltip, the word under the cursor is hovered normally"


def test_hover_over_phrase_start_spans_the_multi_token_term(monkeypatch):
    """Hovering the first token of a multi-token dictionary term (数ある-style) sets the hover span
    over the whole phrase — the underline covers both tokens, Yomitan-style longest-match. Moving off
    clears it."""
    r = _reader(monkeypatch)  # subtitle 本命を読む → 本命 / を / 読む
    # pretend 本命を is a dictionary term so the phrase probe fires over tokens 0..1
    monkeypatch.setattr(r.dict_set, "has_term", lambda *forms: "本命を" in forms)
    ui = Driver(r)
    ui.move_to_word(0)
    assert r._hover_terms == ("本命を",)
    assert r._hover_span == (0, 2), (
        "the highlight must span the hovered token and its phrase partner"
    )
    ui.move_to_word(2)  # switch to 読む — a word with no following phrase term
    assert r._hover_span is None and r._hover_terms == ()


def test_phrase_reaches_panel_lookup_with_dict_tabs_off(monkeypatch):
    """Regression: the phrase terms must reach the entry lookup even when the dict-tab strip is OFF
    (the default). The build once gated extra_terms on ``tabs`` (= show_dict_tabs), so with tabs off
    お休み never stacked — hovering お showed the bare 御 instead."""
    r = Reader(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5, show_dict_tabs=False)
    r.osd = (1920, 1080)
    r._finish_available = lambda: True
    r.set_subtitle("本命を読む")
    monkeypatch.setattr(r.dict_set, "has_term", lambda *forms: "本命を" in forms)
    seen: dict[str, tuple] = {}
    real = r.dict_set.entry_for

    def record(tok, inflected=None, *, extra_terms=()):
        seen["extra"] = tuple(extra_terms)
        return real(tok, inflected, extra_terms=extra_terms)

    monkeypatch.setattr(r.dict_set, "entry_for", record)
    Driver(r).move_to_word(0)
    assert seen["extra"] == ("本命を",), "phrase must reach the panel lookup with dict-tabs off"


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
    from util import assert_golden, bgra_to_image

    r = _reader(monkeypatch)
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert r._tip_state is not None and r._tip_state.ready
    assert r._tip_state.lazy.top_reserve > 0  # base reserves the dict-tab band
    assert_golden(bgra_to_image(r._tip_state.bgra()), "interaction_base_tooltip.png", tol=3.0)

    ui.move_into_tip(0.5, 0.6)  # open the nested scan popup
    assert r._nest.state is not None and r._nest.state.ready
    assert r._nest.state.lazy.top_reserve == 0  # nested is compact — no reserved band
    assert_golden(bgra_to_image(r._nest.state.bgra()), "interaction_nested_popup.png", tol=3.0)
