"""Subtitle navigation facts that retire together when the media source changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.subnav_settle import SettleWindow

if TYPE_CHECKING:
    from saitenka_subtitles import Cue, CueIndex


class NavigationState:
    def __init__(self) -> None:
        self.sub_index: CueIndex | None = None
        self.nav_idx = -1
        self.sub_settle = SettleWindow()
        self.nav_prev_text = ""
        self.geometry_cue_hint: Cue | None = None
        self.nav_provisional_cue_counted = False


class NavigationStore:
    """Single writer for the navigation facts scoped to the current media source."""

    def __init__(self) -> None:
        self._current = NavigationState()

    @property
    def current(self) -> NavigationState:
        return self._current

    def get(self) -> NavigationState:
        return self._current

    def replace(self, state: NavigationState | None = None) -> NavigationState:
        self._current = state if state is not None else NavigationState()
        return self._current
