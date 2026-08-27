"""Hover metadata is one lookup's answer, so it moves as one value.

It used to be four independent attributes on the SessionController, assigned in sequence at four call sites.
Any of those sequences interleaving with a draw publishes a half-updated hover — new phrase terms
against stale mined flags — and no assertion anywhere could see it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from util import FakeIPC

from saitenka.app.features.tooltip.popups import NO_HOVER_METADATA, HoverMetadata
from saitenka.app.session.controller import SessionController
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
    """Against the real SessionController, so the route through the slice is exercised too."""
    reader = SessionController(FakeIPC(), prefetch=False, renderer=NullRenderer())
    try:
        reader.tooltip_controller.resolve_word(
            HoverMetadata(terms=("本命を",), span=(0, 2), mined=True, group_mined=(True, False))
        )
        reader.tooltip_controller.forget_word()

        meta = reader.tooltip_controller.observation().metadata
        assert (meta.terms, meta.span, meta.mined, meta.group_mined) == ((), None, False, ())
    finally:
        reader.close()


def test_the_slice_hands_back_the_value_it_was_given() -> None:
    """Two write paths for one channel is the divergence that makes a fake lie; assert there is one
    — and that the answer round-trips by identity, so nothing rebuilds it field by field."""
    reader = SessionController(FakeIPC(), prefetch=False, renderer=NullRenderer())
    try:
        meta = HoverMetadata(terms=("読む",), span=(1, 2), mined=False, group_mined=(False,))
        reader.tooltip_controller.resolve_word(meta)

        assert reader.tooltip_controller.observation().metadata is meta
        assert (
            reader.tooltip_controller.observation().metadata
            is reader.tooltip_controller.observation().metadata
        )
    finally:
        reader.close()
