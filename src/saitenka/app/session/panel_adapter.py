"""The impure ends of the panel feature: what `panel_intents.reduce` reads, and what it decides.

Registered through `StatelessRouter`, so opening a panel is a command the router owns rather than a
method on the host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app.intents import DismissHover
from saitenka.app.session import panel_intents

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.preview.preview_endpoint import PreviewCommandEndpoint
    from saitenka.app.features.sidebar.sidebar_controller import SidebarController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces


class AnalysisPanelControl(Protocol):
    @property
    def open(self) -> bool: ...

    def set_open(self, *, open: bool) -> None: ...  # noqa: A002


@dataclass(frozen=True, slots=True)
class PanelCommandPorts:
    """Owners and acts participating in the panel-arbitration conjunction."""

    analysis: AnalysisPanelControl
    surfaces: LifecycleSurfaces
    sidebar: SidebarController
    picker: PickerController
    preview: PreviewCommandEndpoint
    retire_hover: Callable[[], None]
    show_sidebar: Callable[[], None]
    hide_sidebar: Callable[[], None]
    open_picker: Callable[[], None]


class PanelCommandCoordinator:
    """Apply ordered panel commands across independent surface owners."""

    def __init__(self, ports: PanelCommandPorts) -> None:
        self._ports = ports

    def inputs(self) -> panel_intents.PanelInputs:
        ports = self._ports
        states = {
            panel_intents.Panel.SIDEBAR: ports.sidebar.state.open,
            panel_intents.Panel.ANALYSIS: ports.analysis.open,
            panel_intents.Panel.SUBTITLE_PICKER: ports.picker.state.open,
        }
        return panel_intents.PanelInputs(
            open_panels=frozenset(panel for panel, is_open in states.items() if is_open)
        )

    def apply(self, effect: panel_intents.PanelEffect, /) -> None:
        if isinstance(effect, DismissHover):
            self._ports.retire_hover()
        elif isinstance(effect, panel_intents.ReplayCardPreview):
            self._ports.preview.replay()
        elif isinstance(effect, panel_intents.OpenPanel):
            self._set_open(effect.panel, opening=True)
        elif isinstance(effect, panel_intents.ClosePanel):
            self._set_open(effect.panel, opening=False)

    def _set_open(self, panel: panel_intents.Panel, *, opening: bool) -> None:
        ports = self._ports
        if panel is panel_intents.Panel.SIDEBAR:
            (ports.show_sidebar if opening else ports.hide_sidebar)()
        elif panel is panel_intents.Panel.ANALYSIS:
            ports.analysis.set_open(open=opening)
        elif panel is panel_intents.Panel.SUBTITLE_PICKER:
            (ports.open_picker if opening else ports.picker.close)()
        elif panel is panel_intents.Panel.CARD_PREVIEW:
            ports.preview.hide()
