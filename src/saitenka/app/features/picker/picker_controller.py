"""Owner of subtitle-picker state, paint geometry, and surface policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.feature_bindings import PICKER_STATEFUL_BINDING
from saitenka.app.features.picker import sub_picker
from saitenka.app.interaction.surfaces import ClickTarget, HoverSuppression, SurfaceSpec, WheelStep
from saitenka.model import claims_pointer
from saitenka.runtime import events

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.config import KeyOptions
    from saitenka.app.features.help.help_controller import ScreenState
    from saitenka.app.features.subtitle.navigation_state import NavigationStore
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.interaction_slice import PickerStore
    from saitenka.runtime.picker import PickerState


class PickerController:
    def __init__(
        self,
        ipc: MpvIPC,
        surfaces: LifecycleSurfaces,
        screen: ScreenState,
        keys: KeyOptions,
        *,
        ui_scale: float,
    ) -> None:
        self.store: PickerStore = PICKER_STATEFUL_BINDING.store(ipc)
        self.panel = sub_picker.PickerPanel()
        self._surfaces = surfaces
        self._screen = screen
        self._keys = keys
        self._ui_scale = ui_scale
        self.lister: Callable[[str], tuple] | None = None
        self.submitter = sub_picker.configure_runtime_job(ipc)

    @property
    def state(self) -> PickerState:
        return self.store.current

    def surface_state(self) -> PickerState:
        return self.state

    def redraw(self) -> None:
        state = self.state
        if not state.open:
            return
        osd = self._screen.osd
        scale = self._ui_scale * max(1.0, osd[1] / 1080)
        rendered, x, y, width, height = sub_picker.picker_panel(
            state,
            osd=osd,
            scale=scale,
            close_key=self._keys.sub_picker_key,
        )
        self.panel.rect = (x, y, width, height)
        self.panel.hits = rendered.hitboxes
        self._surfaces.present(rendered.image, x, y, oid=sub_picker.PICKER_ID)

    def close(self) -> None:
        sub_picker.close_picker(self.store, self.panel, self._surfaces)

    def configure_listing(self, lister: Callable[[str], tuple] | None) -> None:
        self.close()
        self.lister = lister

    def open(
        self,
        video: object,
        *,
        retire_hover: Callable[[], None],
        navigation: NavigationStore,
        stop: threading.Event,
        toast: Callable[..., None],
    ) -> None:
        sub_picker.open_picker(
            self.listing_ports(navigation=navigation, stop=stop, toast=toast),
            video,
            retire_hover=retire_hover,
        )

    def listing_ports(
        self,
        *,
        navigation: NavigationStore,
        stop: threading.Event,
        toast: Callable[..., None],
    ) -> sub_picker.ListingPorts:
        return sub_picker.ListingPorts(
            lister=self.lister,
            store=self.store,
            redraw=self.redraw,
            submit=self.submitter,
            stop=stop,
            current_episode=navigation.get,
            toast=toast,
        )

    def suppress_hover(self, suppression: HoverSuppression) -> bool:
        state = self.state
        if not state.open:
            return False
        if not claims_pointer(self.panel.rect, suppression.pointer, open_=state.open):
            return False
        suppression.release_hover()
        return True

    def scroll(self, wheel: WheelStep, steps: int) -> bool:
        state = self.state
        if not state.open:
            return False
        if not claims_pointer(self.panel.rect, wheel.pointer, open_=state.open):
            return False
        self.store.dispatch(
            events.PickerScrolled(steps, len(sub_picker.listing_of(state).candidates))
        )
        self.redraw()
        return True

    def on_click(self, target: ClickTarget, x: float, y: float) -> bool:
        state = self.state
        panel = self.panel
        if not sub_picker.contains(state, panel, x, y) or panel.rect is None:
            return False
        local_x, local_y = x - panel.rect[0], y - panel.rect[1]
        hit = next((box for box in panel.hits if box.contains(local_x, local_y)), None)
        if hit is not None and hit.kind == "picker-download":
            sub_picker.download_candidate(
                state,
                self.store,
                self.panel,
                hit.value,
                target.download,
            )
        return True

    def surface_binding(self) -> SurfaceSpec:
        return SurfaceSpec(
            "sub_picker",
            state_of=self.surface_state,
            suppress_hover=self.suppress_hover,
            scroll=self.scroll,
            on_click=self.on_click,
            click_without_cue=True,
        )
