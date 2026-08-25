"""Mechanism-specific feature registrations shared by runtime and local fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime import Owner
from saitenka.runtime.events import INTERACTION_EVENTS, EpisodeRetired
from saitenka.runtime.interaction_slice import (
    HELP_ACCEPTS,
    HELP_FEATURE,
    HOVER_ACCEPTS,
    HOVER_PAUSE_ACCEPTS,
    HOVER_PAUSE_FEATURE,
    HOVERED_WORD_ACCEPTS,
    HOVERED_WORD_FEATURE,
    INTERACTION_FEATURE,
    PICKER_ACCEPTS,
    PICKER_FEATURE,
    PREVIEW_ACCEPTS,
    PREVIEW_FEATURE,
    PULSE_ACCEPTS,
    PULSE_FEATURE,
    SIDEBAR_ACCEPTS,
    SIDEBAR_FEATURE,
    TIP_NAV_ACCEPTS,
    TIP_NAV_FEATURE,
    HelpFeature,
    HelpReducer,
    HelpStore,
    HoveredWordFeature,
    HoveredWordReducer,
    HoveredWordStore,
    HoverFeature,
    HoverPauseFeature,
    HoverPauseReducer,
    HoverPauseStore,
    HoverReducer,
    HoverStore,
    InteractionRoutePort,
    PickerFeature,
    PickerReducer,
    PickerStore,
    PreviewFeature,
    PreviewReducer,
    PreviewStore,
    PulseFeature,
    PulseReducer,
    PulseStore,
    SidebarFeature,
    SidebarReducer,
    SidebarStore,
    TipNavFeature,
    TipNavReducer,
    TipNavStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.state import FeatureReducer


@dataclass(frozen=True, slots=True)
class StatefulBinding[StateT, ReducerT: FeatureReducer, StoreT]:
    feature: str
    runtime_owner: Owner
    key: str
    reducer_factory: type[ReducerT]
    initial_factory: type[StateT]
    local_store_factory: Callable[..., StoreT]
    accepted_events: tuple[type[object], ...]

    def build_reducer(self) -> ReducerT:
        return self.reducer_factory()

    def initial(self) -> StateT:
        return self.initial_factory()

    def store(self, port: InteractionRoutePort) -> StoreT:
        return self.local_store_factory(port, reducer=self.reducer_factory())


class InstalledStatefulBinding(Protocol):
    @property
    def feature(self) -> str: ...

    @property
    def runtime_owner(self) -> Owner: ...

    @property
    def key(self) -> str: ...

    def build_reducer(self) -> FeatureReducer: ...

    def initial(self) -> object: ...

    def store(self, port: InteractionRoutePort) -> object: ...

    @property
    def accepted_events(self) -> tuple[type[object], ...]: ...


@dataclass(frozen=True, slots=True)
class OwnerPlan:
    owner: Owner
    feature_order: tuple[str, ...]
    event_vocabulary: tuple[type[object], ...]


def ordered_stateful_bindings(
    plan: OwnerPlan,
    bindings: tuple[InstalledStatefulBinding, ...],
) -> tuple[InstalledStatefulBinding, ...]:
    by_key: dict[str, InstalledStatefulBinding] = {}
    for binding in bindings:
        if binding.runtime_owner is not plan.owner:
            raise ValueError(f"stateful binding belongs to a different owner: {binding.key}")
        if binding.key in by_key:
            raise ValueError(f"stateful feature key already registered: {binding.key}")
        by_key[binding.key] = binding
    if by_key.keys() != set(plan.feature_order):
        raise ValueError("owner plan and stateful bindings disagree")
    accepted = {event for binding in bindings for event in binding.accepted_events}
    if accepted != set(plan.event_vocabulary):
        raise ValueError("owner plan and accepted event vocabulary disagree")
    return tuple(by_key[key] for key in plan.feature_order)


HELP_STATEFUL_BINDING = StatefulBinding(
    feature="help",
    runtime_owner=Owner.INTERACTION,
    key=HELP_FEATURE,
    reducer_factory=HelpReducer,
    initial_factory=HelpFeature,
    local_store_factory=HelpStore,
    accepted_events=HELP_ACCEPTS,
)

HOVER_STATEFUL_BINDING = StatefulBinding(
    "hover",
    Owner.INTERACTION,
    INTERACTION_FEATURE,
    HoverReducer,
    HoverFeature,
    HoverStore,
    HOVER_ACCEPTS,
)
PICKER_STATEFUL_BINDING = StatefulBinding(
    "subtitle-picker",
    Owner.INTERACTION,
    PICKER_FEATURE,
    PickerReducer,
    PickerFeature,
    PickerStore,
    PICKER_ACCEPTS,
)
SIDEBAR_STATEFUL_BINDING = StatefulBinding(
    "sidebar",
    Owner.INTERACTION,
    SIDEBAR_FEATURE,
    SidebarReducer,
    SidebarFeature,
    SidebarStore,
    SIDEBAR_ACCEPTS,
)
TIP_NAV_STATEFUL_BINDING = StatefulBinding(
    "tooltip-navigation",
    Owner.INTERACTION,
    TIP_NAV_FEATURE,
    TipNavReducer,
    TipNavFeature,
    TipNavStore,
    TIP_NAV_ACCEPTS,
)
PULSE_STATEFUL_BINDING = StatefulBinding(
    "copy-pulse",
    Owner.INTERACTION,
    PULSE_FEATURE,
    PulseReducer,
    PulseFeature,
    PulseStore,
    PULSE_ACCEPTS,
)
HOVER_PAUSE_STATEFUL_BINDING = StatefulBinding(
    "hover-pause",
    Owner.INTERACTION,
    HOVER_PAUSE_FEATURE,
    HoverPauseReducer,
    HoverPauseFeature,
    HoverPauseStore,
    HOVER_PAUSE_ACCEPTS,
)
HOVERED_WORD_STATEFUL_BINDING = StatefulBinding(
    "hovered-word",
    Owner.INTERACTION,
    HOVERED_WORD_FEATURE,
    HoveredWordReducer,
    HoveredWordFeature,
    HoveredWordStore,
    HOVERED_WORD_ACCEPTS,
)
PREVIEW_STATEFUL_BINDING = StatefulBinding(
    "card-preview",
    Owner.INTERACTION,
    PREVIEW_FEATURE,
    PreviewReducer,
    PreviewFeature,
    PreviewStore,
    PREVIEW_ACCEPTS,
)

INTERACTION_STATEFUL_BINDINGS: tuple[InstalledStatefulBinding, ...] = (
    HOVER_STATEFUL_BINDING,
    HELP_STATEFUL_BINDING,
    PICKER_STATEFUL_BINDING,
    SIDEBAR_STATEFUL_BINDING,
    TIP_NAV_STATEFUL_BINDING,
    PULSE_STATEFUL_BINDING,
    HOVER_PAUSE_STATEFUL_BINDING,
    HOVERED_WORD_STATEFUL_BINDING,
    PREVIEW_STATEFUL_BINDING,
)
INTERACTION_OWNER_PLAN = OwnerPlan(
    Owner.INTERACTION,
    (
        INTERACTION_FEATURE,
        HELP_FEATURE,
        PICKER_FEATURE,
        SIDEBAR_FEATURE,
        TIP_NAV_FEATURE,
        PULSE_FEATURE,
        HOVER_PAUSE_FEATURE,
        HOVERED_WORD_FEATURE,
        PREVIEW_FEATURE,
    ),
    (*INTERACTION_EVENTS, EpisodeRetired),
)
