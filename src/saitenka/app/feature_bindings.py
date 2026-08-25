"""Mechanism-specific feature registrations shared by runtime and local fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime import Owner
from saitenka.runtime.interaction_slice import (
    HELP_FEATURE,
    HelpFeature,
    HelpReducer,
    HelpStore,
    InteractionRoutePort,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class StatefulBinding[StateT, ReducerT, StoreT]:
    feature: str
    runtime_owner: Owner
    key: str
    reducer_factory: type[ReducerT]
    initial_factory: type[StateT]
    local_store_factory: Callable[..., StoreT]

    def store(self, port: InteractionRoutePort) -> StoreT:
        return self.local_store_factory(port, reducer=self.reducer_factory())


HELP_STATEFUL_BINDING = StatefulBinding(
    feature="help",
    runtime_owner=Owner.INTERACTION,
    key=HELP_FEATURE,
    reducer_factory=HelpReducer,
    initial_factory=HelpFeature,
    local_store_factory=HelpStore,
)
