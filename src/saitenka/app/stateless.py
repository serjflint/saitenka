"""Registration for the stateless half of the feature set.

The stateful half registers: `SliceReducer({name: reducer})` puts a feature's decision function in a
table and a `RouteKey` says what reaches it, so a new stateful feature is a registration. The
stateless half had no equivalent — its policies are pure `reduce(command, inputs)` functions in
`app/*_intents.py`, and the two impure ends around them had nowhere to live but `SessionController`. Purity
relocates impurity; absent a seam it relocates onto the object being retired, which is the growth
`poe host-mass` measures.

An adapter is where a feature's host coupling is *allowed* to be. Concentrating it in named objects
is the point: each one is small, countable, and belongs to its feature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class StatelessAdapter(Protocol):
    """A feature's two impure ends; the pure `reduce` between them stays in `<f>_intents.py`."""

    def inputs(self) -> object: ...

    def apply(self, effect: object, /) -> None: ...


#: What one feature registers: its pure policy, and the adapter that feeds and performs it.
type StatelessFeature = tuple[Callable[..., tuple[object, ...]], StatelessAdapter]


class StatelessRouter:
    """Dispatch a command to the feature owning its vocabulary.

    Keyed by command type, not by a feature name: each feature already owns a closed `StrEnum` of
    the commands it accepts, so the type *is* the key and no caller can name a feature that was
    never registered. The stateful half keys on `(event type, owner)` for the same reason.
    """

    def __init__(self, features: dict[type, StatelessFeature]) -> None:
        self._features = dict(features)

    @property
    def commands(self) -> frozenset[type]:
        return frozenset(self._features)

    def run(self, command: object) -> None:
        entry = self._features.get(type(command))
        if entry is None:
            raise KeyError(f"no stateless feature owns {type(command).__name__}")
        reduce, adapter = entry
        for effect in reduce(command, adapter.inputs()):
            adapter.apply(effect)
