"""Publishing and retiring a hover preserve complete lookup answers."""

from __future__ import annotations

from util import FakeIPC

from saitenka.app.features.tooltip.popups import HoverMetadata, hovered_meta
from saitenka.runtime import events
from saitenka.runtime.interaction_slice import HoveredWordStore


def test_retiring_a_hover_clears_every_field_together() -> None:
    store = HoveredWordStore(FakeIPC())
    answer = HoverMetadata(terms=("本命を",), span=(0, 2), mined=True, group_mined=(True, False))
    store.dispatch(events.HoverWordResolved(answer))
    assert hovered_meta(store) == answer

    store.dispatch(events.HoverWordForgotten())

    meta = hovered_meta(store)
    assert (meta.terms, meta.span, meta.mined, meta.group_mined) == ((), None, False, ())
