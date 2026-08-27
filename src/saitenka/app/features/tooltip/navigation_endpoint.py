"""Tooltip navigation exposed as observations and acts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.tooltip import prefetch

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.help.help_controller import ScreenState


@dataclass(frozen=True, slots=True)
class TooltipNavigationEndpoint:
    screen: ScreenState
    tip_scale_override: float
    tip_max_frac: float
    observe_can_go_back: Callable[[], bool]
    navigate_back: Callable[[], bool]

    def scale(self) -> prefetch.TipScale:
        return prefetch.tip_scale(
            self.screen.osd[1],
            override=self.tip_scale_override,
            max_frac=self.tip_max_frac,
        )

    def can_go_back(self) -> bool:
        return self.observe_can_go_back()

    def back(self) -> None:
        self.navigate_back()
