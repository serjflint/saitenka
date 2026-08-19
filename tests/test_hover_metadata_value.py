"""Hover metadata is one lookup's answer, so it moves as one value.

It used to be four independent attributes on the Reader, assigned in sequence at four call sites.
Any of those sequences interleaving with a draw publishes a half-updated hover — new phrase terms
against stale mined flags — and no assertion anywhere could see it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from util import FakeIPC

from saitenka.app.controller import Reader
from saitenka.app.popups import NO_HOVER_METADATA, HoverMetadata
from saitenka.app.subtitle_render import NullRenderer


def test_the_empty_metadata_is_every_field_empty() -> None:
    """The clear path is one named constant, not four literals a reader has to recognise."""
    assert HoverMetadata(terms=(), span=None, mined=False, group_mined=()) == NO_HOVER_METADATA


def test_metadata_cannot_be_updated_field_by_field() -> None:
    """Frozen is the mechanism: a partial update has to be a new value, so it cannot be partial."""
    meta = HoverMetadata(terms=("本",), span=(0, 1), mined=True, group_mined=(True,))
    with pytest.raises(FrozenInstanceError):
        meta.mined = False  # type: ignore[misc]


def test_retiring_a_hover_clears_every_field_together() -> None:
    """Against the real Reader, so the delegation to the tooltip state is exercised too."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())
    try:
        reader._hover_meta = HoverMetadata(
            terms=("本命を",), span=(0, 2), mined=True, group_mined=(True, False)
        )
        reader._hover_meta = NO_HOVER_METADATA

        meta = reader._hover_meta
        assert (meta.terms, meta.span, meta.mined, meta.group_mined) == ((), None, False, ())
    finally:
        reader.close()


def test_the_reader_shim_and_the_tooltip_state_are_the_same_value() -> None:
    """Two write paths for one channel is the divergence that makes a fake lie; assert there is one."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())
    try:
        meta = HoverMetadata(terms=("読む",), span=(1, 2), mined=False, group_mined=(False,))
        reader._hover_meta = meta

        assert reader.tip.hover is meta
        assert reader._hover_meta is reader.tip.hover
    finally:
        reader.close()
