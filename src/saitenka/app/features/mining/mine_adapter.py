"""The impure ends of the mining commands: token, episode, bookmark."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from saitenka import otel_metrics
from saitenka.app import backlog
from saitenka.app.features.mining import mine_intents
from saitenka.app.intents import Announce

if TYPE_CHECKING:
    from saitenka.app.backlog import CapturePorts
    from saitenka.app.features.mining.mining_controller import MiningController

log = logging.getLogger("saitenka")


class MineHost(Protocol):
    """The command contribution: cue capture plus the bounded mining owner."""

    mining_controller: MiningController

    @property
    def capture_ports(self) -> CapturePorts: ...

    def has_active_cue(self) -> bool: ...

    def toast(self, text: str, kind: str = ..., seconds: float = ...) -> None: ...


class MineAdapter:
    def __init__(self, host: MineHost) -> None:
        self._mining = host.mining_controller
        self._capture = host

    def inputs(self) -> mine_intents.MineInputs:
        mining = self._mining
        return mine_intents.MineInputs(
            has_active_cue=self._capture.has_active_cue(),
            configured=mining.configured,
            target=mining.mine_target() if mining.configured else None,
        )

    def apply(self, effect: mine_intents.MineEffect, /) -> None:
        if isinstance(effect, mine_intents.MineToken):
            log.info("mine: token-index=%d animated=%s", effect.index, effect.animated)
            with otel_metrics.traced("anki_mine", source="base") as span:
                span.set("animated", bool(effect.animated))
                self._mining.mine_index(effect.index, animated=effect.animated)
        elif isinstance(effect, mine_intents.MineEpisode):
            self._mining.bulk_mine()
        elif isinstance(effect, mine_intents.BookmarkCue):
            backlog.capture_current(self._capture.capture_ports)
        elif isinstance(effect, Announce):
            log.info("mine: no target word")
            self._capture.toast(effect.text, effect.kind)
