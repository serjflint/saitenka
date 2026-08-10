"""Persistent cross-session render cache (#149): the SQLite store + the Panel seed/store round-trip.

The cache persists a cost-gated precomposed first viewport so a cold hover in a later session is
copy+upload, not a head raster. These assert the observable contract: a stored head reads back
byte-identical, a config/geometry mismatch is a safe miss (→ live render), the cost gate keeps short
heads out, and the byte ceiling LRU-evicts. No mpv, no dicts — the store keys on plain strings and the
Panel round-trip uses constructed rows, mirroring test_windowed_prefetch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import numpy as np
from overlay.app.popups import Panel
from overlay.app.render_cache import (
    RenderCache,
    config_signature,
    content_key,
    dict_set_signature,
)
from overlay.panel import Definition, Entry, panel_rows

WIDTH = 384
SIG = "v1|w384|c260|test-dicts"


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
    from overlay.app.config import ReaderOptions, TooltipOptions
    from overlay.app.controller import Reader
    from overlay.app.render_cache import content_key
    from util import FakeIPC

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
    from overlay.app.popups import Panel

    assert isinstance(r._tip_state, Panel)  # …and the real interactive panel was still built after


def test_popular_terms_ranks_by_frequency_dedupes_and_caps():
    # The prewarm population is the top-N by freq rank, most-popular first, de-duped across freq dicts —
    # NOT the whole term dump. A term in two freq dicts takes its best (lowest) rank.
    import sqlite3

    from overlay.app.prewarm import _popular_terms

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
    from overlay.app.render_cache import CompressedView

    return CompressedView(260, 260, 4000, 1, nbytes, bytes([fill]) * nbytes)


def test_compressed_head_cache_evicts_oldest_by_bytes():
    from overlay.app.render_cache import CompressedHeadCache

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
    from overlay.app.render_cache import CompressedHeadCache

    c = CompressedHeadCache(max_bytes=250)
    c.put("a", _cv(100))
    c.put("b", _cv(100))
    c.get("a")  # bump 'a' → 'b' is now the LRU victim
    c.put("c", _cv(100))
    assert c.get("b") is None
    assert c.get("a") is not None and c.get("c") is not None


# --- worker disk→tier-2 hydration + tier-3 poll-deferred render ----------------------------------


def _tall_reader(tmp_path, monkeypatch):
    from overlay.app.config import ReaderOptions, TooltipOptions
    from overlay.app.controller import Reader
    from util import FakeIPC

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    r = Reader(
        FakeIPC(),
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
    import threading

    from overlay.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = True
    r._prefetch_threads = [threading.Thread()]  # a worker is running to service the deferral
    i, _tok, _inflected, _mined = _first_content(r)
    r._panel_cache.clear()  # ensure cold
    tooltip.show_tooltip(r, i)
    assert r._tip_state is None  # nothing shown — deferred
    assert r._engaged_req is not None  # handed to the worker


def test_engaged_render_composes_then_drain_shows_warm(tmp_path, monkeypatch):
    import threading

    from overlay.app import prefetch, tooltip

    r, cache = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = True
    r._prefetch_threads = [threading.Thread()]  # a worker is running to service the deferral
    i, tok, inflected, mined = _first_content(r)
    key = tooltip.panel_key(r, tok, inflected, mined=mined, phrase=r._hover_terms)

    # Cold hover defers → worker composes (panel cache + tier-2 + disk all warmed).
    r._panel_cache.clear()
    tooltip.show_tooltip(r, i)
    assert r._tip_state is None and r._engaged_req is not None
    assert prefetch._try_engaged_hover(r) is True
    assert cache.stats()[0] == 1 and len(r.session.render_cache.mem) == 1

    # Still hovering the same word → the drain shows the now-warm tooltip (a cache hit, no cold raster).
    r.hover = i
    tooltip.apply_engaged_results(r)
    assert r._tip_state is not None  # shown
    assert tuple(r._tip_key) == tuple(key)


def test_engaged_result_discarded_when_word_changed(tmp_path, monkeypatch):
    # Key guard (the analysis-overlay pattern): a composed head for a word the user left is dropped — no
    # tooltip flashes for the wrong word. (tier-2/disk stay warm for a later re-hover.)
    from overlay.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r._engaged_results.put((r._prefetch_gen, ("some", "other", "key"), False, ""))
    r.hover, r._tip_state = -1, None  # not hovering that word anymore
    tooltip.apply_engaged_results(r)
    assert r._tip_state is None  # nothing shown


# --- nested scan popup: the same tier-3 off-thread treatment (PR A) ------------------------------


def test_nested_cold_miss_defers_and_enqueues_nested(tmp_path, monkeypatch):
    # A cold inner word (scan-hover) with a worker running defers: the getmask2 raster goes off the main
    # thread, no nested popup is shown yet, and a NESTED engaged request is queued.
    import threading

    from overlay.app import nested_popup

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = True
    r._prefetch_threads = [threading.Thread()]
    _i, tok, inflected, _mined = _first_content(r)
    r._tip_xy, r._tip_scroll = (0, 0), 0
    r._panel_cache.clear()
    nested_popup.open_nested(r, tok, inflected, nested_popup.Anchor(5, 300, 20), defer=True)
    assert r._nest.state is None  # nothing shown — deferred
    assert r._engaged_req is not None and r._engaged_req.nested is True


def test_nested_no_worker_opens_synchronously(tmp_path, monkeypatch):
    # defer=True but no worker to service it → the nested popup builds synchronously (never a dead defer
    # that shows nothing forever).
    from overlay.app import nested_popup

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = False
    r._prefetch_threads = []
    _i, tok, inflected, _mined = _first_content(r)
    r._tip_xy, r._tip_scroll = (0, 0), 0
    r._panel_cache.clear()
    nested_popup.open_nested(r, tok, inflected, nested_popup.Anchor(5, 300, 20), defer=True)
    assert r._nest.state is not None  # shown synchronously
    assert r._engaged_req is None


def test_engaged_nested_composes_warms_bands_without_disk(tmp_path, monkeypatch):
    # The nested worker compose WARMS the nested-cap viewport bands (so the re-show has no synchronous
    # raster) but does NOT persist to disk — the nested viewport is nested-cap-shaped, not the base head
    # the render-cache keys share, so a write would collide.
    import threading

    from overlay.app import prefetch, tooltip

    r, cache = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = True
    r._prefetch_threads = [threading.Thread()]
    _i, tok, inflected, mined = _first_content(r)
    key = tooltip.panel_key(r, tok, inflected, mined=mined)

    r._panel_cache.clear()
    prefetch.request_engaged_render(
        r, tok, inflected, key, mined=mined, nested=True, tail=tok.surface
    )
    assert prefetch._try_engaged_hover(r) is True
    assert cache.stats()[0] == 0  # nested never persisted to disk
    st = r._panel_cache[key]  # panel warmed into the cache
    vh = min(st.full_height, r._cap_for(r.nested_max_frac))
    st.viewport(0, vh, overscan=vh)
    assert (
        st.last_frame_rasters == 0
    )  # bands were rastered off the main thread — the re-show is warm
    gen, _k, nested, tail = r._engaged_results.get_nowait()
    assert gen == r._prefetch_gen and nested is True and tail == tok.surface


def test_engaged_nested_drain_reopens_warm(tmp_path, monkeypatch):
    # End-to-end: a cold scan-hover defers → the worker composes → the tick re-derives the anchor from the
    # scan cell and re-opens the now-warm nested popup (a cache hit, no cold raster).
    import threading
    from types import SimpleNamespace

    from overlay.app import nested_popup, prefetch, tooltip

    r, cache = _tall_reader(tmp_path, monkeypatch)
    r.prefetch = True
    r._prefetch_threads = [threading.Thread()]
    _i, tok, _inflected, _mined = _first_content(r)
    r._tip_xy, r._tip_scroll = (0, 0), 0
    sb = SimpleNamespace(text=tok.surface, x=10, y=10, h=20)
    monkeypatch.setattr(
        tooltip, "scan_hit", lambda _reader, _mx, _my: sb
    )  # re-derive lands on the cell

    r._panel_cache.clear()
    nested_popup.show_nested(r, sb)  # cold → defer (same phrase the worker will build under)
    assert r._nest.state is None and r._engaged_req is not None and r._engaged_req.nested is True
    assert prefetch._try_engaged_hover(r) is True
    tooltip.apply_engaged_results(r)  # drain → scan_hit → show_nested warm
    assert r._nest.state is not None  # nested popup now shown
    assert cache.stats()[0] == 0  # still never persisted


def test_engaged_nested_dropped_when_cursor_left(tmp_path, monkeypatch):
    # A composed nested head whose inner word the cursor has left is dropped — no stale nested flash. With
    # no base tooltip up, scan_hit finds nothing, so the guard drops it cleanly.
    from overlay.app import tooltip

    r, _cache_obj = _tall_reader(tmp_path, monkeypatch)
    r._tip_state, r._tip_rect = None, None
    r._engaged_results.put((r._prefetch_gen, ("k",), True, "本"))
    tooltip.apply_engaged_results(r)
    assert r._nest.state is None
