"""SessionController state grouped by lifetime — the composition that shrinks the ``controller`` god-object (#30).

An mpv session outlives the file it plays; a hover outlives nothing. Grouping SessionController state by *when it
is born and dies* — session ⊃ episode ⊃ interaction — is what makes a re-slot (swap the episode on a
file change, #100) correct by construction: rebind the context and no prior-episode state can leak. This
module owns the remaining **episode** tier plus the feature-owned collaborators whose state shares
the interaction lifetime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.subnav_settle import SettleWindow

if TYPE_CHECKING:
    from saitenka.app.backlog import BacklogStore
    from saitenka.app.session_stats import SessionRecorder
    from saitenka.subtitles import Cue, CueIndex


class EpisodeContext:
    """State scoped to one played file. Rebuilt on every file change (#100 re-slot) — nothing here may
    outlive the episode."""

    def __init__(self) -> None:
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

    def record_mined(self, count: int) -> None:
        if self.session_recorder is not None:
            self.session_recorder.record_mined(count)


class EpisodeSlot:
    """Stable authority whose episode value is replaced on each file change."""

    def __init__(self) -> None:
        self._current = EpisodeContext()

    @property
    def current(self) -> EpisodeContext:
        return self._current

    def get(self) -> EpisodeContext:
        return self._current

    def replace(self, episode: EpisodeContext | None = None) -> EpisodeContext:
        self._current = episode if episode is not None else EpisodeContext()
        return self._current


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
