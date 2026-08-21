"""Robustness stress: churn scan → scroll → nested popup → dismiss over many distinct heavy entries.

The isolated tooltip tests each reset state between cases; this one accumulates it, so it guards the
failure classes that only appear under sustained load — panel-cache eviction (capped at 48), nested
open/scroll/dismiss churn, and overlay/tooltip state that must reset cleanly every cue. Deterministic
(fixed tokens, fixed steps) and hermetic (a synthetic tall entry, no real dicts) so it runs in the
gate. The *performance* side of the same scenario lives in examples/bench_responsiveness.py --stress.
"""

from __future__ import annotations

from driver import Driver
from util import FakeIPC

from saitenka.app import nested_popup
from saitenka.app.config import TooltipOptions
from saitenka.app.controller import NESTED_ID, TIP_ID, Reader
from saitenka.app.tokenize import Token
from saitenka.panel import Definition, Entry

PANEL_CACHE_MAX = TooltipOptions().panel_cache_max


class _TallDS:
    """A dict that returns a multi-section entry tall enough to scroll and dense enough to yield
    scan boxes (so nested popups open) — the shape that stresses the panel machinery."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        para = "とても長い定義の本文で" * 8  # CJK run → many per-char scan cells, wraps + scrolls
        return Entry(
            headword=tok.surface,
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[
                Definition(f"辞書{i}", [para]) for i in range(4)
            ],  # ≥2 → dict-tab strip + reserve
        )


def _reader() -> Reader:
    r = Reader(FakeIPC(), dict_set=_TallDS())
    r.osd = (1920, 1080)
    r.sub_origin = (0, 0)
    return r


# DISTINCT synthetic entries, comfortably over the LRU cache cap, so eviction is genuinely exercised
_CORPUS = [f"語{i:03d}" for i in range(PANEL_CACHE_MAX + 24)]


def _churn(r: Reader, term: str) -> bool:
    """One cold hover → scroll → nested → scroll → dismiss cycle via the real entry points. Setting
    lines+tokens lets `_draw_subtitle` build a consistent box for token 0. Returns whether a nested
    popup actually opened (so a test can assert the nested path was exercised)."""
    tok = Token(term, term, "ご", "名詞", 0, len(term))
    r.lines = [[tok]]
    r.tokens = [tok]
    # Draw first, then hover: the boxes have to exist before a cursor has anywhere to land. The old
    # `set_hover(0)` did both at once, which is why it could not be a move.
    r._draw_subtitle()
    ui = Driver(r, instant=False).move_to_word(0)
    for _ in range(4):  # scroll toward the bottom of the tall entry
        ui.wheel(1)
    st = r.tip.view.state
    boxes = st.windowed.scan_boxes() if st is not None else []
    opened = False
    if boxes:
        nested_popup.show_nested(
            r.tip_ports, r.panel_ports, r.word_lookup, boxes[len(boxes) // 3]
        )  # nested popup on an inner cell
        opened = r.tip.nest.state is not None
        ui.wheel(1)  # nested is up
        r._hide_nested()
    r.retire_hover()  # dismiss the whole stack
    return opened


def test_sustained_churn_evicts_and_stays_clean():
    """Churning many distinct heavy entries (then revisiting evicted ones) must not raise, must keep the
    panel cache within its LRU cap, and must leave no tooltip/nested state after dismiss."""
    r = _reader()
    nested_seen = 0
    for term in _CORPUS:  # fill past the cap → eviction
        nested_seen += _churn(r, term)
    assert len(r.tip.panel_cache) <= PANEL_CACHE_MAX, (
        f"cache overflowed its LRU cap mid-fill: {len(r.tip.panel_cache)}"
    )
    for term in _CORPUS[:8]:  # revisit the earliest (now-evicted) entries → cold rebuild, no crash
        nested_seen += _churn(r, term)

    assert nested_seen > 0, "nested popups never opened — the nested path wasn't exercised"
    assert len(r.tip.panel_cache) <= PANEL_CACHE_MAX, (
        f"panel cache overflowed its LRU cap: {len(r.tip.panel_cache)}"
    )
    # after the final retire_hover() the whole hover stack must be torn down
    assert r.hover_view().tip.state is None
    assert r.hover_view().nested.state is None  # nested popup cleared
    assert r.hover == -1


def test_churn_removes_both_overlays_no_ghost():
    """Every dismiss must issue overlay-remove for the tooltip AND nested ids — a leaked id would
    leave a ghost panel on mpv's OSD."""
    r = _reader()
    for term in _CORPUS[:12]:
        _churn(r, term)
    removed = {c[1] for c in r.ipc.commands if c and c[0] == "overlay-remove"}
    assert r.ov._oid(TIP_ID) in removed and r.ov._oid(NESTED_ID) in removed, (
        f"tooltip/nested overlays never removed; removes={removed}"
    )
