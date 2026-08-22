"""The impure ends of the mining commands: token, episode, bookmark."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from saitenka import otel_metrics
from saitenka.app import backlog, mine_intents, miner
from saitenka.app.intents import Announce

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka.app.anki import Anki
    from saitenka.app.backlog import CapturePorts
    from saitenka.app.miner import MinerPorts
    from saitenka.app.tokenize import Token

log = logging.getLogger("saitenka")


class MineHost(Protocol):
    """This feature's whole host coupling. See `PanelHost` for why it is spelled out."""

    @property
    def anki(self) -> Anki | None: ...

    @property
    def mine_cfg(self) -> object:
        """Read for truthiness only. A property, not a field: a settable one is invariant, and
        the host's own annotation is narrower than `object`."""
        ...

    @property
    def tokens(self) -> Sequence[Token]: ...

    @property
    def capture_ports(self) -> CapturePorts: ...

    def has_active_cue(self) -> bool: ...

    def mine_target(self) -> int | None: ...

    def mine(self, run: Callable[[MinerPorts], None]) -> None: ...

    def toast(self, text: str, kind: str = ..., seconds: float = ...) -> None: ...


class MineAdapter:
    def __init__(self, host: MineHost) -> None:
        self._host = host

    def inputs(self) -> mine_intents.MineInputs:
        host = self._host
        configured = bool(host.anki and host.mine_cfg)
        return mine_intents.MineInputs(
            has_active_cue=host.has_active_cue(),
            configured=configured,
            # The target is only asked for once mining is possible: `mine_target` inspects hover
            # and cue state, which an unconfigured session has no reason to walk.
            target=host.mine_target() if configured else None,
        )

    def apply(self, effect: object, /) -> None:
        host = self._host
        if isinstance(effect, mine_intents.MineToken):
            token = host.tokens[effect.index]
            # Log the KEY-driven mine (still vs video) — without this, the trace can't tell a
            # Ctrl+Shift+m video-mine from a plain one, and a keypress that reached the handler
            # from one that never did.
            log.info("mine: %r animated=%s", token.surface, effect.animated)
            with otel_metrics.traced("anki_mine", source="base") as span:
                span.set("animated", bool(effect.animated))
                host.mine(lambda p: miner.mine_token(p, token, animated=effect.animated))
        elif isinstance(effect, mine_intents.MineEpisode):
            host.mine(miner.bulk_mine)
        elif isinstance(effect, mine_intents.BookmarkCue):
            backlog.capture_current(host.capture_ports)
        elif isinstance(effect, Announce):
            log.info("mine: no target word")
            host.toast(effect.text, effect.kind)
