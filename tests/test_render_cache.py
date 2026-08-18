"""Persistent cross-session render cache (#149): the SQLite store + the Panel seed/store round-trip.

The cache persists a cost-gated precomposed first viewport so a cold hover in a later session is
copy+upload, not a head raster. These assert the observable contract: a stored head reads back
byte-identical, a config/geometry mismatch is a safe miss (→ live render), the cost gate keeps short
heads out, and the byte ceiling LRU-evicts. No mpv, no dicts — the store keys on plain strings and the
Panel round-trip uses constructed rows, mirroring test_windowed_prefetch.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import ClassVar

import numpy as np

from saitenka.app import tooltip, tooltip_engaged, tooltip_raster
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.popups import Panel
from saitenka.app.render_cache import (
    RenderCache,
    config_signature,
    content_key,
    dict_set_signature,
)
from saitenka.panel import Definition, Entry, panel_rows
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome

WIDTH = 384
SIG = "v1|w384|c260|test-dicts"


class _DeferredRenderSubmitter:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return True

    def finish(self):
        call = self.calls.pop(0)
        result = tooltip_raster.run_render_ahead(call["request"], threading.Event())
        call["on_finished"](
            EffectFinished(
                EffectId(1),
                call["owner"],
                call["identity"],
                EffectOutcome.SUCCEEDED,
                result=result,
            )
        )

    def finish_all(self):
        while self.calls:
            self.finish()


class _DeferredEngagedSubmitter:
    def __init__(self, reader):
        self.backend = tooltip_engaged.ReaderEngagedBackend(reader)
        self.calls = []
        self.reject_next = False

    def __call__(self, **kwargs):
        if self.reject_next:
            self.reject_next = False
            return False
        self.calls.append(kwargs)
        return True

    def finish(self, *, outcome=EffectOutcome.SUCCEEDED, run=True):
        call = self.calls.pop(0)
        result = (
            tooltip_engaged.run_engaged(call["request"], threading.Event(), self.backend)
            if run
            else None
        )
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


def _enable_engaged(reader):
    submitter = _DeferredEngagedSubmitter(reader)
    reader._engaged_tooltip_submit = submitter
    return submitter


def _cache(tmp_path, **kw) -> RenderCache:
    cache = RenderCache.open(
        tmp_path / "render.sqlite", max_bytes=kw.pop("max_bytes", 10 * 1024**2)
    )
    assert cache is not None
    return cache


def _view(h: int = 260, w: int = WIDTH, fill: int = 7) -> np.ndarray:
    return np.full((h, w, 4), fill, dtype=np.uint8)


def _entry(n_defs: int = 20) -> Entry:
    return Entry(
        headword=["掛ける", {"tag": "rt", "content": "かける"}],
        defs=[
            Definition(f"辞書{i}", [f"意味{i}：長い説明文が縦に伸びていく本文。" * 2])
            for i in range(n_defs)
        ],
    )


def test_put_then_get_returns_byte_identical_view(tmp_path):
    cache = _cache(tmp_path)
    arr = _view()
    cache.put(SIG, "kakeru", 260, 260, 4000, arr)
    got = cache.get(SIG, "kakeru", 260, 260)
    assert got is not None
    assert got.view_h == 260 and got.overscan == 260 and got.full_h == 4000
    assert np.array_equal(got.array, arr)


def test_peek_returns_view_and_full_h_regardless_of_geometry(tmp_path):
    # The direct-paint path peeks by key alone (it doesn't know view_h until it has full_h): the row
    # comes back with the stored full_h for placement + scrollbar.
    cache = _cache(tmp_path)
    cache.put(SIG, "kakeru", 260, 260, 4000, _view())
    got = cache.peek(SIG, "kakeru")
    assert got is not None and got.full_h == 4000 and got.array.shape == (260, WIDTH, 4)
    assert cache.peek(SIG, "absent") is None


def test_get_misses_on_absent_key(tmp_path):
    cache = _cache(tmp_path)
    assert cache.get(SIG, "never-stored", 260, 260) is None


def test_get_misses_on_geometry_mismatch(tmp_path):
    # A different full-height → different view_h/overscan at show time: must miss (the stored pixels are
    # for the wrong viewport), so the caller live-renders instead of installing wrong-sized pixels.
    cache = _cache(tmp_path)
    cache.put(SIG, "kakeru", 260, 260, 4000, _view())
    assert cache.get(SIG, "kakeru", 300, 300) is None
    assert cache.get(SIG, "kakeru", 260, 200) is None


def test_get_misses_across_config_partitions(tmp_path):
    cache = _cache(tmp_path)
    cache.put(SIG, "kakeru", 260, 260, 4000, _view())
    assert cache.get("v1|w640|c432|test-dicts", "kakeru", 260, 260) is None


def test_put_is_idempotent_last_write_wins(tmp_path):
    cache = _cache(tmp_path)
    cache.put(SIG, "kakeru", 260, 260, 4000, _view(fill=1))
    cache.put(SIG, "kakeru", 260, 260, 4000, _view(fill=9))
    got = cache.get(SIG, "kakeru", 260, 260)
    assert got is not None and int(got.array[0, 0, 0]) == 9
    assert cache.stats()[0] == 1  # replaced, not duplicated


def test_put_never_trims_on_the_live_path(tmp_path):
    # The live write path (prefetch worker) must NOT trim — no per-put SUM(nbytes) scan. Three writes
    # past a 2-entry ceiling all survive until enforce_limits() runs offline.
    cache = _cache(tmp_path)
    cache.put(SIG, "a", 260, 260, 4000, _view(fill=1))
    cache.put(SIG, "b", 260, 260, 4000, _view(fill=2))
    cache.max_bytes = cache.stats()[1] + 1  # room for ~two — yet put still won't evict
    cache.put(SIG, "c", 260, 260, 4000, _view(fill=3))
    assert cache.stats()[0] == 3  # nothing evicted on the live path


def test_enforce_limits_evicts_oldest_inserted_offline(tmp_path):
    # Eviction is OFFLINE (prewarm) now, oldest-INSERTED first — a read no longer bumps recency (pure
    # read, no write). So a recently-read-but-oldest entry is still the victim.
    cache = _cache(tmp_path)
    cache.put(SIG, "a", 260, 260, 4000, _view(fill=1))
    cache.put(SIG, "b", 260, 260, 4000, _view(fill=2))
    cache.max_bytes = cache.stats()[1] + 1  # room for ~the two present, but not a third
    cache.put(SIG, "c", 260, 260, 4000, _view(fill=3))
    cache.get(SIG, "a", 260, 260)  # a read does NOT save 'a' anymore (recency untracked)
    cache.enforce_limits()
    assert (
        cache.get(SIG, "a", 260, 260) is None
    )  # oldest INSERTED — evicted despite the recent read
    assert cache.get(SIG, "b", 260, 260) is not None
    assert cache.get(SIG, "c", 260, 260) is not None


def test_protected_prewarm_rows_evict_last(tmp_path):
    # The capped-cache fix, now enforced offline: enforce_limits evicts an unprotected row before a
    # prewarmed (protected=True) popular head — so a live write-back never thrashes the prewarm set.
    cache = _cache(tmp_path)
    cache.put(SIG, "popular", 260, 260, 4000, _view(fill=1), protected=True)  # prewarmed
    cache.put(SIG, "rare1", 260, 260, 4000, _view(fill=2))  # live write-back
    cache.max_bytes = cache.stats()[1] + 1  # room for ~two, not three
    cache.put(SIG, "rare2", 260, 260, 4000, _view(fill=3))  # live: no trim here
    cache.enforce_limits()  # offline: must evict rare1 (unprotected, oldest), NOT popular
    assert cache.get(SIG, "popular", 260, 260) is not None  # protected — survived
    assert cache.get(SIG, "rare1", 260, 260) is None  # unprotected + oldest — evicted
    assert cache.get(SIG, "rare2", 260, 260) is not None


def test_config_signature_varies_with_width_and_dicts():
    a = config_signature(width=384, cap=260, dict_sig="X")
    assert a != config_signature(width=640, cap=260, dict_sig="X")
    assert a != config_signature(width=384, cap=432, dict_sig="X")
    assert a != config_signature(width=384, cap=260, dict_sig="Y")


def test_dict_set_signature_tracks_titles_and_db_stamp(tmp_path):
    db = tmp_path / "dictionaries.sqlite"
    db.write_bytes(b"x" * 10)
    ds = SimpleNamespace(
        dicts=[SimpleNamespace(title="Jitendex", db=SimpleNamespace(path=db))],
        freqs=[SimpleNamespace(title="Freq")],
        pitches=[],
    )
    sig = dict_set_signature(ds)
    ds2 = SimpleNamespace(
        dicts=[SimpleNamespace(title="Other", db=SimpleNamespace(path=db))],
        freqs=[SimpleNamespace(title="Freq")],
        pitches=[],
    )
    assert dict_set_signature(ds2) != sig  # a different dict title → different partition
    db.write_bytes(b"x" * 20)  # a re-import changes size → different stamp
    assert dict_set_signature(ds) != sig


def test_panel_store_then_fresh_panel_loads_zero_raster(tmp_path):
    # End-to-end: a prefetch-worker precompose stores a cost-gated head; a fresh Panel (cold, other
    # session) loads it and the show's first viewport is a zero-raster copy of the same pixels.
    cache = _cache(tmp_path)
    rows = panel_rows(_entry(), WIDTH)
    cap = 260
    writer = Panel.from_rows(rows, WIDTH, "かける")
    writer.render_head(cap)
    writer.precompose_head(cap, cache=cache, config_sig=SIG, content_key="kakeru", min_height=0)
    ref = writer.viewport(0, min(writer.full_height, cap), overscan=min(writer.full_height, cap))

    reader = Panel.from_rows(panel_rows(_entry(), WIDTH), WIDTH, "かける")
    reader.render_head(cap)
    assert reader.load_precomposed_head(cap, cache, SIG, "kakeru") is True
    vh = min(reader.full_height, cap)
    warm = reader.viewport(0, vh, overscan=vh)
    assert reader.last_frame_rasters == 0  # served from the disk-seeded first view
    assert np.array_equal(
        warm, ref
    )  # Panel.viewport returns premul-BGRA; same pixels either session


def test_cost_gate_skips_short_heads(tmp_path):
    # A head shorter than min_height (the common, cheap-cold case) is NOT persisted — the cache stays
    # bounded to the pathological tall tail.
    cache = _cache(tmp_path)
    writer = Panel.from_rows(panel_rows(_entry(2), WIDTH), WIDTH, "かける")
    cap = 260
    writer.render_head(cap)
    tall_gate = writer.full_height + 10_000
    writer.precompose_head(
        cap, cache=cache, config_sig=SIG, content_key="short", min_height=tall_gate
    )
    assert cache.stats()[0] == 0  # nothing stored


def test_load_misses_when_never_stored(tmp_path):
    cache = _cache(tmp_path)
    panel = Panel.from_rows(panel_rows(_entry(), WIDTH), WIDTH, "かける")
    panel.render_head(260)
    assert panel.load_precomposed_head(260, cache, SIG, "absent") is False


class _TallDS:
    """A dict set stand-in whose every entry is tall (scrollable) so the cost gate keeps it."""

    dicts: ClassVar[list] = []
    freqs: ClassVar[list] = []
    pitches: ClassVar[list] = []

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        para = "とても長い定義の本文で" * 8
        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition(f"辞書{i}", [para]) for i in range(3)],
        )

    def has_term(self, *forms):  # noqa: ARG002  # protocol shape
        return False


def test_cold_show_paints_directly_from_cache_before_building(tmp_path, monkeypatch):
    # The whole point of #149's direct-paint: a cold hover the cache HAS uploads the cached pixels
    # WITHOUT the build+measure+raster pipeline. Prove the path (not just identical pixels) with a
    # sentinel array a fresh raster could never produce — if mpv receives the sentinel, it came from disk.
    from util import FakeIPC

    from saitenka.app.config import ReaderOptions, TooltipOptions
    from saitenka.app.controller import Reader
    from saitenka.app.render_cache import content_key

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    r = Reader(
        FakeIPC(),
        dict_set=_TallDS(),
        options=ReaderOptions(tooltip=TooltipOptions(render_cache=True)),
    )
    r.osd = (1920, 1080)
    r.set_subtitle("本命を読む")
    r._hover_terms = ()
    i = next(i for i, t in enumerate(r.tokens) if t.is_content)
    tok = r.tokens[i]
    cap, w = r._tip_cap(), r.tip_width
    key = r._panel_key(tok, r._inflected_surface(i), mined=r._is_mined(tok))

    # The reader uses the cache only WHEN AVAILABLE, so create + inject it (prewarm's role). The MAIN
    # thread now direct-paints from the in-memory tier-2 (never disk), so hydrate tier-2 as a worker
    # would (peek_compressed the persisted head → mem.put); then a real cold show paints from RAM.
    cache = _cache(tmp_path)
    r.session.render_cache.obj, r.session.render_cache.built = cache, True
    sentinel = np.full(
        (cap, w, 4), 123, dtype=np.uint8
    )  # full_h == view_h == cap → no scrollbar mutates it
    sig, ck = r._render_cache_sig(), content_key(key)
    cache.put(sig, ck, cap, cap, cap, sentinel)
    r.session.render_cache.mem.put((sig, ck), cache.peek_compressed(sig, ck))

    uploaded: list = []
    monkeypatch.setattr(r.ov, "show_bgra", lambda view, *_a, **_k: uploaded.append(np.array(view)))
    r._panel_cache.clear()  # force cold
    r._show_tooltip(i)

    assert uploaded and np.array_equal(
        uploaded[0], sentinel
    )  # painted from disk, not a fresh raster
    from saitenka.app.popups import Panel

    assert isinstance(r._tip_state, Panel)  # …and the real interactive panel was still built after


# --- the reusable raster-accounting oracle + the unified popup view (Phase A) --------------------


class _ScrollTallDS:
    """Inner-word / hovered-word lookup returns a VERY tall entry (full_height ≫ one screen of
    overscan), so a scroll reaches genuinely cold 1× bands — the negative control needs a real raster to
    catch. Every word gets its own headword, so distinct tokens build distinct panels."""

    dicts: ClassVar[list] = []
    freqs: ClassVar[list] = []
    pitches: ClassVar[list] = []

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        para = "とても長い定義の本文がここに縦へ縦へと伸びていく段落" * 6
        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition(f"辞書{i}", [para]) for i in range(16)],
        )

    def has_term(self, *forms):  # noqa: ARG002  # protocol shape
        return False


def _nested_reader(*, two_words: bool = False):
    """A 4K (hi-dpi → the crisp native compose path) scan reader whose inner-word lookup returns a TALL,
    scrollable entry — the fixture the nested-popup blit/scroll/crisp tests drive. Prefetch is on so
    scroll records render-ahead; the tests decide when to drain it (the 'worker'). ``two_words`` gives
    the base and the nested view DISTINCT words → distinct panels, so warming one can't warm the other."""
    from util import FakeIPC

    from saitenka.app.config import ReaderOptions
    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_render import NullRenderer
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = Reader(
        FakeIPC(), dict_set=_ScrollTallDS(), options=ReaderOptions(prefetch=True), scan_delay=0.0
    )
    r._render_ahead_submit = _DeferredRenderSubmitter()
    r.osd = (3840, 2160)  # 4K → _raster_scale 2.0, so _blit_native takes the crisp path
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    if two_words:
        r.tokens.append(Token("読書", "読書", "どくしょ", "名詞", 2, 4))
        r.boxes.append(WordBox(1, 420, 300, 40, 40))
    r.renderer = NullRenderer()
    return r


