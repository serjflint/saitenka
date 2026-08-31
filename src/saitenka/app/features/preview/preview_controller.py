"""Owner of card-preview state and paint/process state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.feature_bindings import PREVIEW_STATEFUL_BINDING
from saitenka.app.features.preview.card_preview import PreviewPanel
from saitenka.app.interaction.surfaces import SurfaceSpec

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from saitenka_tokenize.japanese import Token

    from saitenka.app.features.preview.miner_ui import CardSource, PreviewPorts
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
        return SurfaceSpec("preview", state_of=self.surface_state)

    def reset_capture(self) -> None:
        self.panel.last_jpg = self.panel.last_audio = None

    def captured_image(self, path: Path) -> None:
        self.panel.last_jpg = path

    def captured_audio(self, path: Path) -> None:
        self.panel.last_audio = path

    def remember_duplicate(self, token: Token | None) -> None:
        self.panel.dup_tok = token

    def present_mined(
        self,
        ports: Callable[[], PreviewPorts],
        source: Callable[[], CardSource],
        toast: Callable[..., object],
        card,
        token,
        video,
        status: str = "mined",
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            toast(f"mined {card.expression}")
            return
        from saitenka.app.features.preview import miner_ui

        miner_ui.preview_mined(ports(), source(), card, token, video, status)

    def present_existing(
        self,
        ports: Callable[[], PreviewPorts],
        source: Callable[[], CardSource],
        toast: Callable[..., object],
        note_id: int,
        card,
        status: str,
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            toast(f"already have {card.expression}")
            return
        from saitenka.app.features.preview import miner_ui

        miner_ui.preview_existing(ports(), source(), note_id, card, status)
