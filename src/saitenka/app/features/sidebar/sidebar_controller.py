"""Owner of sidebar state, paint geometry, and surface policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.feature_bindings import SIDEBAR_STATEFUL_BINDING
from saitenka.app.features.sidebar import sidebar
from saitenka.app.interaction.surfaces import ClickTarget, HoverSuppression, SurfaceSpec, WheelStep
from saitenka.model import claims_pointer

if TYPE_CHECKING:
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.interaction_slice import SidebarStore
    from saitenka.runtime.sidebar import SidebarState


class SidebarController:
    def __init__(self, ipc: MpvIPC) -> None:
        self.store: SidebarStore = SIDEBAR_STATEFUL_BINDING.store(ipc)
        self.panel = sidebar.SidebarPanel()

    @property
    def state(self) -> SidebarState:
        return self.store.current

    def surface_state(self) -> SidebarState:
        return self.state

    def suppress_hover(self, suppression: HoverSuppression) -> bool:
        state = self.state
        if not state.open:
            return False
        if not claims_pointer(self.panel.rect, suppression.pointer, open_=state.open):
            return False
        suppression.hide_annotation()
        suppression.release_hover()
        return True

    @staticmethod
    def scroll(wheel: WheelStep, steps: int) -> bool:
        return sidebar.wheel(
            wheel.sidebar,
            steps,
            wheel.pointer,
            hold=wheel.hold_sidebar,
        )

    @staticmethod
    def on_click(target: ClickTarget, x: float, y: float) -> bool:
        return sidebar.click(target.sidebar, target.sidebar_acts, x, y)

    def surface_binding(self) -> SurfaceSpec:
        return SurfaceSpec(
            "sidebar",
            state_of=self.surface_state,
            suppress_hover=self.suppress_hover,
            scroll=self.scroll,
            on_click=self.on_click,
        )
