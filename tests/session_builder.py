"""Construct a live session over the suite's immediate fake-overlay boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import (
    LiveSession,
    SessionIdentity,
    SessionInfrastructure,
    SessionServices,
    TooltipWorkMode,
    _compose_session,
)
from saitenka.mpvio.osd import Overlay

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.session.graph import SessionGraph
    from saitenka.app.session.runtime import SessionEntry


@dataclass(eq=False, slots=True, weakref_slot=True)
class TestSession:
    __test__: ClassVar[bool] = False

    live: LiveSession
    graph: SessionGraph
    entry: SessionEntry
    _emit: Callable[[dict], None]

    def start(self) -> None:
        self.live.start()

    def pump(self, timeout: float | None = 0.0) -> bool:
        return self.live.pump(timeout)

    def run(self) -> None:
        self.live.run()

    def request_stop(self) -> None:
        self.live.request_stop()

    def command(self, command) -> None:
        """Deliver a script command through the production mailbox and owner-thread turn."""
        from saitenka.runtime import UserCommand

        event = command if isinstance(command, UserCommand) else UserCommand(command)
        if event.command_id is not None or event.coalesced_ids:
            raise ValueError("test commands acquire identity at runtime ingress")
        self._emit({"event": "client-message", "args": [event.name, *event.args]})
        self.pump()

    def close(self):
        return self.live.close()


def build_session(
    ipc,
    *,
    services: SessionServices | None = None,
    options: ReaderOptions | None = None,
    infrastructure: SessionInfrastructure | None = None,
    identity: SessionIdentity | None = None,
    runtime_submit=None,
):
    resolved_options = options or ReaderOptions()
    installs_runtime = ipc.session_loop is None
    if installs_runtime:
        from saitenka.app.session.routes import install_session_runtime

        install_session_runtime(ipc, startup_hint=False)
    physical = infrastructure or SessionInfrastructure()
    if installs_runtime:
        supplied_jobs = physical.tooltip_jobs
        if supplied_jobs is None:
            physical = replace(physical, tooltip_work=TooltipWorkMode.INLINE)
        else:

            def inline_test_jobs(jobs):
                return supplied_jobs(replace(jobs, metadata=None, engaged=None))

            physical = replace(physical, tooltip_jobs=inline_test_jobs)
    if physical.overlay is None:
        physical = replace(
            physical,
            overlay=Overlay(
                ipc,
                id_base=resolved_options.overlay_id_base,
                runtime_submit=runtime_submit or ipc.submit_runtime_mpv,
            ),
        )
    live, graph, prepared = _compose_session(
        ipc,
        services=services,
        options=resolved_options,
        infrastructure=physical,
        identity=identity,
    )
    return TestSession(live, graph, prepared.entry, ipc.emit)


def install_profile_dependencies(session, *, scorer=None, dictionaries=None) -> None:
    """Install a profile-qualified collaborator bundle through the production owner seam."""
    from saitenka.app.features.profiles.dependencies import DependencyBundle

    session.graph.profile.accept(
        DependencyBundle(
            session.graph.profile.identity,
            scorer=scorer,
            dictionaries=dictionaries,
        )
    )
