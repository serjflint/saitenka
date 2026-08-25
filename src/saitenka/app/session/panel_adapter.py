"""The impure ends of the panel feature: what `panel_intents.reduce` reads, and what it decides.

Registered through `StatelessRouter`, so opening a panel is a command the router owns rather than a
method on the host.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka.app.features.picker import sub_picker
from saitenka.app.features.preview import miner_ui
from saitenka.app.features.sidebar import sidebar
from saitenka.app.intents import DismissHover
from saitenka.app.session import panel_intents

if TYPE_CHECKING:
    from saitenka.app import analysis_overlay
    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.picker.sub_picker import ListingPorts
    from saitenka.app.features.preview.miner_ui import PreviewPorts
    from saitenka.app.features.sidebar.sidebar import SidebarView
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.runtime.picker import PickerState
    from saitenka.runtime.sidebar import SidebarState


class PanelHost(Protocol):
    """Exactly what this feature needs from the host — its whole coupling, in one readable block.

    A `SessionController` parameter would be shorter to write and is what the host inventory sits at zero to
    prevent: it hides which members a feature actually touches, so nothing can tell a narrow
    adapter from a wide one. Declaring the surface makes the adapter constructible from a small
    fake, and makes an adapter that grows a dependency show it here first.
    """

    analysis: analysis_overlay.AnalysisState
    lifecycle_surfaces: LifecycleSurfaces
    picker_controller: PickerController

    def retire_hover(self) -> None: ...

    @property
    def sidebar(self) -> SidebarState: ...

    @property
    def sub_picker(self) -> PickerState: ...

    @property
    def sidebar_view(self) -> SidebarView: ...

    @property
    def listing_ports(self) -> ListingPorts: ...

    @property
    def preview_ports(self) -> PreviewPorts: ...

    def set_analysis_open(self, *, open: bool) -> None:  # noqa: A002 — matches the host's signature
        ...

    def property_value(self, name: str) -> object | None: ...


class PanelAdapter:
    def __init__(self, host: PanelHost) -> None:
        self._host = host

    def inputs(self) -> panel_intents.PanelInputs:
        host = self._host
        states = {
            panel_intents.Panel.SIDEBAR: host.sidebar.open,
            panel_intents.Panel.ANALYSIS: host.analysis.open,
            panel_intents.Panel.SUBTITLE_PICKER: host.sub_picker.open,
        }
        return panel_intents.PanelInputs(
            open_panels=frozenset(panel for panel, is_open in states.items() if is_open)
        )

    def apply(self, effect: panel_intents.PanelEffect, /) -> None:
        if isinstance(effect, DismissHover):
            self._host.retire_hover()
        elif isinstance(effect, panel_intents.ReplayCardPreview):
            miner_ui.replay_preview(self._host.preview_ports)
        elif isinstance(effect, panel_intents.OpenPanel):
            self._set_open(effect.panel, opening=True)
        elif isinstance(effect, panel_intents.ClosePanel):
            self._set_open(effect.panel, opening=False)

    def _set_open(self, panel: panel_intents.Panel, *, opening: bool) -> None:
        host = self._host
        if panel is panel_intents.Panel.SIDEBAR:
            (sidebar.show if opening else sidebar.hide)(host.sidebar_view)
        elif panel is panel_intents.Panel.ANALYSIS:
            host.set_analysis_open(open=opening)
        elif panel is panel_intents.Panel.SUBTITLE_PICKER:
            (
                sub_picker.open_picker(
                    host.listing_ports,
                    host.property_value("path"),
                    retire_hover=host.retire_hover,
                )
                if opening
                else host.picker_controller.close()
            )
        elif panel is panel_intents.Panel.CARD_PREVIEW:
            miner_ui.hide_preview(host.preview_ports)
