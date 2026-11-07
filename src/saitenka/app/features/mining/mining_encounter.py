"""Freeze one mining encounter from the current feature-owned facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.mining import miner
from saitenka.app.features.mining.miner import MineCue
from saitenka.app.media import AudioSource, Timespan, media_span
from saitenka.app.subnav_policy import indexed_delay

if TYPE_CHECKING:
    from saitenka.app.features.profiles.profile_controller import ProfileController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.mpvio.ipc import MpvIPC


def selected_audio(
    aid: object, tracks: object, path: object
) -> tuple[AudioSource | None, str | None]:
    if aid is False or aid == "no":
        return None, "audio is disabled"
    if not isinstance(aid, int) or isinstance(aid, bool) or not isinstance(tracks, list):
        return None, "audio selection unavailable"
    track = next(
        (
            t
            for t in tracks
            if isinstance(t, dict) and t.get("type") == "audio" and t.get("id") == aid
        ),
        None,
    )
    if track is None:
        return None, "selected audio track is missing"
    if track.get("external"):
        return None, "external audio capture is unsupported"
    index = track.get("ff-index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        return None, "selected audio stream is unidentified"
    if not isinstance(path, str) or not path:
        return None, "audio source path is missing"
    return AudioSource(path, index), None


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
        authored_span = span
        delay = indexed_delay(value)
        timing_error = None
        if delay is None:
            span, timing_error = None, "unsupported subtitle speed, FPS or play direction"
        elif span is not None:
            try:
                span = media_span(span.start, span.end, delay)
            except ValueError as exc:
                span, timing_error = None, str(exc)
        path, playhead = value("path"), value("time-pos")
        audio, audio_error = selected_audio(value("aid"), value("track-list"), path)
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
            sentence_html=(
                miner.sentence_html(cue.lines)
                if cue.study_lines is None
                else "<br>".join(cue.study_lines)
            ),
            authored_span=authored_span,
            subtitle_delay=delay or 0.0,
            timing_error=timing_error,
            audio_source=audio,
            audio_error=audio_error,
            hovered_terms=tooltip.metadata.terms,
            connection_epoch=state.connection.epoch if self.playback.observing else None,
        )
