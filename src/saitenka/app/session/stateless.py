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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class StatelessAdapter[InputsT, EffectT](Protocol):
    """A feature's two impure ends; the pure `reduce` between them stays in `<f>_intents.py`."""

    def inputs(self) -> InputsT: ...

    def apply(self, effect: EffectT, /) -> None: ...


@dataclass(frozen=True, slots=True)
class StatelessBinding[CommandT, InputsT, EffectT]:
    """One type-checked command policy and its impure feature endpoint."""

    feature: str
    command_type: type[CommandT]
    reduce: Callable[[CommandT, InputsT], tuple[EffectT, ...]]
    adapter: StatelessAdapter[InputsT, EffectT]

    def run(self, command: object) -> None:
        if not isinstance(command, self.command_type):
            raise TypeError(f"{self.feature} does not accept {type(command).__name__}")
        for effect in self.reduce(command, self.adapter.inputs()):
            self.adapter.apply(effect)


class InstalledStatelessBinding(Protocol):
    @property
    def feature(self) -> str: ...

    @property
    def command_type(self) -> type: ...

    def run(self, command: object) -> None: ...


def bind_stateless[CommandT, InputsT, EffectT](
    feature: str,
    command_type: type[CommandT],
    reduce: Callable[[CommandT, InputsT], tuple[EffectT, ...]],
    adapter: StatelessAdapter[InputsT, EffectT],
) -> StatelessBinding[CommandT, InputsT, EffectT]:
    """Preserve reducer/adapter agreement until the heterogeneous registry boundary."""
    return StatelessBinding(feature, command_type, reduce, adapter)


class StatelessRouter:
    """Dispatch a command to the feature owning its vocabulary.

    Keyed by command type, not by a feature name: each feature already owns a closed `StrEnum` of
    the commands it accepts, so the type *is* the key and no caller can name a feature that was
    never registered. The stateful half keys on `(event type, owner)` for the same reason.
    """

    def __init__(self, features: tuple[InstalledStatelessBinding, ...]) -> None:
        self._features: dict[type, InstalledStatelessBinding] = {}
        for binding in features:
            if binding.command_type in self._features:
                raise ValueError(
                    f"stateless command type already registered: {binding.command_type.__name__}"
                )
            self._features[binding.command_type] = binding

    @property
    def commands(self) -> frozenset[type]:
        return frozenset(self._features)

    def run(self, command: object) -> None:
        entry = self._features.get(type(command))
        if entry is None:
            raise KeyError(f"no stateless feature owns {type(command).__name__}")
        entry.run(command)
