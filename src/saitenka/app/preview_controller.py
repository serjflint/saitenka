"""Owner of card-preview state and paint/process state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.card_preview import PreviewPanel
from saitenka.app.feature_bindings import PREVIEW_STATEFUL_BINDING

if TYPE_CHECKING:
    from saitenka.app.surfaces import SurfaceSpec
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.card_preview import CardPreview
    from saitenka.runtime.interaction_slice import PreviewStore


class PreviewController:
    def __init__(self, ipc: MpvIPC) -> None:
        self.store: PreviewStore = PREVIEW_STATEFUL_BINDING.store(ipc)
        self.panel = PreviewPanel()

    @property
    def state(self) -> CardPreview:
        return self.store.current

    def surface_state(self) -> CardPreview:
        return self.state

    def surface_binding(self) -> SurfaceSpec:
        from saitenka.app.surfaces import SurfaceSpec

        return SurfaceSpec("preview", state_of=self.surface_state)
