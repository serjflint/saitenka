"""Phase C: clicked/keyed nested OPENs (kanji `k`, headword/body kanji, search, cross-ref link) defer
their getmask2 raster off the click/key tick — the worker warms the panel bands, the tick places it
warm. Unlike the scan-hover defer, the anchor is CARRIED (a click isn't scan-re-derivable). With no
worker the open is synchronous (unchanged)."""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from weakref import WeakKeyDictionary

import dicthelp
from driver import Driver
from util import FakeIPC

from saitenka.app.config import ReaderOptions
from saitenka.app.features.tooltip import nested_popup, tooltip
from saitenka.app.session.controller import SessionController
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.subtitles import WordBox
from saitenka.app.tokenize import Token
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome

_SUBMITTERS: WeakKeyDictionary[SessionController, _DeferredEngagedSubmitter]


class _DeferredEngagedSubmitter:
    def __init__(self):
        self.run = None
        self.enabled = False
        self.calls = []

    def __call__(self, **kwargs):
        if not self.enabled:
            return False
        self.calls.append(kwargs)
        return True

    def enable(self, reader: SessionController) -> None:
        self.run = reader.tooltip_controller.run_engaged
        self.enabled = True

    def finish(self, *, outcome=EffectOutcome.SUCCEEDED, run=True):
        call = self.calls.pop(0)
        assert self.run is not None
        result = self.run(call["request"]) if run else None
        call["on_finished"](
            EffectFinished(
                EffectId(1),
                call["owner"],
                call["identity"],
                outcome,
                result=result,
                error=EffectError.INTERNAL if outcome is EffectOutcome.FAILED else None,
            )
        )


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


_SUBMITTERS = WeakKeyDictionary()


def _submitter(reader: SessionController) -> _DeferredEngagedSubmitter:
    return _SUBMITTERS[reader]


def _reader(tmp_path, *, worker: bool):
    submitter = _DeferredEngagedSubmitter()
    r = SessionController(
        FakeIPC(),
        dict_set=_fixture_ds(tmp_path),
        options=ReaderOptions(prefetch=True),
        tooltip_runtime_jobs=lambda jobs: replace(jobs, engaged=submitter),
    )
    _SUBMITTERS[r] = submitter
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
    Driver(r).move_to_word(0).move_to_word(1).move_to_word(0)  # end on 読む, both panels cached
    if worker:
        submitter.enable(r)
    return r


def test_no_worker_opens_kanji_synchronously(tmp_path):
    # Negative control: with no prefetch worker, the open builds + shows on the calling tick (unchanged).
    r = _reader(tmp_path, worker=False)
    nested_popup.open_kanji(r._tip_ports, r._panel_ports, "読", 100.0, 300.0, 40.0)
    assert (
        r.tooltip_controller.surface_state().nest.state is not None
        and r.tooltip_controller.surface_state().nest.word == "読"
    )
    assert _submitter(r).calls == []  # nothing deferred


def test_kanji_open_defers_then_places_warm_without_interactive_raster(tmp_path):
    r = _reader(tmp_path, worker=True)
    nested_popup.open_kanji(r._tip_ports, r._panel_ports, "読", 100.0, 300.0, 40.0)
    assert (
        r.tooltip_controller.surface_state().nest.state is None
    )  # deferred — nothing shown on the click tick
    assert _submitter(r).calls

    _submitter(r).finish()

    assert (
        r.tooltip_controller.surface_state().nest.state is not None
        and r.tooltip_controller.surface_state().nest.word == "読"
    )
    # the place composited from worker-warmed bands — zero synchronous glyph rasters on this tick
    assert r.tooltip_controller.surface_state().nest.state.windowed.last_frame_rasters == 0


def test_kanji_open_worker_failure_uses_current_origin_sync_fallback(tmp_path):
    r = _reader(tmp_path, worker=True)
    nested_popup.open_kanji(r._tip_ports, r._panel_ports, "読", 100.0, 300.0, 40.0)

    _submitter(r).finish(outcome=EffectOutcome.FAILED, run=False)

    assert (
        r.tooltip_controller.surface_state().nest.state is not None
        and r.tooltip_controller.surface_state().nest.word == "読"
    )


def test_open_dropped_when_the_base_word_switches_in_the_defer_window(tmp_path):
    # origin guard: a base word switch between the click and the tick must NOT open the stale kanji onto
    # the new word.
    r = _reader(tmp_path, worker=True)
    nested_popup.open_kanji(
        r._tip_ports, r._panel_ports, "読", 100.0, 300.0, 40.0
    )  # origin = id(読む panel)
    submitter = _submitter(r)
    call = submitter.calls.pop(0)
    result = submitter.run(call["request"])
    # the user moves to another word mid-flight → a different panel, so a different id
    Driver(r).move_to_word(1)
    call["on_finished"](
        EffectFinished(
            EffectId(1), call["owner"], call["identity"], EffectOutcome.SUCCEEDED, result=result
        )
    )
    assert (
        r.tooltip_controller.surface_state().nest.state is None
    )  # the stale open was dropped, not opened onto 見る


def test_stale_open_failure_skips_sync_rebuild(tmp_path, monkeypatch):
    r = _reader(tmp_path, worker=True)
    nested_popup.open_kanji(r._tip_ports, r._panel_ports, "読", 100.0, 300.0, 40.0)
    Driver(r).move_to_word(1)
    rebuilt = []
    monkeypatch.setattr(
        r,
        "_engaged_open_panel",
        lambda source, query, **kwargs: rebuilt.append((source, query, kwargs)),
    )

    _submitter(r).finish(outcome=EffectOutcome.FAILED, run=False)

    assert rebuilt == [] and r.tooltip_controller.surface_state().nest.state is None


def test_kanji_with_no_entry_toasts_on_the_click_tick(tmp_path, monkeypatch):
    r = _reader(tmp_path, worker=True)
    toasts: list = []
    monkeypatch.setattr(r.notifications, "show", lambda text, _k="ok", _s=2.8: toasts.append(text))
    nested_popup.open_kanji(
        r._tip_ports, r._panel_ports, "犬", 100.0, 300.0, 40.0
    )  # 犬 isn't in the kanji bank
    assert toasts and "犬" in toasts[0]  # the no-entry toast fired on the tick…
    assert _submitter(r).calls == [] and r.tooltip_controller.surface_state().nest.state is None


def test_cross_reference_link_open_defers(tmp_path):
    # a clicked cross-ref link (exact term) rides the same anchor-carried defer as kanji
    from saitenka.model import LinkBox

    r = _reader(tmp_path, worker=True)
    lb = LinkBox("見る", 10, 20, 40, 40)
    tooltip.nested_popup.open_link(
        r._tip_ports,
        r._panel_ports,
        lb,
        r.tooltip_controller.surface_state().view.xy,
        r.tooltip_controller.surface_state().view.scroll,
    )
    assert r.tooltip_controller.surface_state().nest.state is None and _submitter(r).calls
    _submitter(r).finish()
    assert (
        r.tooltip_controller.surface_state().nest.state is not None
        and r.tooltip_controller.surface_state().nest.word == "見る"
    )
