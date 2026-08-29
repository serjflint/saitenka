"""The live-session lifecycle and owner-thread turn boundary."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import subtitle_intents
from saitenka.app.features.mining import mine_intents
from saitenka.app.session.runtime import SessionActs, SessionFacts, SessionRuntime
from saitenka.runtime import StartupReady, UserCommand, events

if TYPE_CHECKING:
    from saitenka.app.session.close_ledger import CloseLedger
    from saitenka.app.session.graph import SessionGraph

log = logging.getLogger(__name__)


class SessionController:
    """Own one live session's lifecycle and ordered owner-thread turns."""

    def __init__(self, graph: SessionGraph) -> None:
        if graph.ipc.session_loop is None:
            raise RuntimeError("a composed session requires an installed session runtime")
        if not graph.playback.routed:
            raise RuntimeError("a composed session requires an installed session reactor")
        self._graph = graph
        self._interactive_ready = False
        self.entry_runtime = self._build_entry_runtime()

    def start(self) -> None:
        """Install and start the session participants exactly once."""
        with otel_metrics.traced("startup.reader_setup"):
            self._graph.lifecycle.start()

    def pump(self, timeout: float | None = 0.0) -> bool:
        """Consume and settle one owner-thread turn."""
        graph = self._graph
        try:
            operations_before = graph.overlay.ops
            graph.ipc.receive_session(timeout, self._drain_event)
            self._settle_turn()
            if not graph.connection.current.ready:
                return True
            graph.presentation.schedule_paused_nudge(operations_before)
            if self._mark_interactive_ready():
                graph.ipc.receive_session(0.0, self._drain_event)
                self._settle_turn()
            return True
        except (OSError, ValueError):
            return False

    def run(self) -> None:
        """Start and drive turns until stop or transport retirement."""
        self.start()
        loop = self._graph.ipc.session_loop
        if loop is None:
            raise RuntimeError("session runtime disappeared after construction")
        loop.run(self.pump, until=self._graph.lifecycle.stop_signal.is_set)

    def request_stop(self) -> None:
        self._graph.lifecycle.request_stop()

    def close(self) -> CloseLedger:
        return self._graph.lifecycle.close()

    def _settle_turn(self) -> None:
        self._graph.interaction.settle()
        self._graph.cue.settle()

    def _drain_event(self, event: object) -> None:
        graph = self._graph
        if isinstance(event, events.FileLoaded):
            graph.episode_watch.file_loaded()
        elif isinstance(event, UserCommand):
            graph.commands.perform(event)
        else:
            log.debug("ignored unsupported session event: %s", type(event).__name__)

    def _drive_annotation_once(self, timeout: float | None) -> None:
        """Drain a nested annotation turn without settling the outer cue transaction."""
        self._graph.ipc.receive_session(timeout, self._drain_event)

    def _prepare_subtitle_blocking(self, text: str) -> None:
        graph = self._graph
        graph.annotation.prepare_blocking(
            text,
            graph.cue.annotation_inputs(),
            drive=self._drive_annotation_once,
        )
        graph.cue.set_subtitle(text)

    def _mark_interactive_ready(self) -> bool:
        if self._interactive_ready:
            return False
        graph = self._graph
        if graph.playback.observing and graph.playback.state.value("osd-dimensions") in (None, {}):
            return False
        self._interactive_ready = True
        connected_at = graph.ipc.connected_at
        with otel_metrics.traced(
            "startup.interactive_ready",
            cue_pending=str(graph.annotation.view.pending_text is not None).lower(),
            deps_pending=str(not graph.annotation.view.dependencies_settled).lower(),
        ) as span:
            if connected_at is not None:
                span.set("since_ipc_ms", round((time.monotonic() - connected_at) * 1_000, 3))
            graph.ipc.publish_runtime_event(StartupReady())
        return True

    def _build_entry_runtime(self) -> SessionRuntime:
        graph = self._graph
        facts = SessionFacts(
            refresh_osd=graph.presentation.refresh_osd,
            prop=graph.playback.value,
            get=graph.playback.query,
            tokens=lambda: graph.subtitle_presentation.cue.current.tokens,
            is_content_token=lambda token: graph.profile.profile.tokenizer.is_content(token),
            osd_height=lambda: graph.screen.osd[1],
            painted=lambda: (
                graph.lifecycle_surfaces.settled() and graph.interaction_surfaces.settled()
            ),
        )
        acts = SessionActs(
            drive_annotation_once=self._drive_annotation_once,
            prepare_subtitle=self._prepare_subtitle_blocking,
            prepare_hover=graph.tooltip.prepare_hover_blocking,
            mark_ready=self._mark_interactive_ready,
            scroll_tip=graph.tooltip.scroll_tip,
            toggle_translation=graph.stateless_commands.handler(
                subtitle_intents.SubtitleCommand.TOGGLE_TRANSLATION
            ),
            mine_current=graph.stateless_commands.handler(mine_intents.MineCommand.WORD),
            bulk_mine=graph.stateless_commands.handler(mine_intents.MineCommand.EPISODE),
        )
        return SessionRuntime(facts, acts, graph.ipc)
