"""Impure application seam for reading-profile commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka.app import profile_intents

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka.app.profile_controller import ProfileSwitchOutcome
    from saitenka.app.profiles import Profile


class ProfileHost(Protocol):
    @property
    def profiles(self) -> Sequence[Profile]: ...

    @property
    def profile_index(self) -> int: ...

    def switch_to(self, index: int) -> ProfileSwitchOutcome: ...


class ProfileAdapter:
    def __init__(self, controller: ProfileHost) -> None:
        self._controller = controller

    def inputs(self) -> profile_intents.ProfileInputs:
        controller = self._controller
        return profile_intents.ProfileInputs(
            profile_count=len(controller.profiles),
            profile_index=controller.profile_index,
        )

    def apply(self, effect: profile_intents.ProfileEffect, /) -> None:
        if isinstance(effect, profile_intents.SwitchProfile):
            self._controller.switch_to(effect.index)
