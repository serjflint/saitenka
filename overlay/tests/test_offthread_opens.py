"""Phase C: clicked/keyed nested OPENs (kanji `k`, headword/body kanji, search, cross-ref link) defer
their getmask2 raster off the click/key tick — the worker warms the panel bands, the tick places it
warm. Unlike the scan-hover defer, the anchor is CARRIED (a click isn't scan-re-derivable). With no
worker the open is synchronous (unchanged)."""

from __future__ import annotations

import json
import zipfile

import dicthelp
from overlay.app import nested_popup, prefetch, tooltip
from overlay.app.config import ReaderOptions
from overlay.app.controller import Reader
from overlay.app.subtitle_render import NullRenderer
from overlay.app.subtitles import WordBox
from overlay.app.tokenize import Token
from util import FakeIPC


def _fixture_ds(tmp_path):
    """A dict with the term 読む + 見る and a kanji bank (読 / 見) — enough to drive kanji + link opens."""
    kanji = [
        ["読", "ドク", "よ.む", "jouyou", ["to read"], {"strokes": "14"}],
        ["見", "ケン", "み.る", "jouyou", ["to see"], {"strokes": "7"}],
    ]
    terms = [
        ["読む", "よむ", "", "", 0, ["to read"], 1, ""],
        ["見る", "みる", "", "", 0, ["to see"], 2, ""],
    ]
    p = tmp_path / "kd.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "KanjiDict", "format": 3}))
        zf.writestr("term_bank_1.json", json.dumps(terms, ensure_ascii=False))
        zf.writestr("kanji_bank_1.json", json.dumps(kanji, ensure_ascii=False))
    return dicthelp.load_set([str(p)])


def _reader(tmp_path, *, worker: bool):
    r = Reader(FakeIPC(), dict_set=_fixture_ds(tmp_path), options=ReaderOptions(prefetch=True))
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [
        Token("読む", "読む", "よむ", "動詞", 0, 2),
        Token("見る", "見る", "みる", "動詞", 2, 4),
    ]
    r.boxes = [WordBox(0, 100, 300, 40, 40), WordBox(1, 420, 300, 40, 40)]
    r.renderer = NullRenderer()
    # Warm both base panels while there's NO worker (else a cold base show would itself defer, #293), so
    # the base tooltip is up + switchable synchronously and the tests isolate the nested-open defer.
    r.set_hover(0)
    r.set_hover(1)
    r.set_hover(0)  # end on 読む, both base panels now cached
    if (
        worker
    ):  # make workers_running(reader) True without a live thread; we pump the worker by hand
        import threading

        r._prefetch_threads.append(threading.Thread(target=lambda: None))
    return r


def test_no_worker_opens_kanji_synchronously(tmp_path):
    # Negative control: with no prefetch worker, the open builds + shows on the calling tick (unchanged).
    r = _reader(tmp_path, worker=False)
    nested_popup.open_kanji(r, "読", 100.0, 300.0, 40.0)
    assert r._nest.state is not None and r._nest.word == "読"
    assert r._open_req is None  # nothing deferred


def test_kanji_open_defers_then_places_warm_without_interactive_raster(tmp_path):
    r = _reader(tmp_path, worker=True)
    nested_popup.open_kanji(r, "読", 100.0, 300.0, 40.0)
    assert r._nest.state is None  # deferred — nothing shown on the click tick
    assert r._open_req is not None

    assert prefetch._try_engaged_open(r)  # the worker warms the kanji panel's bands
    tooltip.apply_engaged_results(r)  # the tick places it warm

    assert r._nest.state is not None and r._nest.word == "読"
    # the place composited from worker-warmed bands — zero synchronous glyph rasters on this tick
    assert r._nest.state.windowed.last_frame_rasters == 0


def test_open_dropped_when_the_base_word_switches_in_the_defer_window(tmp_path):
    # origin guard: a base word switch between the click and the tick must NOT open the stale kanji onto
    # the new word.
    r = _reader(tmp_path, worker=True)
    nested_popup.open_kanji(r, "読", 100.0, 300.0, 40.0)  # origin = id(読む panel)
    prefetch._try_engaged_open(r)
    r.set_hover(1)  # switch the base tooltip to 見る (a different panel → different id)
    tooltip.apply_engaged_results(r)
    assert r._nest.state is None  # the stale open was dropped, not opened onto 見る


def test_kanji_with_no_entry_toasts_on_the_click_tick(tmp_path, monkeypatch):
    r = _reader(tmp_path, worker=True)
    toasts: list = []
    monkeypatch.setattr(r, "_toast", lambda text, _k="ok", _s=2.8: toasts.append(text))
    nested_popup.open_kanji(r, "犬", 100.0, 300.0, 40.0)  # 犬 isn't in the kanji bank
    assert toasts and "犬" in toasts[0]  # the no-entry toast fired on the tick…
    assert r._open_req is None and r._nest.state is None  # …and nothing was deferred or shown


def test_cross_reference_link_open_defers(tmp_path):
    # a clicked cross-ref link (exact term) rides the same anchor-carried defer as kanji
    from overlay.model import LinkBox

    r = _reader(tmp_path, worker=True)
    lb = LinkBox("見る", 10, 20, 40, 40)
    tooltip.nested_popup.open_link(r, lb, r._tip_xy, r._tip_scroll)
    assert r._nest.state is None and r._open_req is not None  # deferred
    prefetch._try_engaged_open(r)
    tooltip.apply_engaged_results(r)
    assert r._nest.state is not None and r._nest.word == "見る"
