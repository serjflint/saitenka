"""Card-preview operations composed from their actual owners and presentation resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.preview import miner_ui
from saitenka.app.features.preview.miner_ui import PreviewPorts
from saitenka.app.features.tooltip import prefetch

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.config import KeyOptions
    from saitenka.app.features.help.help_controller import HelpController, ScreenState
    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.preview.preview_controller import PreviewController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.mpvio.ipc import MpvIPC


@dataclass(frozen=True, slots=True)
class PreviewCommandEndpoint:
    preview: PreviewController
    help: HelpController
    tip_keys_bound: Callable[[], bool]
    mining: MiningController
    surfaces: LifecycleSurfaces
    screen: ScreenState
    ipc: MpvIPC
    keys: KeyOptions
    tip_scale_override: float
    tip_max_frac: float
    play_audio: bool
    cue: CueRenderStore
    playback: PlaybackObservationController
    toast: Callable[[str, str], None]

    def ports(self) -> PreviewPorts:
        tip_width = prefetch.tip_scale(
            self.screen.osd[1],
            override=self.tip_scale_override,
            max_frac=self.tip_max_frac,
        ).width
        return PreviewPorts(
            preview=self.preview,
            help_open=self.help.state.open,
            tip_keys_bound=self.tip_keys_bound(),
            surfaces=self.surfaces,
            osd=self.screen.osd,
            tip_width=tip_width,
            ipc=self.ipc,
            keys=self.keys,
            add_duplicate=self.add_duplicate,
            play_audio=self.play_audio,
        )

    def add_duplicate(self) -> None:
        from saitenka.app.features.mining.mining_controller import ForceDuplicate

        self.mining.force_duplicate(ForceDuplicate(miner_ui.duplicate_token(self.preview.panel)))

    def card_source(self) -> miner_ui.CardSource:
        from saitenka.app.features.mining import miner

        access = self.mining.preview_access()
        return miner_ui.CardSource(
            deck=access.deck,
            model=access.model,
            fields=access.fields,
            note_info=access.note_info,
            fetch_image=access.fetch_image,
            fetch_media=access.fetch_media,
            lines=self.cue.current.lines,
            provenance=lambda video: miner.provenance(
                self.playback.number("time-pos") or 0.0,
                video,
            ),
            video_path=lambda: self.playback.query("path"),
            toast=self.toast,
        )

    def sentence_lines(self) -> list[str]:
        return miner_ui.sentence_lines(self.cue.current.lines)

    def render(self) -> None:
        miner_ui.render_preview(
            self.preview,
            self.surfaces,
            self.screen.osd,
            self.ports().tip_width,
        )

    def hide(self) -> None:
        miner_ui.hide_preview(self.ports())

    def replay(self) -> None:
        miner_ui.replay_preview(self.ports())

    def click(self, x: float, y: float) -> bool:
        return miner_ui.click_preview(self.ports(), x, y)
