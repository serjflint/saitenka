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
        state = self.playback.state

        def value(name: str) -> object:
            return (
                state.value(name)
                if self.playback.observing and state.observes(name)
                else self.playback.query(name)
            )

        start, end = value("sub-start"), value("sub-end")
        span = (
            Timespan(float(start), float(end))
            if isinstance(start, int | float) and isinstance(end, int | float)
            else None
        )
        path, playhead = value("path"), value("time-pos")
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
            media_path=path if isinstance(path, str) else None,
            playhead=float(playhead) if isinstance(playhead, int | float) else 0.0,
            span=span,
            sentence_html=miner.sentence_html(cue.lines),
            hovered_terms=tooltip.metadata.terms,
            connection_epoch=state.connection.epoch if self.playback.observing else None,
        )
