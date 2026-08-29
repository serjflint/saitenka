"""Registration for the stateless half of the feature set.

The stateful half registers: `SliceReducer({name: reducer})` puts a feature's decision function in a
table and a `RouteKey` says what reaches it, so a new stateful feature is a registration. The
stateless half had no equivalent — its policies are pure `reduce(command, inputs)` functions in
`app/*_intents.py`, and the two impure ends around them had nowhere to live but `SessionController`. Purity
relocates impurity; absent a seam it relocates onto the session shell.

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

    @property
    def policy(self) -> object:
        return self.reduce

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

    @property
    def policy(self) -> object: ...

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


@dataclass(frozen=True, slots=True)
class StatelessCommandRegistration:
    """One script message carrying one typed stateless command."""

    message: str
    command: object


@dataclass(frozen=True, slots=True)
class StatelessCommandEndpoint:
    graph: StatelessCommandGraph
    command: object

    def run(self) -> None:
        self.graph.run(self.command)


class StatelessCommandGraph:
    """The immutable post-construction graph for synchronous command policies."""

    def __init__(
        self,
        features: tuple[InstalledStatelessBinding, ...],
        commands: tuple[StatelessCommandRegistration, ...],
    ) -> None:
        self._router = StatelessRouter(features)
        self._commands: dict[str, object] = {}
        command_types: set[type] = set()
        for row in commands:
            if not row.message:
                raise ValueError("stateless command message must not be empty")
            if row.message in self._commands:
                raise ValueError(f"stateless command already registered: {row.message}")
            command_type = type(row.command)
            if command_type not in self._router.commands:
                raise ValueError(
                    f"stateless command has no installed policy: {command_type.__name__}"
                )
            command_types.add(command_type)
            self._commands[row.message] = row.command
        missing = self._router.commands - command_types
        if missing:
            names = ", ".join(sorted(command_type.__name__ for command_type in missing))
            raise ValueError(f"stateless policies have no script messages: {names}")

    @property
    def command_types(self) -> frozenset[type]:
        return self._router.commands

    def run(self, command: object) -> None:
        self._router.run(command)

    def handler(self, command: object) -> Callable[[], None]:
        """Bind a typed command for another bounded capability value."""
        if type(command) not in self._router.commands:
            raise KeyError(f"no stateless feature owns {type(command).__name__}")
        return StatelessCommandEndpoint(self, command).run

    def handlers(self) -> dict[str, Callable[[], None]]:
        return {message: self.handler(command) for message, command in self._commands.items()}
