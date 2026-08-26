"""Impure application seam for reading-profile commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.profiles import profile_intents

if TYPE_CHECKING:
    from saitenka.app.features.profiles.profile_controller import ProfileController


@dataclass(frozen=True, slots=True)
class ProfileCommandEndpoint:
    controller: ProfileController

    def inputs(self) -> profile_intents.ProfileInputs:
        controller = self.controller
        return profile_intents.ProfileInputs(
            profile_count=len(controller.profiles),
            profile_index=controller.profile_index,
        )

    def apply(self, effect: profile_intents.ProfileEffect, /) -> None:
        if isinstance(effect, profile_intents.SwitchProfile):
            self.controller.switch_to(effect.index)
