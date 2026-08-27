"""Compose mining's outward effects from their existing feature owners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.mining import miner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.preview.miner_ui import CardSource, PreviewPorts
    from saitenka.app.features.preview.preview_controller import PreviewController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipApply, TooltipController


@dataclass(frozen=True, slots=True)
class MiningProjection:
    """Bind mining outcomes to fresh public views of the state owners they affect."""

    toast: Callable[..., object]
    preview: PreviewController
    preview_ports: Callable[[], PreviewPorts]
    card_source: Callable[[], CardSource]
    preview_enabled: Callable[[], bool]
    tooltip: TooltipController
    tooltip_apply: Callable[[], TooltipApply]
    mined_here: Callable[[], None]
    record_mined: Callable[[int], None]

    def build(self) -> miner.MiningApply:
        return miner.MiningApply(
            toast=self.toast,
            reset_capture=self.preview.reset_capture,
            captured_image=self.preview.captured_image,
            captured_audio=self.preview.captured_audio,
            mark_mined=self._mark_mined,
            mined_here=self.mined_here,
            remember_duplicate=self.preview.remember_duplicate,
            preview_existing=self._preview_existing,
            preview_mined=self._preview_mined,
            record_mined=self.record_mined,
        )

    def _mark_mined(self, expression: str) -> None:
        self.tooltip.mark_mined(expression, self.tooltip_apply())

    def _preview_existing(self, note_id: int, card, status: str) -> None:
        self.preview.present_existing(
            self.preview_ports(),
            self.card_source(),
            note_id,
            card,
            status,
            enabled=self.preview_enabled(),
        )

    def _preview_mined(self, card, token, video, status: str = "mined") -> None:
        self.preview.present_mined(
            self.preview_ports(),
            self.card_source(),
            card,
            token,
            video,
            status,
            enabled=self.preview_enabled(),
        )