def assert_no_interactive_raster(view, act) -> int:
    """The raster-accounting invariant that makes 'no tooltip raster on the interactive thread'
    enforceable, not aspirational: drive ``act`` — an interactive-thread action on popup ``view`` — and
    return how many glyph bands it rasterised SYNCHRONOUSLY on the calling thread (the ``_sync_rasters``
    delta — the crisp warm_only compose reads cached bands and rasters none). The contract is 0 on a
    warm view; a cold view is the negative control (returns > 0), proving the oracle can fire. Reused by
    every phase that moves a raster off the interactive thread."""
    st = view.state
    assert st is not None, "the view must have a panel to account for its rasters"
    before = st.windowed._sync_rasters
    act()
    return st.windowed._sync_rasters - before


def test_cold_nested_scroll_rasters_on_the_interactive_thread():
    # Negative control for the oracle: a nested popup scrolled into its COLD tail (never warmed, since we
    # don't drain the render-ahead) rasters glyph bands on the calling thread — proving the oracle can
    # actually fire (else a 0 in the positive test is meaningless).
    r = _nested_reader()
    tok = r.tokens[0]
    r._open_nested(tok, tok.surface, 300.0, 2000.0, 40.0)

    def scroll_to_the_cold_tail() -> None:
        for _ in range(
            6
        ):  # each notch grows the height estimate and moves past the warmed overscan
            tooltip.scroll_view(r, r._nest, r._nest.view_h)

    assert assert_no_interactive_raster(r._nest, scroll_to_the_cold_tail) > 0


