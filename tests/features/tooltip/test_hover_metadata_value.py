"""Hover metadata is one lookup's answer, so it moves as one value.

It used to be four independent attributes on the SessionController, assigned in sequence at four call sites.
Any of those sequences interleaving with a draw publishes a half-updated hover — new phrase terms
against stale mined flags — and no assertion anywhere could see it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from util import FakeIPC

from saitenka.app.features.tooltip.popups import NO_HOVER_METADATA, HoverMetadata, hovered_meta
from saitenka.runtime import events
from saitenka.runtime.interaction_slice import HoveredWordStore


def test_the_empty_metadata_is_every_field_empty() -> None:
    """The clear path is one named constant, not four literals a reader has to recognise."""
    assert HoverMetadata(terms=(), span=None, mined=False, group_mined=()) == NO_HOVER_METADATA


def test_metadata_cannot_be_updated_field_by_field() -> None:
    """Frozen is the mechanism: a partial update has to be a new value, so it cannot be partial."""
    meta = HoverMetadata(terms=("本",), span=(0, 1), mined=True, group_mined=(True,))
    with pytest.raises(FrozenInstanceError):
        meta.mined = False  # type: ignore[misc]


def test_retiring_a_hover_clears_every_field_together() -> None:
    store = HoveredWordStore(FakeIPC())
    store.dispatch(
        events.HoverWordResolved(
            HoverMetadata(terms=("本命を",), span=(0, 2), mined=True, group_mined=(True, False))
        )
    )
    store.dispatch(events.HoverWordForgotten())

    meta = hovered_meta(store)
    assert (meta.terms, meta.span, meta.mined, meta.group_mined) == ((), None, False, ())


def test_the_slice_hands_back_the_value_it_was_given() -> None:
    """Two write paths for one channel is the divergence that makes a fake lie; assert there is one
    — and that the answer round-trips by identity, so nothing rebuilds it field by field."""
    store = HoveredWordStore(FakeIPC())
    meta = HoverMetadata(terms=("読む",), span=(1, 2), mined=False, group_mined=(False,))
    store.dispatch(events.HoverWordResolved(meta))

    assert hovered_meta(store) is meta
    assert hovered_meta(store) is hovered_meta(store)
