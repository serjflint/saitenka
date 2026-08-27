"""Persistent glyph mask atlas (#149 Tier-1): the serialize round-trip + the glyph_mask read/write seam.

The make-or-break contract is byte-identity — a persisted mask must draw pixel-for-pixel like a fresh
``getmask2`` or text renders wrong. With the atlas OFF (the default), the hot path is untouched (the
existing font/golden tests cover that); these exercise it ON.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import pytest

from saitenka import fonts
from saitenka.app import mask_atlas_startup
from saitenka.app.features.tooltip.preparation import (
    PersistentHeadCache,
    TooltipPreparationConfig,
)
from saitenka.mask_atlas import MaskAtlas, deserialize_core, serialize_core
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


def _font(char: str = "見", size: int = 40):
    return fonts.load(fonts.FontSpec(fonts.font_for_char(char), size))


def _raw(core) -> bytes:
    return bytes(core)


def test_serialize_core_round_trips_byte_identical():
    core, _off = _font().getmask2(
        "見", "L", None, None, None, 0, "ls", 0, (0.0, 0.0), stroke_filled=True
    )
    w, h, data = serialize_core(core)
    core2 = deserialize_core(w, h, "L", data)
    assert core2.size == core.size
    assert _raw(core2) == _raw(core)  # a loaded mask is the same stencil → draws identically


@pytest.fixture
def _atlas_off():
    # Always restore the module globals so a failure never leaks the atlas into other tests.
    yield
    fonts.set_mask_atlas(None, None)


@pytest.mark.usefixtures("_atlas_off")
def test_atlas_hit_reuses_mask_without_calling_getmask2(tmp_path, monkeypatch):
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    font = _font()
    gid = font._satk_font_id  # stamped by load()
    fresh = fonts.glyph_mask(font, "見", "L", (0.0, 0.0))
    atlas.put(gid, "見", "L", (0.0, 0.0), fresh)

    mem: dict = {}
    assert atlas.load_into(mem) == 1
    fonts.set_mask_atlas(mem, None)
    fonts._tls.masks = OrderedDict()  # drop the per-thread memo → force the atlas path

    def _boom(*_a, **_k):
        raise AssertionError("getmask2 was called — the atlas hit should have skipped it")

    monkeypatch.setattr(font, "getmask2", _boom)
    got = fonts.glyph_mask(font, "見", "L", (0.0, 0.0))
    assert _raw(got[0]) == _raw(fresh[0]) and got[1] == fresh[1]  # byte-identical, from disk


@pytest.mark.usefixtures("_atlas_off")
def test_write_back_persists_a_live_miss(tmp_path):
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    font = _font("語")
    fonts.set_mask_atlas(None, atlas)  # write-only
    fonts._tls.masks = OrderedDict()
    fonts.glyph_mask(font, "語", "L", (0.0, 0.0))  # a miss → written back
    assert atlas.count() == 1
    mem: dict = {}
    atlas.load_into(mem)
    assert any(k[1] == "語" for k in mem)  # keyed by (font_id, text, mode, sx, sy)


@pytest.mark.usefixtures("_atlas_off")
def test_lazy_get_one_reuses_mask_without_bulk_load(tmp_path, monkeypatch):
    # The runtime path: no load_into / no shared mem dict — glyph_mask reads the atlas per-glyph and
    # must still skip getmask2 on a hit (byte-identical), so a session never bulk-loads the atlas.
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    font = _font()
    gid = font._satk_font_id
    fresh = fonts.glyph_mask(font, "見", "L", (0.0, 0.0))
    atlas.put(gid, "見", "L", (0.0, 0.0), fresh)

    fonts.set_mask_atlas(None, atlas)  # lazy read source (no mem dict), also write-back
    fonts._tls.masks = OrderedDict()  # drop the per-thread memo → force the atlas path

    def _boom(*_a, **_k):
        raise AssertionError("getmask2 was called — the lazy atlas hit should have skipped it")

    monkeypatch.setattr(font, "getmask2", _boom)
    got = fonts.glyph_mask(font, "見", "L", (0.0, 0.0))
    assert _raw(got[0]) == _raw(fresh[0]) and got[1] == fresh[1]  # byte-identical, from disk lazily


def test_get_one_miss_then_hit_round_trips(tmp_path):
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    assert atlas.get_one("f:40:400", "見", "L", (0.0, 0.0)) is None  # empty atlas → miss, no error
    mask = _font().getmask2("見", "L", None, None, None, 0, "ls", 0, (0.0, 0.0), stroke_filled=True)
    atlas.put("f:40:400", "見", "L", (0.0, 0.0), mask)
    got = atlas.get_one("f:40:400", "見", "L", (0.0, 0.0))
    assert got is not None
    assert _raw(got[0]) == _raw(mask[0]) and got[1] == mask[1]  # byte-identical single-glyph read


@pytest.mark.usefixtures("_atlas_off")
def test_runtime_activation_is_lazy_never_bulk_loads(tmp_path, monkeypatch):
    # Regression: startup must wire the atlas as a LAZY per-glyph reader, NEVER bulk-load it into RAM.
    # An 864 MB / 587k-mask `load_into` stalled startup ~9s (the "saitenka starting…" hang). Guard: the
    # startup actor opens the atlas, points fonts at it for lazy reads + write-back, and never bulk-loads.
    import saitenka.mask_atlas as mask_atlas_mod

    atlas_path = tmp_path / "mask-atlas.sqlite"  # a prebuilt atlas the session should read lazily
    seed = MaskAtlas.open(atlas_path)
    assert seed is not None
    font = _font()
    seed.put(
        font._satk_font_id, "見", "L", (0.0, 0.0), fonts.glyph_mask(font, "見", "L", (0.0, 0.0))
    )
    seed.close()

    def _boom(*_a, **_k):
        raise AssertionError("load_into was called — startup must NOT bulk-load the atlas into RAM")

    monkeypatch.setattr(mask_atlas_mod.MaskAtlas, "load_into", _boom)

    fonts.set_mask_atlas(None, None)  # start from a clean slate
    opened = mask_atlas_startup.open_mask_atlas(
        mask_atlas_startup.MaskAtlasRequest(enabled=True, path=atlas_path),
        threading.Event(),
    )
    assert isinstance(opened, mask_atlas_startup.OpenedMaskAtlas)
    target = PersistentHeadCache(
        TooltipPreparationConfig(
            enabled=False,
            workers=0,
            cue_lookahead=0,
            head_lookahead=0,
            head_queue_max=1,
            cache_enabled=False,
            cache_max_bytes=0,
            cache_min_height=0,
            mask_atlas_enabled=True,
        )
    )
    assert target.install_mask_atlas(opened)

    assert fonts._ATLAS_MEM is None  # no bulk read dict loaded into RAM
    assert fonts._ATLAS_WRITE is target.mask_atlas  # atlas = lazy read + write-back
    # and it actually reads lazily: the seeded glyph resolves through get_one, not getmask2
    fonts._tls.masks = OrderedDict()

    def _no_raster(*_a, **_k):
        raise AssertionError("getmask2 called — the lazy atlas hit should have served it")

    monkeypatch.setattr(font, "getmask2", _no_raster)
    assert fonts.glyph_mask(font, "見", "L", (0.0, 0.0)) is not None


def test_runtime_activation_rejects_a_late_result_after_close(tmp_path):
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    state = mask_atlas_startup.ActivationState(generation=1, inflight=True)
    mask_atlas_startup.close(state)

    result = mask_atlas_startup.finish(
        state,
        EffectFinished(
            EffectId(1),
            Owner.SESSION,
            ("mask-atlas-startup", 1),
            EffectOutcome.SUCCEEDED,
            result=mask_atlas_startup.OpenedMaskAtlas(atlas),
        ),
    )

    assert result is None


def test_runtime_activation_admits_only_one_startup_job(tmp_path):
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return True

    state = mask_atlas_startup.ActivationState()
    request = mask_atlas_startup.MaskAtlasRequest(enabled=True, path=tmp_path / "atlas.sqlite")

    assert mask_atlas_startup.request(state, request, submit, lambda _completion: None)
    assert not mask_atlas_startup.request(state, request, submit, lambda _completion: None)
    assert len(calls) == 1


def test_runtime_activation_opens_only_after_its_correlated_terminal(tmp_path):
    path = tmp_path / "atlas.sqlite"
    seed = MaskAtlas.open(path)
    assert seed is not None
    seed.close()
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return True

    state = mask_atlas_startup.ActivationState()
    request = mask_atlas_startup.MaskAtlasRequest(enabled=True, path=path)
    assert mask_atlas_startup.request(state, request, submit, lambda _completion: None)
    opened = mask_atlas_startup.open_mask_atlas(request, threading.Event())
    completion = EffectFinished(
        EffectId(7),
        Owner.SESSION,
        calls[0]["identity"],
        EffectOutcome.SUCCEEDED,
        result=opened,
    )

    assert isinstance(
        mask_atlas_startup.finish(state, completion), mask_atlas_startup.OpenedMaskAtlas
    )


def test_put_is_idempotent_no_duplicate_rows(tmp_path):
    # INSERT OR IGNORE: putting the same key twice keeps ONE row (no duplicate, no error).
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    mask = _font().getmask2("見", "L", None, None, None, 0, "ls", 0, (0.0, 0.0), stroke_filled=True)
    atlas.put("f:40:400", "見", "L", (0.0, 0.0), mask)
    atlas.put("f:40:400", "見", "L", (0.0, 0.0), mask)  # same key again
    assert atlas.count() == 1


def test_put_counts_new_vs_already_cached(tmp_path):
    # The prewarm "already cached" signal: a fresh key is inserted; the same key again is an IGNORE'd
    # no-op — counted separately so a re-scale run can show masks were re-rastered but already present.
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    mask = _font().getmask2("見", "L", None, None, None, 0, "ls", 0, (0.0, 0.0), stroke_filled=True)
    atlas.put("f:40:400", "見", "L", (0.0, 0.0), mask)
    assert (atlas.inserted, atlas.ignored) == (1, 0)  # stored a new mask
    atlas.put("f:40:400", "見", "L", (0.0, 0.0), mask)  # identical key → already cached
    assert (atlas.inserted, atlas.ignored) == (1, 1)  # one stored, one re-cached
    assert atlas.count() == 1


def test_done_ledger_marks_and_probes(tmp_path):
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    assert not atlas.is_done(1.5, "門")  # nothing marked yet
    atlas.mark_done(1.5, "門")
    assert atlas.is_done(1.5, "門")
    atlas.mark_done(1.5, "門")  # idempotent — no error, still done
    assert atlas.is_done(1.5, "門")


def test_done_ledger_is_scale_scoped(tmp_path):
    # 1.5 masks ≠ 2.0 masks, so a word done at one scale must NOT count as done at another.
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    atlas.mark_done(1.5, "経")
    assert atlas.is_done(1.5, "経")
    assert not atlas.is_done(2.0, "経")  # different scale → still needs rastering


def test_done_ledger_survives_reopen(tmp_path):
    # Durable (committed) — a stopped prewarm's progress persists so a re-run can skip finished words.
    path = tmp_path / "atlas.sqlite"
    a1 = MaskAtlas.open(path)
    assert a1 is not None
    a1.mark_done(1.5, "読")
    a1.close()
    a2 = MaskAtlas.open(path)
    assert a2 is not None
    assert a2.is_done(1.5, "読")


def test_backfill_reference_done_marks_native_words_at_the_reference_scale(tmp_path):
    # A native-scale done marker implies its 1× reference pass ran, so backfill marks those words done
    # at the reference scale too — letting a scale-1.0 run skip what a 1.5 (or 2.0) run already built.
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    atlas.mark_done(1.5, "経")
    atlas.mark_done(2.0, "済")
    assert not atlas.is_done(1.0, "経")  # no reference marker before backfill
    assert atlas.backfill_reference_done() == 2  # both native words gain a reference marker
    assert atlas.is_done(1.0, "経") and atlas.is_done(1.0, "済")
    assert atlas.backfill_reference_done() == 0  # idempotent — nothing new the second time


def test_done_words_returns_the_scale_scoped_set(tmp_path):
    # The prewarm startup summary intersects this with the term list for its already-done / remaining split.
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    atlas.mark_done(1.5, "門")
    atlas.mark_done(1.5, "経")
    atlas.mark_done(2.0, "読")  # a different scale — excluded from the 1.5 set
    assert atlas.done_words(1.5) == {"門", "経"}
    assert atlas.done_words(2.0) == {"読"}
    assert atlas.done_words(3.0) == set()  # nothing at this scale


def test_disk_bytes_grows_with_stored_masks(tmp_path):
    # The heartbeat reports this as the real footprint (the atlas is uncapped) — it must be a positive,
    # non-decreasing size, not the placeholder 0 the atlas-only path used to send.
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    empty = atlas.disk_bytes()
    assert empty > 0  # even a fresh DB has header/schema pages
    mask = _font().getmask2("見", "L", None, None, None, 0, "ls", 0, (0.0, 0.0), stroke_filled=True)
    for i in range(200):  # enough rows to spill onto new pages
        atlas.put(f"f:{i}:400", "見", "L", (0.0, 0.0), mask)
    atlas.checkpoint()  # fold the WAL back so page_count reflects the writes
    assert atlas.disk_bytes() > empty
