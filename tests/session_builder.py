"""Construct a live session over the suite's immediate fake-overlay boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import (
    LiveSession,
    SessionIdentity,
    SessionInfrastructure,
    SessionServices,
    _compose_session,
)
from saitenka.mpvio.osd import Overlay

if TYPE_CHECKING:
    from saitenka.app.session.turn import SessionTurn


@dataclass(eq=False, slots=True, weakref_slot=True)
class TestSession:
    live: LiveSession
    turn: SessionTurn

    def start(self) -> None:
        self.live.start()

    def pump(self, timeout: float | None = 0.0) -> bool:
        return self.live.pump(timeout)

    def run(self) -> None:
        self.live.run()

    def request_stop(self) -> None:
        self.live.request_stop()

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
    physical = infrastructure or SessionInfrastructure()
    if physical.overlay is None:
        physical = replace(
            physical,
            overlay=Overlay(
                ipc,
                id_base=resolved_options.overlay_id_base,
                runtime_submit=runtime_submit,
            ),
        )
    live, turn, _prepared = _compose_session(
        ipc,
        services=services,
        options=resolved_options,
        infrastructure=physical,
        identity=identity,
    )
    return TestSession(live, turn)


def install_profile_dependencies(session, *, scorer=None, dictionaries=None) -> None:
    """Install a profile-qualified collaborator bundle through the production owner seam."""
    from saitenka.app.features.profiles.dependencies import DependencyBundle

    session.turn.profile_session.accept(
        DependencyBundle(
            session.turn.profile_session.identity,
            scorer=scorer,
            dictionaries=dictionaries,
        )
    )
