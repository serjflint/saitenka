"""SessionController state grouped by lifetime — the composition that shrinks the ``controller`` god-object (#30).

An mpv session outlives the file it plays; a hover outlives nothing. Grouping SessionController state by *when it
is born and dies* — session ⊃ episode ⊃ interaction — is what makes a re-slot (swap the episode on a
file change, #100) correct by construction: rebind the context and no prior-episode state can leak. This
module owns the **episode** tier (and its cohesive sub-clusters, e.g. ``SubtitleSource``) plus the
feature-owned collaborators whose state shares the interaction lifetime.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from saitenka.app.features.tooltip.popups import hovered_meta
from saitenka.app.subnav_settle import SettleWindow

if TYPE_CHECKING:
    from saitenka.app.backlog import BacklogStore
    from saitenka.app.features.tooltip.popups import HoverMetadata, TooltipState
    from saitenka.app.session_stats import SessionRecorder
    from saitenka.app.subtitle_modes import ProviderFetchFactory
    from saitenka.runtime.hover_pause import PauseClaim
    from saitenka.runtime.interaction_slice import (
        HoveredWordStore,
        HoverPauseStore,
        PulseStore,
        TipNavStore,
    )
    from saitenka.runtime.pulse import PulseState
    from saitenka.runtime.tipnav import TipNavState
    from saitenka.subtitles import Cue, CueIndex


class SubtitleSource:
    """The background provider-fetch and retry handshake for one file.

    The *selection* it used to hold moved to `Owner.SUBTITLE`'s slice. What is left is the part no
    reducer can take: a lock and the flag it guards."""

    def __init__(self) -> None:
        self.retry_factory: ProviderFetchFactory | None = None
        self.retry_active = False
        self.retry_lock = threading.Lock()


class TooltipStateOwner(Protocol):
    """The interaction context reads tooltip facts through their bounded owner."""

    @property
    def state(self) -> TooltipState: ...

    @property
    def nav_store(self) -> TipNavStore: ...

    @property
    def pulse_store(self) -> PulseStore: ...

    @property
    def pause_store(self) -> HoverPauseStore: ...

    @property
    def word_store(self) -> HoveredWordStore: ...


class EpisodeContext:
    """State scoped to one played file. Rebuilt on every file change (#100 re-slot) — nothing here may
    outlive the episode."""

    def __init__(self) -> None:
        self.subtitle = SubtitleSource()
        # external sub-index of the JP cue file → Alt+←/→/↓ render the target line INSTANTLY, decoupled
        # from mpv's slow video seek; the real sub-seek fires behind it and reconciles once it settles.
        self.sub_index: CueIndex | None = None
        self.nav_idx = -1  # last cue index jumped to (chaining hint; -1 = unknown)
        # While open, ignore mpv's mid-seek transient sub-text (app/subnav_settle.py).
        self.sub_settle = SettleWindow()
        self.nav_prev_text = ""  # cue text showing right before a nav render (reconcile)
        # The cue a nav jumped to, so a geometry decision uses the target line rather than whatever
        # mpv is still showing mid-seek. Episode-scoped: a hint left over from the previous file
        # would aim the first decision of the new one at a cue that is no longer anywhere.
        self.geometry_cue_hint: Cue | None = None
        self.nav_provisional_cue_counted = False
        # durable per-session recorder (app/session_stats.py); None until stats start on file load
        self.session_recorder: SessionRecorder | None = None


class EpisodeSlot:
    """Stable authority whose episode value is replaced on each file change."""

    def __init__(self) -> None:
        self._current = EpisodeContext()

    @property
    def current(self) -> EpisodeContext:
        return self._current

    def get(self) -> EpisodeContext:
        return self._current

    def subtitle_source(self) -> SubtitleSource:
        return self._current.subtitle

    def replace(self, episode: EpisodeContext | None = None) -> EpisodeContext:
        self._current = episode if episode is not None else EpisodeContext()
        return self._current


class InteractionContext:
    """State scoped to the current on-screen interaction (hover/tooltip).

    Holds the tooltip collaborator whose state shares this volatile interaction lifetime. Other OSD
    surfaces are owned directly by their feature controllers.
    """

    #: Assigned by `SessionController.__init__`; the owner needs runtime/build collaborators this lifetime
    #: container has no business constructing.
    tooltip: TooltipStateOwner

    @property
    def tip_nav(self) -> TipNavState:
        return self.tooltip.nav_store.current

    @property
    def tip(self) -> TooltipState:
        """Read-only surface projection of the tooltip feature's owned state."""
        return self.tooltip.state

    @property
    def nav_store(self) -> TipNavStore:
        return self.tooltip.nav_store

    @property
    def pulse_store(self) -> PulseStore:
        return self.tooltip.pulse_store

    @property
    def pause_store(self) -> HoverPauseStore:
        return self.tooltip.pause_store

    @property
    def word_store(self) -> HoveredWordStore:
        return self.tooltip.word_store

    @property
    def copy_pulse(self) -> PulseState:
        return self.tooltip.pulse_store.current

    @property
    def hover_pause(self) -> PauseClaim:
        return self.tooltip.pause_store.current

    @property
    def hovered_word_meta(self) -> HoverMetadata:
        return hovered_meta(self.tooltip.word_store)


class SessionContext:
    """Shared session state not already owned by a bounded feature controller."""

    def __init__(self) -> None:
        self.anki_cache: tuple[float, bool] = (0.0, False)
        self.backlog_store: BacklogStore | None = None  # lazy review-backlog DB handle

    def ensure_backlog_store(self) -> BacklogStore:
        """Open the session-owned backlog store on first use."""
        store = self.backlog_store
        if store is None:
            from saitenka.app.backlog import BacklogStore

            store = self.backlog_store = BacklogStore()
        return store
