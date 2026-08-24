"""`Owner.PRESENTATION`'s reducer and store.

The two facts are deliberately separate, and the test that matters is the one that pins why: an
auto-reveal ending must not release a track the manual toggle is still holding, so `drawn` moving
must never move `held`.
"""

from __future__ import annotations

import pytest
from util import FakeIPC, runtime_gateway

from saitenka.app.session_routes import install_session_reactor
from saitenka.runtime.events import TranslationDrawn, TranslationHeld
from saitenka.runtime.presentation import TranslationState
from saitenka.runtime.presentation_slice import TranslationReducer, TranslationStore

#: A manual hold, a reveal, a cue change, a take-down, and the hold released.
STREAM = (
    TranslationHeld(held=True),
    TranslationDrawn("I want you to read this."),
    TranslationDrawn("Another line."),
    TranslationDrawn(None),
    TranslationHeld(held=False),
)


def _fold(events) -> TranslationState:
    reducer = TranslationReducer()
    state = TranslationState()
    for event in events:
        state = reducer.reduce(state, event)
    return state


def test_drawing_and_taking_down_never_moves_the_manual_hold() -> None:
    """The whole reason these are two fields. A reveal that ends because the cursor left a word
    must leave `held` alone, or it releases the secondary track out from under the toggle."""
    reducer = TranslationReducer()
    held = reducer.reduce(TranslationState(), TranslationHeld(held=True))

    revealed = reducer.reduce(held, TranslationDrawn("something"))
    taken_down = reducer.reduce(revealed, TranslationDrawn(None))

    assert taken_down.held
    assert taken_down.drawn is None


def test_the_slice_ends_on_what_the_last_declaration_said() -> None:
    assert _fold(STREAM) == TranslationState(held=False, drawn=None)


def test_a_declaration_of_the_same_value_changes_nothing() -> None:
    once = _fold((TranslationHeld(held=True),))

    assert _fold((TranslationHeld(held=True), TranslationHeld(held=True))) == once


def test_the_same_stream_lands_the_same_state_with_or_without_a_reactor(request) -> None:
    """The differential: a session with a reactor keeps the slice in `SessionState.presentation`,
    one without keeps it in the store, and neither may end anywhere else."""
    local = TranslationStore(FakeIPC())

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = TranslationStore(ipc)

    assert [local.dispatch(e) for e in STREAM] == [routed.dispatch(e) for e in STREAM]


def test_a_reactor_owned_slice_refuses_a_write_that_bypasses_it(request) -> None:
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = TranslationStore(ipc)

    with pytest.raises(RuntimeError):
        store.current = TranslationState(held=True)


def test_the_readers_hold_is_the_slice_and_assigning_it_is_a_declaration() -> None:
    """`SessionController.translate_on` is a property over the slot now. Assigning it has to reach the same
    place the toggle does, or a test establishing the precondition sets a copy nothing reads."""
    from saitenka.app.session_controller import SessionController

    reader = SessionController(FakeIPC(), prefetch=False)
    try:
        reader.translate_on = True

        assert reader.translation_store.current.held
        assert reader.translate_on
    finally:
        reader.close()
