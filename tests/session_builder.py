"""Construct a live session over the suite's immediate fake-overlay boundary."""

from __future__ import annotations

from dataclasses import replace

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import (
    SessionIdentity,
    SessionInfrastructure,
    SessionServices,
    create_session_controller,
)
from saitenka.mpvio.osd import Overlay


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
    return create_session_controller(
        ipc,
        services=services,
        options=resolved_options,
        infrastructure=physical,
        identity=identity,
    )


def install_profile_dependencies(session, *, scorer=None, dictionaries=None) -> None:
    """Install a profile-qualified collaborator bundle through the production owner seam."""
    from saitenka.app.features.profiles.dependencies import DependencyBundle

    session.profile_session.accept(
        DependencyBundle(
            session.profile_session.identity,
            scorer=scorer,
            dictionaries=dictionaries,
        )
    )
