"""Compose mining's outward effects from their existing feature owners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.mining import miner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.preview.preview_controller import PreviewController
    from saitenka.app.features.preview.preview_endpoint import PreviewCommandEndpoint
    from saitenka.app.features.tooltip.tooltip_controller import TooltipApply, TooltipController
    from saitenka.app.toast_controller import NotificationSink


@dataclass(frozen=True, slots=True)
class MiningProjection:
    """Bind mining outcomes to fresh public views of the state owners they affect."""

    notifications: NotificationSink
    preview: PreviewController
    preview_endpoint: Callable[[], PreviewCommandEndpoint]
    tooltip: TooltipController
    tooltip_apply: Callable[[], TooltipApply]
    mined_here: Callable[[], None]
    record_mined: Callable[[int], None]

    def build(self) -> miner.MiningApply:
        return miner.MiningApply(
            toast=self.notifications.show,
            reset_capture=self.preview.reset_capture,
            captured_image=self.preview.captured_image,
            captured_audio=self.preview.captured_audio,
            mark_mined=self._mark_mined,
            mined_here=self.mined_here,
            remember_duplicate=self.preview.remember_duplicate,
            preview_existing=self._preview_existing,
            preview_mined=self._preview_mined,
            record_mined=self.record_mined,
            record_link=lambda *_args: None,
        )

    def _mark_mined(self, expression: str) -> None:
        self.tooltip.mark_mined(expression, self.tooltip_apply())

    def _preview_existing(self, note_id: int, card, status: str) -> None:
        endpoint = self.preview_endpoint()
        self.preview.present_existing(
            endpoint.ports,
            endpoint.card_source,
            self.notifications.show,
            note_id,
            card,
            status,
            enabled=endpoint.mining.show_preview,
        )

    def _preview_mined(self, card, token, video, status: str = "mined") -> None:
        endpoint = self.preview_endpoint()
        self.preview.present_mined(
            endpoint.ports,
            endpoint.card_source,
            self.notifications.show,
            card,
            token,
            video,
            status,
            enabled=endpoint.mining.show_preview,
        )