def test_warm_nested_scroll_upgrades_to_crisp_with_no_interactive_raster():
    # Phase A: the nested popup finally gets render-ahead + crisp-poll. After a scroll records a warm and
    # the worker drains it, the poll tick assembles the crisp viewport from warm native bands with ZERO
    # synchronous raster on the interactive thread — the guarantee the base tooltip already had.
    r = _nested_reader()
    tok = r.tokens[0]
    r._open_nested(tok, tok.surface, 300.0, 2000.0, 40.0)  # soft first paint
    tooltip.scroll_view(r, r._nest, r._nest.view_h // 2)  # soft-first + records a render-ahead
    assert r._nest.crisp_pending
    r._render_ahead_submit.finish_all()

    rasters = assert_no_interactive_raster(r._nest, lambda: tooltip.apply_pending_crisp(r, r._nest))
    assert rasters == 0
    assert r._nest.crisp_miss == "" and not r._nest.crisp_pending  # upgraded soft → crisp


def test_nested_scroll_requests_render_ahead_for_the_nested_view():
    # The wiring behind the guarantee above: scrolling the nested popup records a render-ahead request
    # for the NESTED panel (not the base) so a worker can warm its next bands.
    r = _nested_reader()
    tok = r.tokens[0]
    r._open_nested(tok, tok.surface, 300.0, 2000.0, 40.0)
    nest = r._nest.state
    tooltip.scroll_view(r, r._nest, max(1, r._nest.view_h // 3))
    pending = r._render_ahead.pending
    assert pending is not None
    req = pending[1]
    assert req is not None and req.panel is nest  # the request targets the nested panel


def _render_ahead_scales(r, view, monkeypatch) -> list[float]:
    """Drive a flick + one worker render-ahead pass, recording every ``scale`` the panel's render_ahead
    was warmed at — the observable of #297's fix (raw bands warmed ahead, not only native)."""
    r._render_ahead_submit.finish_all()
    seen: list[float] = []
    st = view.state
    assert st is not None
    real = st.render_ahead

    def spy(*a, **k):
        seen.append(k.get("scale", 1.0))
        return real(*a, **k)

    monkeypatch.setattr(st, "render_ahead", spy)
    tooltip.scroll_view(r, view, view.view_h)  # flick → records a render-ahead
    r._render_ahead_submit.finish_all()
    return seen


def test_render_ahead_warms_raw_bands_ahead_on_hidpi(monkeypatch):
    # #297: on a hi-dpi display the soft-first blit rasters RAW 1× bands for a region beyond the soft
    # overscan — synchronously on the flick tick. The worker must now warm the raw (scale=1.0) bands ahead
    # in ADDITION to the native ones, so a fast flick finds them cached instead of rastering on-thread.
    r = _nested_reader()
    r.hover = 0
    monkeypatch.setattr(r.ov, "show_bgra", lambda *_a, **_k: None)
    r._show_tooltip(0)
    scales = _render_ahead_scales(r, r._tip_view, monkeypatch)
    assert r._raster_scale > 1.0  # the fixture is 4K → native scale
    assert r._raster_scale in scales  # native bands warmed ahead (unchanged)
    assert 1.0 in scales  # …and the raw bands the soft flick path reads (the fix)


def test_render_ahead_warms_raw_once_on_lodpi(monkeypatch):
    # Negative control: at scale 1.0 the native path IS the raw path, so render-ahead warms raw exactly
    # once — the fix must not add a redundant second 1.0 warm when there are no separate native bands.
    r = _nested_reader()
    r.osd = (1920, 1080)  # lo-dpi → _raster_scale 1.0
    r.hover = 0
    monkeypatch.setattr(r.ov, "show_bgra", lambda *_a, **_k: None)
    r._show_tooltip(0)
    scales = _render_ahead_scales(r, r._tip_view, monkeypatch)
    assert r._raster_scale == 1.0
    assert scales == [1.0]  # exactly one raw warm, no duplicate


def test_soft_nested_paint_upgrades_the_nested_view_not_the_base(monkeypatch):
    # Regression (the bug Phase A closes): _blit_native wrote a SINGLE shared crisp flag, so a nested
    # soft paint flipped the BASE's crisp_pending — apply_pending_crisp then re-blit the base and cleared
    # it, and the nested popup NEVER upgraded to crisp. Per-view flags fix it: each popup owns its own.
    r = _nested_reader(two_words=True)  # distinct base/nested words → distinct panels
    r.hover = 0
    r._show_tooltip(0)  # base tooltip up, cold → its own crisp_pending
    assert r._tip_view.crisp_pending
    tok = r.tokens[1]  # nested on a DIFFERENT word, so warming it can't warm the base
    r._open_nested(tok, tok.surface, 300.0, 2000.0, 40.0)  # nested soft paint
    assert r._nest.crisp_pending  # nested has its OWN pending flag…
    assert r._tip_view.crisp_pending  # …and did NOT clobber the base's

    nest = r._nest.state
    assert nest is not None
    vh = min(r._nest.view_h, nest.full_height)
    y0 = max(0, min(r._nest.scroll, max(0, nest.full_height - vh)))
    nest.viewport(y0, vh, overscan=vh, scale=r._raster_scale)  # worker warms ONLY the nested native

    uploads: list = []
    monkeypatch.setattr(r.ov, "show_bgra", lambda _v, *_a, oid=None, **_k: uploads.append(oid))
    tooltip.apply_pending_crisp(r, r._tip_view)  # base native still cold → no-op, base not re-blit
    tooltip.apply_pending_crisp(r, r._nest)  # nested native warm → upgrades the NESTED to crisp

    assert not r._nest.crisp_pending and r._nest.crisp_miss == ""  # nested is crisp now
    assert r._tip_view.crisp_pending  # base still pending (untouched by the nested upgrade)
    assert uploads == [
        OverlayId.NESTED
    ]  # ONLY the nested re-blit; the base was not spuriously redrawn


def test_popular_terms_ranks_by_frequency_dedupes_and_caps():
    # The prewarm population is the top-N by freq rank, most-popular first, de-duped across freq dicts —
    # NOT the whole term dump. A term in two freq dicts takes its best (lowest) rank.
    import sqlite3

    from saitenka.app.prewarm import _popular_terms

    def _freq_source(rows, dict_id):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute(
            "CREATE TABLE term_meta (dict_id INT, mode TEXT, term TEXT, reading TEXT, rank INT)"
        )
        conn.executemany(
            "INSERT INTO term_meta VALUES (?,?,?,?,?)",
            [(dict_id, "freq", t, r, rk) for t, r, rk in rows],
        )
        conn.commit()
        return SimpleNamespace(dict_id=dict_id, db=SimpleNamespace(_conn=lambda: conn))

    ds = SimpleNamespace(
        freqs=[
            _freq_source([("見る", "みる", 5), ("手", "て", 1), ("鬱", "うつ", 900)], 1),
            _freq_source([("手", "て", 3), ("気", "き", 2)], 2),  # 手 also here, worse rank
        ]
    )
    out = _popular_terms(ds, limit=3)
    assert out == [
        ("手", "て"),
        ("気", "き"),
        ("見る", "みる"),
    ]  # rank 1,2,5 — 鬱(900) dropped by the cap


def test_content_key_is_stable_and_distinguishing():
    k1 = content_key(("見る", "見る", "みる", "見る", 384, True, False, (), ()))
    k2 = content_key(("見る", "見る", "みる", "見る", 384, True, True, (), ()))
    assert k1 == content_key(("見る", "見る", "みる", "見る", 384, True, False, (), ()))
    assert k1 != k2  # mined flag flips the ⊕→✓ header pixels → a distinct key


# --- pure reads + compressed round-trip ---------------------------------------------------------


def test_reads_never_write_back(tmp_path):
    # peek/get/peek_compressed are pure reads now (no LRU used_seq bump) — the main-thread hover path
    # must not write. Prove used_seq is unchanged after a batch of reads.
    import sqlite3

    cache = _cache(tmp_path)
    cache.put(SIG, "k", 260, 260, 4000, _view())

    def used_seq() -> int:
        conn = sqlite3.connect(cache.path)
        try:
            return conn.execute("SELECT used_seq FROM heads WHERE content_key='k'").fetchone()[0]
        finally:
            conn.close()

    before = used_seq()
    cache.peek(SIG, "k")
    cache.get(SIG, "k", 260, 260)
    cache.peek_compressed(SIG, "k")
    assert used_seq() == before  # no read bumped recency


def test_peek_compressed_round_trips_and_matches_get(tmp_path):
    cache = _cache(tmp_path)
    arr = _view(fill=5)
    cache.put(SIG, "k", 260, 260, 4000, arr)
    cv = cache.peek_compressed(SIG, "k")
    assert cv is not None and cv.view_h == 260 and cv.overscan == 260 and cv.full_h == 4000
    assert 0 < cv.nbytes < arr.nbytes  # stored compressed
    assert np.array_equal(cv.inflate().array, arr)  # inflates byte-identical
    assert cache.peek_compressed(SIG, "absent") is None


# --- tier-2 in-memory compressed head cache -----------------------------------------------------


def _cv(nbytes: int, fill: int = 1):
    from saitenka.app.render_cache import CompressedView

    return CompressedView(260, 260, 4000, 1, nbytes, bytes([fill]) * nbytes)


def test_compressed_head_cache_evicts_oldest_by_bytes():
    from saitenka.app.render_cache import CompressedHeadCache

    c = CompressedHeadCache(max_bytes=250)
    c.put("a", _cv(100))
    c.put("b", _cv(100))
    assert len(c) == 2 and c.nbytes == 200
    c.put("c", _cv(100))  # 300 > 250 → drop the oldest ('a')
    assert c.get("a") is None
    assert c.get("b") is not None and c.get("c") is not None
    assert c.nbytes == 200


def test_compressed_head_cache_get_bumps_ram_recency():
    # tier-2 IS recency-aware (an in-RAM move_to_end — cheap, no IO), unlike the disk store's
    # insertion-order eviction. A recently-read entry survives the next eviction.
    from saitenka.app.render_cache import CompressedHeadCache

    c = CompressedHeadCache(max_bytes=250)
    c.put("a", _cv(100))
    c.put("b", _cv(100))
    c.get("a")  # bump 'a' → 'b' is now the LRU victim
    c.put("c", _cv(100))
    assert c.get("b") is None
    assert c.get("a") is not None and c.get("c") is not None


# --- worker disk→tier-2 hydration + tier-3 poll-deferred render ----------------------------------


def _tall_reader(tmp_path, monkeypatch, ipc=None):
    from util import FakeIPC

    from saitenka.app.config import ReaderOptions, TooltipOptions
    from saitenka.app.controller import Reader

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    r = Reader(
        ipc or FakeIPC(),
        dict_set=_TallDS(),
        options=ReaderOptions(tooltip=TooltipOptions(render_cache=True)),
    )
    r.osd = (1920, 1080)
    r.set_subtitle("本命を読む")
    r._hover_terms = ()
    r.session.render_cache.cache_min_height_px = 0  # store every head (no cost gate) for the test
    cache = _cache(tmp_path)
    r.session.render_cache.obj, r.session.render_cache.built = cache, True
    return r, cache


def test_preloop_demo_hover_is_ready_with_a_real_runtime_gateway(tmp_path, monkeypatch):
    from util import FakeIPC, runtime_gateway

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    r, _cache_obj = _tall_reader(tmp_path, monkeypatch, ipc)
    i, _tok, _inflected, _mined = _first_content(r)
    r._panel_cache.clear()
    try:
        r.prepare_hover_blocking(i)
        assert r._tip_state is not None
        assert r._engaged_tooltip.inflight is None
    finally:
        r.close()
        gateway.close()


def _first_content(r):
    i = next(i for i, t in enumerate(r.tokens) if t.is_content)
    tok = r.tokens[i]
    return i, tok, r._inflected_surface(i), r._is_mined(tok)


def test_worker_seed_head_hydrates_tier2_from_disk_no_raster(tmp_path, monkeypatch):
    # The worker seeds an evicted-then-rebuilt panel's first view from disk (inflate off the main
    # thread) and mirrors it into tier-2 — so the main-thread read then hits RAM, never SQLite.
    r, cache = _tall_reader(tmp_path, monkeypatch)
    _i, tok, inflected, mined = _first_content(r)
    cap = r._tip_cap()
    st = r._panel_for(tok, inflected, min_h=cap, mined=mined)
    r._precompose_head(st, tok, inflected, mined=mined, cap=cap, protected=True)  # a prewarmed head
    assert cache.stats()[0] == 1  # persisted to disk

    r._panel_cache.clear()  # simulate the in-memory panel evicted
    fresh = r._panel_for(tok, inflected, min_h=cap, mined=mined)
    assert fresh.windowed.first_view is None  # cold rebuild
    assert r._worker_seed_head(fresh, tok, inflected, mined=mined, cap=cap) is True
    assert fresh.windowed.first_view is not None  # seeded from disk on the worker
    assert len(r.session.render_cache.mem) == 1  # tier-2 hydrated
    assert (
        r._peek_render_cache(r._panel_key(tok, inflected, mined=mined)) is not None
    )  # main hits RAM


def test_cold_miss_defers_showing_nothing_and_enqueues(tmp_path, monkeypatch):
    # A cold miss (empty tier-2 + disk) must NOT build/raster on the main thread: no tooltip is shown,
    # and a top-priority worker compose is enqueued instead.
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    i, _tok, _inflected, _mined = _first_content(r)
    r._panel_cache.clear()  # ensure cold
    tooltip.show_tooltip(r, i)
    assert r._tip_state is None  # nothing shown — deferred
    assert submitter.calls  # handed to the runtime actor


def test_engaged_render_composes_then_completion_shows_warm(tmp_path, monkeypatch):
    from saitenka.app import tooltip

    r, cache = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    i, tok, inflected, mined = _first_content(r)
    key = tooltip.panel_key(r, tok, inflected, mined=mined, phrase=r._hover_terms)

    # Cold hover defers → worker composes (panel cache + tier-2 + disk all warmed).
    r._panel_cache.clear()
    tooltip.show_tooltip(r, i)
    assert r._tip_state is None and submitter.calls
    r.hover = i
    submitter.finish()
    assert cache.stats()[0] == 1 and len(r.session.render_cache.mem) == 1

    # Still hovering the same word → completion shows the now-warm tooltip.
    assert r._tip_state is not None  # shown
    assert tuple(r._tip_key) == tuple(key)


def test_engaged_render_failure_emits_a_terminal_tooltip_outcome(tmp_path, monkeypatch):
    import contextlib

    from saitenka import otel_metrics
    from saitenka.app import tooltip

    r, _cache = _tall_reader(tmp_path, monkeypatch)
    _i, tok, inflected, mined = _first_content(r)
    key = tooltip.panel_key(r, tok, inflected, mined=mined)
    spans = []

    @contextlib.contextmanager
    def traced(name, **attrs):
        spans.append((name, attrs))
        yield None

    monkeypatch.setattr(otel_metrics, "traced", traced)
    submitter = _enable_engaged(r)
    r._tip_view.job_id = r._interaction_jobs.begin("tooltip")
    assert r._request_engaged_tooltip(
        tooltip_engaged.HoverRequest(
            tok, inflected, mined, tuple(key), r._tip_cap(), job_id=r._tip_view.job_id
        )
    )
    submitter.finish(outcome=EffectOutcome.FAILED, run=False)

    assert spans[-1][0] == "tooltip_request"
    assert spans[-1][1]["outcome"] == "failed"


def test_engaged_render_capability_change_does_not_strand_hover(tmp_path, monkeypatch):
    from saitenka.app import tooltip

    r, _cache = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    i, tok, inflected, mined = _first_content(r)
    old_key = tooltip.panel_key(r, tok, inflected, mined=mined)
    r._tip_view.job_id = r._interaction_jobs.begin("tooltip")
    assert r._request_engaged_tooltip(
        tooltip_engaged.HoverRequest(
            tok, inflected, mined, tuple(old_key), r._tip_cap(), job_id=r._tip_view.job_id
        )
    )

    r._tts_ok = not r._tts_ok
    r.hover = i
    monkeypatch.setattr(tooltip, "_paint_from_cache", lambda *_args: False)
    submitter.finish()

    assert r._tip_state is not None or submitter.calls
    if submitter.calls:
        queued = submitter.calls[-1]["request"].request
        assert isinstance(queued, tooltip_engaged.HoverRequest)
        assert tuple(queued.key) != tuple(old_key)


def test_engaged_result_discarded_when_word_changed(tmp_path, monkeypatch):
    # Key guard (the analysis-overlay pattern): a composed head for a word the user left is dropped — no
    # tooltip flashes for the wrong word. (tier-2/disk stay warm for a later re-hover.)
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r.hover, r._tip_state = -1, None  # not hovering that word anymore
    tooltip.apply_engaged_hover(
        r, tooltip_engaged.HoverReady(("old",), nested=False, tail="", job_id=None)
    )
    assert r._tip_state is None  # nothing shown


def test_engaged_result_cannot_drive_a_new_hover_job(tmp_path, monkeypatch):
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    i, _tok, _inflected, _mined = _first_content(r)
    old_job = r._interaction_jobs.begin("tooltip")
    r.hover = i
    r._tip_view.job_id = r._interaction_jobs.begin("tooltip")

    tooltip.apply_engaged_hover(
        r, tooltip_engaged.HoverReady(("old",), nested=False, tail="", job_id=old_job)
    )

    assert r._tip_state is None
    assert r._engaged_tooltip.pending is None


# --- nested scan popup: the same tier-3 off-thread treatment (PR A) ------------------------------


def test_nested_cold_miss_defers_and_enqueues_nested(tmp_path, monkeypatch):
    # A cold inner word (scan-hover) with a worker running defers: the getmask2 raster goes off the main
    # thread, no nested popup is shown yet, and a NESTED engaged request is queued.
    from saitenka.app import nested_popup

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    _i, tok, inflected, _mined = _first_content(r)
    r._tip_xy, r._tip_scroll = (0, 0), 0
    r._panel_cache.clear()
    nested_popup.open_nested(r, tok, inflected, nested_popup.Anchor(5, 300, 20), defer=True)
    assert r._nest.state is None  # nothing shown — deferred
    request = submitter.calls[-1]["request"].request
    assert isinstance(request, tooltip_engaged.HoverRequest) and request.nested


def test_nested_no_worker_opens_synchronously(tmp_path, monkeypatch):
    # defer=True but no worker to service it → the nested popup builds synchronously (never a dead defer
    # that shows nothing forever).
    from saitenka.app import nested_popup

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = False
    _i, tok, inflected, _mined = _first_content(r)
    r._tip_xy, r._tip_scroll = (0, 0), 0
    r._panel_cache.clear()
    nested_popup.open_nested(r, tok, inflected, nested_popup.Anchor(5, 300, 20), defer=True)
    assert r._nest.state is not None  # shown synchronously
    assert r._engaged_tooltip.pending is None and r._engaged_tooltip.inflight is None


def test_engaged_nested_composes_warms_bands_without_disk(tmp_path, monkeypatch):
    # The nested worker compose WARMS the nested-cap viewport bands (so the re-show has no synchronous
    # raster) but does NOT persist to disk — the nested viewport is nested-cap-shaped, not the base head
    # the render-cache keys share, so a write would collide.
    from saitenka.app import tooltip

    r, cache = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    _i, tok, inflected, mined = _first_content(r)
    key = tooltip.panel_key(r, tok, inflected, mined=mined)

    r._panel_cache.clear()
    assert r._request_engaged_tooltip(
        tooltip_engaged.HoverRequest(
            tok, inflected, mined, tuple(key), r._tip_cap(), nested=True, tail=tok.surface
        )
    )
    submitter.finish()
    assert cache.stats()[0] == 0  # nested never persisted to disk
    st = r._panel_cache[key]  # panel warmed into the cache
    vh = min(st.full_height, r._cap_for(r.nested_max_frac))
    st.viewport(0, vh, overscan=vh)
    assert (
        st.last_frame_rasters == 0
    )  # bands were rastered off the main thread — the re-show is warm


def test_engaged_nested_drain_reopens_warm(tmp_path, monkeypatch):
    # End-to-end: a cold scan-hover defers → the worker composes → the tick re-derives the anchor from the
    # scan cell and re-opens the now-warm nested popup (a cache hit, no cold raster).
    import threading
    import time
    from types import SimpleNamespace

    from saitenka.app import hover_metadata, nested_popup, tooltip
    from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

    r, cache = _tall_reader(tmp_path, monkeypatch)

    def submit_metadata(**kwargs):
        result = hover_metadata.run_metadata(kwargs["request"], threading.Event())
        kwargs["on_finished"](
            EffectFinished(
                EffectId(1),
                Owner.INTERACTION,
                kwargs["identity"],
                EffectOutcome.SUCCEEDED,
                result=result,
            )
        )
        return True

    r._interaction_metadata_submit = submit_metadata
    submitter = _enable_engaged(r)
    _i, tok, _inflected, _mined = _first_content(r)
    r._tip_xy, r._tip_scroll = (0, 0), 0
    sb = SimpleNamespace(text=tok.surface, x=10, y=10, h=20)
    monkeypatch.setattr(
        tooltip, "scan_hit", lambda _reader, _mx, _my: sb
    )  # re-derive lands on the cell

    r._panel_cache.clear()
    nested_popup.show_nested(r, sb)  # cold → defer (same phrase the worker will build under)
    deadline = time.monotonic() + 1
    while not submitter.calls and time.monotonic() < deadline:
        time.sleep(0.001)
    assert r._nest.state is None and submitter.calls
    submitter.finish()
    assert r._nest.state is not None  # nested popup now shown
    assert cache.stats()[0] == 0  # still never persisted


def test_mined_generation_change_requeues_current_hover_metadata(tmp_path, monkeypatch):
    from saitenka.app.hover_metadata import HoverMetadata

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    index, _tok, _inflected, _mined = _first_content(r)
    requests = []
    monkeypatch.setattr(r, "_request_interaction_metadata", requests.append)
    r.hover = index
    r._tip_view.job_id = r._interaction_jobs.begin("tooltip")
    tooltip._request_hover_metadata(r, index)
    original = requests[-1]
    r._mined_generation += 1
    tooltip.apply_hover_metadata(
        r,
        HoverMetadata(
            original.key,
            phrase_terms=(),
            phrase_span=None,
            mined=False,
            group_mined=(),
        ),
    )

    assert requests[-1].key.mined_generation == r._mined_generation
    assert requests[-1].key.job_id == r._tip_view.job_id


def test_engaged_nested_dropped_when_cursor_left(tmp_path, monkeypatch):
    # A composed nested head whose inner word the cursor has left is dropped — no stale nested flash. With
    # no base tooltip up, scan_hit finds nothing, so the guard drops it cleanly.
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r._tip_state, r._tip_rect = None, None
    tooltip.apply_engaged_hover(
        r, tooltip_engaged.HoverReady(("k",), nested=True, tail="本", job_id=None)
    )
    assert r._nest.state is None


# --- clicked cross-reference navigation: off the main thread too (tier-3) ------------------------


def _base_tip_up(r):
    _i, tok, inflected, mined = _first_content(r)
    r._tip_state = r._panel_for(tok, inflected, min_h=r._tip_cap(), mined=mined)
    return tok


def test_clicked_nav_defers_when_worker_running(tmp_path, monkeypatch):
    # A clicked cross-ref with a worker running enqueues an off-thread nav (build+raster off the click
    # tick) and does NOT swap synchronously — no getmask2 on the tick.
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    tok = _base_tip_up(r)
    tooltip.navigate_tip(r, tok.surface)
    request = submitter.calls[-1]["request"].request
    assert isinstance(request, tooltip_engaged.NavigateRequest) and request.query == tok.surface
    assert r._tip_nav == []  # nothing pushed / swapped yet


def test_clicked_nav_no_worker_navigates_synchronously(tmp_path, monkeypatch):
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = False
    tok = _base_tip_up(r)
    tooltip.navigate_tip(r, tok.surface)
    assert r._engaged_tooltip.inflight is None
    assert len(r._tip_nav) == 1  # synchronous swap pushed the previous view


def test_engaged_nav_composes_then_swaps_from_warm_bands(tmp_path, monkeypatch):
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    tok = _base_tip_up(r)
    old = r._tip_state
    tooltip.navigate_tip(r, tok.surface)  # defer
    submitter.finish()
    assert r._tip_state is not None and r._tip_state is not old  # navigated panel installed
    assert len(r._tip_nav) == 1  # previous view pushed for Esc/back
    assert r._tip_key is None  # a navigated view is keyless


def test_engaged_nav_worker_failure_uses_current_origin_sync_fallback(tmp_path, monkeypatch):
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    tok = _base_tip_up(r)
    old = r._tip_state

    tooltip.navigate_tip(r, tok.surface)
    submitter.finish(outcome=EffectOutcome.FAILED, run=False)

    assert r._tip_state is not None and r._tip_state is not old
    assert len(r._tip_nav) == 1


def test_rejected_new_generation_uses_its_own_sync_fallback(tmp_path, monkeypatch):
    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    tok = _base_tip_up(r)
    old = r._tip_state
    assert r._request_engaged_tooltip(
        tooltip_engaged.HoverRequest(tok, tok.surface, mined=False, key=("old",), cap=r._tip_cap())
    )
    r._prefetch_gen += 1
    r._cancel_engaged_tooltip()
    assert r._request_engaged_tooltip(tooltip_engaged.NavigateRequest(tok.surface, id(old)))
    submitter.reject_next = True

    submitter.finish(outcome=EffectOutcome.FAILED, run=False)

    assert r._tip_state is not None and r._tip_state is not old
    assert len(r._tip_nav) == 1


def test_engaged_nav_dropped_when_tooltip_changed(tmp_path, monkeypatch):
    # origin guard: if the base tooltip changed (a word switch) in the defer window, the composed nav is
    # dropped — never hijacks the new tooltip into the clicked target.
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    tok = _base_tip_up(r)
    tooltip.navigate_tip(r, tok.surface)
    call = submitter.calls.pop(0)
    result = tooltip_engaged.run_engaged(call["request"], threading.Event(), submitter.backend)
    # A word switch in the defer window → a genuinely different panel object under _tip_state.
    j = next(k for k, t in enumerate(r.tokens) if t.is_content and t.surface != tok.surface)
    r._tip_state = r._panel_for(
        r.tokens[j], r._inflected_surface(j), min_h=r._tip_cap(), mined=False
    )
    call["on_finished"](
        EffectFinished(
            EffectId(1), call["owner"], call["identity"], EffectOutcome.SUCCEEDED, result=result
        )
    )
    assert r._tip_nav == []  # not swapped — origin mismatch


def test_stale_engaged_nav_failure_skips_sync_rebuild(tmp_path, monkeypatch):
    from saitenka.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    submitter = _enable_engaged(r)
    tok = _base_tip_up(r)
    tooltip.navigate_tip(r, tok.surface)
    j = next(k for k, t in enumerate(r.tokens) if t.is_content and t.surface != tok.surface)
    r._tip_state = r._panel_for(
        r.tokens[j], r._inflected_surface(j), min_h=r._tip_cap(), mined=False
    )
    rebuilt = []
    monkeypatch.setattr(r, "_navigated_panel", lambda query: rebuilt.append(query))

    submitter.finish(outcome=EffectOutcome.FAILED, run=False)

    assert rebuilt == [] and r._tip_nav == []
