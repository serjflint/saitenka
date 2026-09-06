"""Freeze one mining encounter from the current feature-owned facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.mining import miner
from saitenka.app.features.mining.miner import MineCue
from saitenka.app.media import Timespan

if TYPE_CHECKING:
    from saitenka.app.features.profiles.profile_controller import ProfileController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.mpvio.ipc import MpvIPC


@dataclass(frozen=True, slots=True)
class MiningEncounterSource:
    """Sample the semantic owners once at mining admission."""

    ipc: MpvIPC
    cue: CueRenderStore
    tooltip: TooltipController
    profile: ProfileController
    playback: PlaybackObservationController
    max_bulk: int

    def capture(self) -> miner.MiningEncounter:
        cue = self.cue.current
        tooltip = self.tooltip.observation()
        start = self.playback.number("sub-start")
        end = self.playback.number("sub-end")
        span = Timespan(start, end) if start is not None and end is not None else None
        return miner.MiningEncounter(
            cue=MineCue(
                cue.tokens,
                cue.styles,
                tooltip.selected,
                self.profile.tokenizer,
                self.max_bulk,
            ),
            dict_set=self.profile.dict_set,
            ipc=self.ipc,
            media_path=self.playback.text("path"),
            playhead=self.playback.number("time-pos") or 0.0,
            span=span,
            sentence_html=miner.sentence_html(cue.lines),
            hovered_terms=tooltip.metadata.terms,
        )
