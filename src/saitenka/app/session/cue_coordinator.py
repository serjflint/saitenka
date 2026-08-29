"""Named cross-owner transactions for one cue and one episode slot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.runtime import CueCommandState
from saitenka.runtime import events, playback

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.subtitle.navigation_state import NavigationState

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CueTransactions:
    settle_interaction: Callable[[], None]
    current_text: Callable[[], str]
    reconcile_text: Callable[[str], None]
    revision: Callable[[], int]
    reduce_playback: Callable[[events.PlaybackEvent], None]
    retire_settle_window: Callable[[], None]
    retire_annotation_cue: Callable[[], bool]
    teardown_tooltip: Callable[[], None]
    retire_tooltip_selection: Callable[[], None]
    reset_cue_render: Callable[[], None]
    close_picker: Callable[[], None]
    retire_acquisition_episode: Callable[[], None]
    retire_annotation_warm: Callable[[], None]
    retire_translation_episode: Callable[[], None]
    playback_routed: Callable[[], bool]
    retire_playback_episode: Callable[[], None]
    retire_subtitle_episode: Callable[[], None]
    retire_tooltip_episode: Callable[[], None]
    replace_navigation: Callable[[], NavigationState]


class CueCoordinator:
    """Expose the ordering that must span cue/episode owners; own no feature policy."""

    def __init__(self, transactions: CueTransactions) -> None:
        self._tx = transactions
        self._pending: playback.ObservedCue | None = None
        self._identity_ever_installed = False

    def mark_identity_installed(self) -> None:
        self._identity_ever_installed = True

    def command_state(self, *, retired: bool) -> CueCommandState:
        if not retired:
            return CueCommandState.ACTIVE
        if self._identity_ever_installed:
            return CueCommandState.RETIRED_AFTER_ACTIVE
        return CueCommandState.NEVER_INSTALLED

    def observe(self, cue: playback.ObservedCue) -> None:
        self._pending = cue

    def settle(self) -> None:
        self._tx.settle_interaction()
        cue, self._pending = self._pending, None
        if cue is None:
            otel_metrics.record_cue_settle("no-observation")
            return
        before = self._tx.current_text()
        with otel_metrics.traced("cue_reconcile", cue_revision=str(self._tx.revision())) as span:
            self._tx.reconcile_text(cue.text)
            settled = "adopted" if self._tx.current_text() != before else "reinstalled"
            otel_metrics.record_cue_settle(settled, span)

    def clear_identity(self) -> None:
        self._tx.retire_annotation_cue()
        self._request_playback_retirement()

    def retire(self, reason: str) -> None:
        if not self._tx.retire_annotation_cue():
            self._request_playback_retirement()
            return
        log.debug("cue interaction retired: %s", reason)
        self._request_playback_retirement()
        self._tx.teardown_tooltip()
        self._tx.retire_tooltip_selection()
        self._tx.reset_cue_render()

    def replace_source(self, path: object = None, *, reason: str) -> None:
        self._tx.retire_settle_window()
        self._tx.reduce_playback(events.SourceReplaced(path))
        self.retire(reason)

    def rebind_episode(self) -> None:
        self._tx.close_picker()
        self._tx.retire_acquisition_episode()
        self._retire_episode_facts()
        self._tx.replace_navigation()
        self._tx.reset_cue_render()
        self._tx.retire_annotation_cue()

    def _retire_episode_facts(self) -> None:
        self._tx.retire_annotation_warm()
        self._tx.retire_translation_episode()
        self._tx.retire_playback_episode()
        if self._tx.playback_routed():
            return
        self._tx.retire_subtitle_episode()
        self._tx.retire_tooltip_episode()

    def _request_playback_retirement(self) -> None:
        self._tx.reduce_playback(events.CueIdentityRetireRequested(playback.RetireReason.CUE_TEXT))
