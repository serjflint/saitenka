"""The impure ends of the mining commands: token, episode, bookmark."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import backlog
from saitenka.app.features.mining import mine_intents
from saitenka.app.intents import Announce

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.app.toast_controller import NotificationSink
    from saitenka.runtime.playback import PlaybackCueView
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore


@dataclass(frozen=True, slots=True)
class BookmarkCommandEndpoint:
    """Freeze and persist one bookmark from bounded state owners."""

    playback: PlaybackCueView
    cue: CueRenderStore
    tracks: SubtitleTrackStore
    tooltip: TooltipController
    store: Callable[[], backlog.BacklogStore]
    property_value: Callable[[str], object | None]
    number_property: Callable[[str], float | None]
    sequence_property: Callable[[str], list]
    secondary_text: Callable[[], str]
    notifications: NotificationSink
    record_capture: Callable[[], None]

    def has_active_cue(self) -> bool:
        """Return whether the current mpv facts form a bookmarkable cue."""
        return bool(
            self.property_value("path")
            and self.number_property("sub-start") is not None
            and self.number_property("sub-end") is not None
            and self.playback.cue.text.strip()
        )

    def capture(self) -> None:
        cue_facts = self.playback.cue
        track = self.tracks.current
        cue = self.cue.current
        backlog.capture_current(
            backlog.CapturePorts(
                video=self.property_value("path"),
                start=self.number_property("sub-start"),
                end=self.number_property("sub-end"),
                text=cue_facts.text,
                secondary_text=self.secondary_text(),
                language=track.language,
                tokens=cue.tokens,
                hover=self.tooltip.observation().selected,
                jp_sid=track.jp_sid,
                en_sid=track.en_sid,
                tracks=self.sequence_property("track-list"),
                store=self.store,
                toast=self.notifications.show,
                record_capture=self.record_capture,
            )
        )


log = logging.getLogger("saitenka")


@dataclass(frozen=True, slots=True)
class MineCommandPorts:
    """Mining owner plus the separate cue-bookmark and feedback authorities."""

    mining: MiningController
    bookmark: BookmarkCommandEndpoint
    notifications: NotificationSink


class MineCommandCoordinator:
    """Coordinate mining transactions and the distinct bookmark command family."""

    def __init__(self, ports: MineCommandPorts) -> None:
        self._ports = ports

    def inputs(self) -> mine_intents.MineInputs:
        ports = self._ports
        mining = ports.mining
        return mine_intents.MineInputs(
            has_active_cue=ports.bookmark.has_active_cue(),
            configured=mining.configured,
            target=mining.mine_target() if mining.configured else None,
        )

    def apply(self, effect: mine_intents.MineEffect, /) -> None:
        if isinstance(effect, mine_intents.MineToken):
            log.info("mine: token-index=%d animated=%s", effect.index, effect.animated)
            with otel_metrics.traced("anki_mine", source="base") as span:
                span.set("animated", bool(effect.animated))
                self._ports.mining.mine_index(effect.index, animated=effect.animated)
        elif isinstance(effect, mine_intents.MineEpisode):
            self._ports.mining.bulk_mine()
        elif isinstance(effect, mine_intents.BookmarkCue):
            self._ports.bookmark.capture()
        elif isinstance(effect, Announce):
            log.info("mine: no target word")
            self._ports.notifications.show(effect.text, effect.kind)
