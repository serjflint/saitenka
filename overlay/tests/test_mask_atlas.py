"""Persistent glyph mask atlas (#149 Tier-1): the serialize round-trip + the glyph_mask read/write seam.

The make-or-break contract is byte-identity — a persisted mask must draw pixel-for-pixel like a fresh
``getmask2`` or text renders wrong. With the atlas OFF (the default), the hot path is untouched (the
existing font/golden tests cover that); these exercise it ON.
"""

from __future__ import annotations

from collections import OrderedDict

import pytest

from overlay import fonts
from overlay.mask_atlas import MaskAtlas, deserialize_core, serialize_core


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


def test_put_is_idempotent_no_duplicate_rows(tmp_path):
    # INSERT OR IGNORE: putting the same key twice keeps ONE row (no duplicate, no error).
    atlas = MaskAtlas.open(tmp_path / "atlas.sqlite")
    assert atlas is not None
    mask = _font().getmask2("見", "L", None, None, None, 0, "ls", 0, (0.0, 0.0), stroke_filled=True)
    atlas.put("f:40:400", "見", "L", (0.0, 0.0), mask)
    atlas.put("f:40:400", "見", "L", (0.0, 0.0), mask)  # same key again
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
