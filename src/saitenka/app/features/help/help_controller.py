"""Feature owner for the shortcut-reference overlay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.bindings import active_bindings
from saitenka.app.features.help import help_overlay
from saitenka.app.interaction.surfaces import SurfaceSpec
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.runtime import Owner
from saitenka.runtime.help import (
    CloseHelp,
    HelpCommand,
    HelpEffect,
    HelpState,
    OpenHelp,
    ShowHelpPage,
)

if TYPE_CHECKING:
    from saitenka.app.config import KeyOptions
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.interaction_slice import HelpStore


@dataclass(slots=True)
class ScreenState:
    osd: tuple[int, int] = (1280, 720)
    ui_scale: float = 1.0

    def chrome_scale(self) -> float:
        return self.ui_scale * max(1.0, self.osd[1] / 1080)


@dataclass(slots=True)
class TooltipKeyContext:
    bound: bool = False

    def claim(self) -> bool:
        if self.bound:
            return False
        self.bound = True
        return True

    def release(self) -> bool:
        if not self.bound:
            return False
        self.bound = False
        return True


class HelpController:
    """Own help state, command policy, key context, and presentation."""

    def __init__(
        self,
        ipc: MpvIPC,
        surfaces: LifecycleSurfaces,
        keys: KeyOptions,
        screen: ScreenState,
        tooltip_keys: TooltipKeyContext,
        store: HelpStore,
        *,
        ui_scale: float,
    ) -> None:
        self._ipc = ipc
        self._surfaces = surfaces
        self._keys = keys
        self._screen = screen
        self._tooltip_keys = tooltip_keys
        if screen.ui_scale != ui_scale:
            raise ValueError("screen and help UI scales disagree")
        self.store = store

    @property
    def state(self) -> HelpState:
        return self.store.current

    def surface_state(self) -> HelpState:
        return self.state

    def surface_binding(self) -> SurfaceSpec:
        return SurfaceSpec(
            "help",
            state_of=self.surface_state,
            suppress_hover=self.suppress_hover,
            scroll=self.scroll,
        )

    def document(self):
        osd = self._screen.osd
        return help_overlay.help_document(
            active_bindings(self._keys, "global", "tooltip", "mpv"),
            osd=osd,
            close_key=self._keys.help_key,
            scale=self._screen.chrome_scale(),
        )

    def run(self, command: HelpCommand) -> None:
        pages = len(self.document().pages) if self.state.open else 0
        for effect in self.store.dispatch(command, page_count=pages):
            self._apply(effect)

    def page(self, steps: int) -> None:
        self.run(HelpCommand.NEXT if steps > 0 else HelpCommand.PREVIOUS)

    def suppress_hover(self, _suppression: object) -> bool:
        return self.state.open

    def scroll(self, _wheel: object, steps: int) -> bool:
        if not self.state.open:
            return False
        if steps:
            self.page(steps)
        return True

    def redraw(self) -> None:
        if not self.state.open:
            return
        document = self.document()
        self.store.repaginate(len(document.pages))
        image = help_overlay.page_image(document, self.state.page)
        osd = self._screen.osd
        self._surfaces.present(
            image,
            (osd[0] - document.width) // 2,
            (osd[1] - document.height) // 2,
            oid=OverlayId.HELP,
        )

    def _apply(self, effect: HelpEffect) -> None:
        if isinstance(effect, OpenHelp):
            self._bind_keys()
            self.redraw()
        elif isinstance(effect, CloseHelp):
            self._surfaces.remove(OverlayId.HELP)
            self._restore_context_keys()
        elif isinstance(effect, ShowHelpPage):
            self.redraw()

    def _bind_keys(self) -> None:
        for binding in active_bindings(self._keys, "help"):
            if binding.spec.message is not None:
                send_correlated(
                    self._ipc,
                    f"help-keybind:{binding.key}",
                    "keybind",
                    binding.key,
                    f"script-message {binding.spec.message}",
                    owner=Owner.INTERACTION,
                )

    def _restore_context_keys(self) -> None:
        tooltip_by_key = {
            binding.key: binding.spec.message for binding in active_bindings(self._keys, "tooltip")
        }
        for binding in active_bindings(self._keys, "help"):
            message = tooltip_by_key.get(binding.key) if self._tooltip_keys.bound else None
            send_correlated(
                self._ipc,
                f"help-keybind-restore:{binding.key}",
                "keybind",
                binding.key,
                f"script-message {message}" if message else "ignore",
                owner=Owner.INTERACTION,
            )
