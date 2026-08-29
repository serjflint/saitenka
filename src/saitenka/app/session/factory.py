"""Production study-session assembly at the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

    from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
    from saitenka.app.episode_reslot import ReslotPorts, WatchPorts
    from saitenka.app.features.profiles.profile_session import ProfileSession
    from saitenka.app.features.tooltip.tooltip_controller import TooltipRuntimeJobs
    from saitenka.app.profiles import Profile
    from saitenka.app.scoring import Scorer
    from saitenka.app.session.close_ledger import CloseLedger
    from saitenka.app.session.controller import SessionController
    from saitenka.app.session.runtime import SessionEntry
    from saitenka.app.session.turn import SessionTurn
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.mpvio.osd import Overlay
    from saitenka.subtitles.geometry import GeometryBackend


@dataclass(frozen=True, slots=True)
class SessionServices:
    scorer: Scorer | None = None
    anki: object | None = None
    mining: object | None = None
    dictionaries: object | None = None
    tts: bool | None = None


class TooltipWorkMode(Enum):
    DEFERRED = "deferred"
    INLINE = "inline"


@dataclass(frozen=True, slots=True)
class SessionInfrastructure:
    renderer: SubtitleRenderer | NullRenderer | None = None
    geometry: GeometryBackend | None = None
    tooltip_jobs: Callable[[TooltipRuntimeJobs], TooltipRuntimeJobs] | None = None
    overlay: Overlay | None = None
    tooltip_work: TooltipWorkMode = TooltipWorkMode.DEFERRED


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    profile: Profile | None = None
    tokenizer_warm: Future[None] | None = None


class LiveSession(Protocol):
    """The running session role exposed after composition is complete."""

    def start(self) -> None: ...

    def pump(self, timeout: float | None = None) -> bool: ...

    def run(self) -> None: ...

    def request_stop(self) -> None: ...

    def close(self) -> CloseLedger: ...


@dataclass(frozen=True, slots=True)
class PreparedSession:
    """Pre-start capabilities consumed by launch/attach before only ``live`` survives."""

    live: LiveSession
    profile: ProfileSession
    rebuild_sub_index: Callable[[], None]
    configure_subtitle_mode: Callable[..., None]
    reslot: ReslotPorts
    watch: WatchPorts
    entry: SessionEntry


def create_session_controller(
    ipc: MpvIPC,
    *,
    services: SessionServices | None = None,
    options: ReaderOptions | None = None,
    infrastructure: SessionInfrastructure | None = None,
    identity: SessionIdentity | None = None,
) -> LiveSession:
    return prepare_session_controller(
        ipc,
        services=services,
        options=options,
        infrastructure=infrastructure,
        identity=identity,
    ).live


def prepare_session_controller(
    ipc: MpvIPC,
    *,
    services: SessionServices | None = None,
    options: ReaderOptions | None = None,
    infrastructure: SessionInfrastructure | None = None,
    identity: SessionIdentity | None = None,
) -> PreparedSession:
    return _compose_session(
        ipc,
        services=services,
        options=options,
        infrastructure=infrastructure,
        identity=identity,
    )[2]


def _compose_session(
    ipc: MpvIPC,
    *,
    services: SessionServices | None = None,
    options: ReaderOptions | None = None,
    infrastructure: SessionInfrastructure | None = None,
    identity: SessionIdentity | None = None,
) -> tuple[SessionController, SessionTurn, PreparedSession]:
    """Return the live object, internal turn, and public pre-start capabilities."""
    from saitenka.app.config import ReaderOptions
    from saitenka.app.session.assembly import build_session_assembly
    from saitenka.app.session.builder import build_session_turn
    from saitenka.app.session.controller import SessionController, SessionGraph
    from saitenka.app.session.runtime import SessionEntry

    resolved = services or SessionServices()
    resolved_options = options or ReaderOptions()
    physical = infrastructure or SessionInfrastructure()
    session_identity = identity or SessionIdentity()
    resolved_assembly = build_session_assembly(
        ipc,
        resolved_options,
        runtime_submit=ipc.submit_runtime_mpv,
        overlay=physical.overlay,
        tokenizer_warm=session_identity.tokenizer_warm,
    )
    turn = build_session_turn(
        ipc,
        resolved_assembly,
        resolved_options,
        scorer=resolved.scorer,
        anki=resolved.anki,
        mine_cfg=resolved.mining,
        dict_set=resolved.dictionaries,
        tts_ok=resolved.tts,
        renderer=physical.renderer,
        profile=session_identity.profile,
        # This factory is the composition layer, so it is where the correlated-command port is
        # handed over. A session assembled here uses gateway egress; a SessionController built directly (tests,
        # prewarm) writes straight to mpv unless its caller says otherwise. Named, not probed: the
        # port is on every `MpvIPC`, so a probe here could only ever answer "renamed" as "absent",
        # and absent silently moves every overlay write back onto the direct path.
        tooltip_runtime_jobs=(
            physical.tooltip_jobs
            if physical.tooltip_jobs is not None
            else (_inline_tooltip_jobs if physical.tooltip_work is TooltipWorkMode.INLINE else None)
        ),
        # Same reasoning for the geometry provider: which implementation runs is composition's
        # call, not the SessionController's. A SessionController built directly gets whatever its caller injects.
        geometry_backend=(
            physical.geometry
            if physical.geometry is not None
            else _geometry_backend(resolved_options.subtitle_geometry)
        ),
    )
    controller = SessionController(SessionGraph(ipc, turn, turn.lifecycle))
    prepared = PreparedSession(
        live=controller,
        profile=turn.profile_session,
        rebuild_sub_index=turn.rebuild_sub_index,
        configure_subtitle_mode=turn.configure_subtitle_mode,
        reslot=turn.reslot_ports,
        watch=turn.watch_ports,
        entry=SessionEntry(runtime=turn.entry_runtime, run=controller.run),
    )
    return controller, turn, prepared


def _inline_tooltip_jobs(jobs: TooltipRuntimeJobs) -> TooltipRuntimeJobs:
    from dataclasses import replace

    return replace(jobs, metadata=None, engaged=None)


def _geometry_backend(settings: SubtitleGeometryOptions):
    """Pick the shipping geometry provider for a session's settings.

    Lives here rather than beside the `GeometryBackend` Protocol because selecting an
    implementation means importing one, and `libass_backend` already imports `geometry` — the
    package may not depend on its own leaf. Composition is where that dependency belongs anyway: a
    host that picks its own provider cannot be handed a different one, which is what makes the
    fake/null/libass conformance contract testable at all.
    """
    if not settings.native_visible:
        return None
    from saitenka.subtitles.libass_backend import LibassGeometryBackend

    return LibassGeometryBackend(
        library_path=Path(settings.library_path) if settings.library_path else None,
        renderer_cache_max=settings.cache_max,
    )
