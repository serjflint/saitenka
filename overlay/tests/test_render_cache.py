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


def test_byte_ceiling_evicts_least_recently_used(tmp_path):
    # One view compresses tiny (uniform fill → good zlib), so set a ceiling that holds ~2 of them and
    # write 3; the least-recently-used (never re-read) must be the eviction victim.
    cache = _cache(tmp_path)
    cache.put(SIG, "a", 260, 260, 4000, _view(fill=1))
    cache.put(SIG, "b", 260, 260, 4000, _view(fill=2))
    _n, total = cache.stats()
    cache.max_bytes = total + 1  # room for ~the two present, but not a third
    cache.get(SIG, "a", 260, 260)  # touch 'a' → 'b' is now the LRU victim
    cache.put(SIG, "c", 260, 260, 4000, _view(fill=3))
    assert cache.get(SIG, "b", 260, 260) is None  # evicted
    assert cache.get(SIG, "a", 260, 260) is not None
    assert cache.get(SIG, "c", 260, 260) is not None


def test_protected_prewarm_rows_evict_last(tmp_path):
    # The capped-cache fix: a live write-back (protected=False) can only evict another unprotected row,
    # never a prewarmed (protected=True) popular head — so live hovering never thrashes the prewarm set.
    cache = _cache(tmp_path)
    cache.put(SIG, "popular", 260, 260, 4000, _view(fill=1), protected=True)  # prewarmed
    cache.put(SIG, "rare1", 260, 260, 4000, _view(fill=2))  # live write-back
    _n, total = cache.stats()
    cache.max_bytes = total + 1  # room for ~two, not three
    cache.put(SIG, "rare2", 260, 260, 4000, _view(fill=3))  # live: must evict rare1, NOT popular
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

    # The reader uses the cache only WHEN AVAILABLE, so create + inject it (prewarm's role); then a real
    # cold show must direct-paint from it.
    cache = _cache(tmp_path)
    r.session.render_cache.obj, r.session.render_cache.built = cache, True
    sentinel = np.full(
        (cap, w, 4), 123, dtype=np.uint8
    )  # full_h == view_h == cap → no scrollbar mutates it
    cache.put(r._render_cache_sig(), content_key(key), cap, cap, cap, sentinel)

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
