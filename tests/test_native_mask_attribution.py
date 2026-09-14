from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from saitenka_subtitles.libass_backend import extract_token_geometry
from saitenka_subtitles.native_masks import attribute_native_masks
from test_libass_geometry_backend import Layer, Result, request


@given(alpha=st.integers(1, 255), hint_alpha=st.integers(1, 255))
def test_attribution_copies_native_alpha_not_identity_hint(alpha, hint_alpha):
    inputs = request(palette_size=1)
    hints = (Layer(1, 1, bytes([hint_alpha]), 0x01020300, 10, 20),)
    tokens = extract_token_geometry(Result(hints), inputs)

    actual = attribute_native_masks(
        (Layer(2, 1, bytes([alpha, alpha]), 0xFFFFFF00, 10, 20),), hints, inputs, tokens
    )

    assert actual[0].coverage == bytes([alpha, alpha])
    assert actual[0].bounds.width == 2


def test_attribution_preserves_disconnected_mark_and_excludes_reserved_ink():
    inputs = request(palette_size=1)
    hints = (
        Layer(1, 3, b"\xff\0\xff", 0x01020300, 10, 20),
        Layer(1, 1, b"\xff", 0xFFFFFF00, 15, 20),
    )
    tokens = extract_token_geometry(Result(hints), inputs)

    actual = attribute_native_masks(
        (Layer(6, 3, b"\x80\0\0\0\0\x40" + b"\0" * 6 + b"\x20" + b"\0" * 5, 0xFFFFFF00, 10, 20),),
        hints,
        inputs,
        tokens,
    )

    assert actual[0].coverage == b"\x80\0\x20"
    assert actual[0].bounds == tokens[0].bounds


@pytest.mark.parametrize("other_color", [0x04050600, 0xFFFFFF00])
def test_attribution_refuses_native_bridge_between_distinct_owners(other_color):
    inputs = request(palette_size=2 if other_color == 0x04050600 else 1)
    hints = (Layer(1, 1, b"\xff", 0x01020300, 10, 20), Layer(1, 1, b"\xff", other_color, 12, 20))
    tokens = extract_token_geometry(Result(hints), inputs)

    with pytest.raises(ValueError, match="ambiguous connected ink"):
        attribute_native_masks(
            (Layer(3, 1, b"\xff" * 3, 0xFFFFFF00, 10, 20),), hints, inputs, tokens
        )


def test_attribution_refuses_native_ink_without_identity_evidence():
    inputs = request(palette_size=1)
    hints = (Layer(1, 1, b"\xff", 0x01020300, 10, 20),)
    tokens = extract_token_geometry(Result(hints), inputs)

    with pytest.raises(ValueError, match="no owner"):
        attribute_native_masks(
            (Layer(3, 1, b"\xff\0\xff", 0xFFFFFF00, 10, 20),), hints, inputs, tokens
        )


@pytest.mark.parametrize(
    "budget", ["MAX_ATTRIBUTION_PIXELS", "MAX_ATTRIBUTION_RUNS", "MAX_ATTRIBUTION_MASK_BYTES"]
)
def test_attribution_budget_exhaustion_cannot_publish_hint_pixels(monkeypatch, budget):
    monkeypatch.setattr(f"saitenka_subtitles.native_masks.{budget}", 0)
    inputs = request(palette_size=1)
    hints = (Layer(1, 1, b"\xff", 0x01020300, 10, 20),)
    tokens = extract_token_geometry(Result(hints), inputs)

    with pytest.raises(ValueError, match="budget exhausted"):
        attribute_native_masks(hints, hints, inputs, tokens)


def test_original_document_changes_snapshot_identity_but_not_renderer_identity():
    original = replace(request(), native_ass=b"original")
    changed = replace(original, native_ass=b"changed")

    assert changed.cache_key() != original.cache_key()
    assert changed.renderer_key() == original.renderer_key()


def test_sparse_masks_share_one_aggregate_output_budget(monkeypatch):
    monkeypatch.setattr("saitenka_subtitles.native_masks.MAX_ATTRIBUTION_MASK_BYTES", 60)
    inputs = request()
    sparse = b"\xff" + b"\0" * 29 + b"\xff"
    hints = (Layer(31, 1, sparse, 0x01020300, 0, 0), Layer(31, 1, sparse, 0x04050600, 0, 3))
    tokens = extract_token_geometry(Result(hints), inputs)

    with pytest.raises(ValueError, match="aggregate mask budget"):
        attribute_native_masks(hints, hints, inputs, tokens)


def test_fragmented_adjacent_rows_preserve_every_isolated_component():
    inputs = replace(request(palette_size=1), frame_size=(4096, 4), storage_size=(4096, 4))
    pixels = (b"\xff\0" * 2048) * 2
    hints = (Layer(4096, 2, pixels, 0x01020300, 0, 0),)
    tokens = extract_token_geometry(Result(hints), inputs, keep_coverage=True)

    actual = attribute_native_masks(hints, hints, inputs, tokens)

    assert actual[0].coverage == tokens[0].coverage
    assert actual[0].bounds == tokens[0].bounds
