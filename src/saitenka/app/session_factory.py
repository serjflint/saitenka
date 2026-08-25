"""Production study-session assembly at the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from concurrent.futures import Future

    from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
    from saitenka.app.profiles import Profile
    from saitenka.app.scoring import Scorer
    from saitenka.app.session_controller import SessionController
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
    from saitenka.mpvio.ipc import MpvIPC


@dataclass(frozen=True, slots=True)
class SessionServices:
    scorer: Scorer | None = None
    anki: object | None = None
    mining: object | None = None
    dictionaries: object | None = None
    tts: bool | None = None


def create_session_controller(
    ipc: MpvIPC,
    *,
    services: SessionServices | None = None,
    options: ReaderOptions | None = None,
    renderer: SubtitleRenderer | NullRenderer | None = None,
    profile: Profile | None = None,
    tokenizer_warm: Future[None] | None = None,
) -> SessionController:
    from saitenka.app.config import ReaderOptions
    from saitenka.app.session_controller import SessionController

    resolved = services or SessionServices()
    return SessionController(
        ipc,
        scorer=resolved.scorer,
        anki=resolved.anki,
        mine_cfg=resolved.mining,
        dict_set=resolved.dictionaries,
        tts_ok=resolved.tts,
        options=options,
        renderer=renderer,
        profile=profile,
        tokenizer_warm=tokenizer_warm,
        # This factory is the composition layer, so it is where the correlated-command port is
        # handed over. A session assembled here uses gateway egress; a SessionController built directly (tests,
        # prewarm) writes straight to mpv unless its caller says otherwise. Named, not probed: the
        # port is on every `MpvIPC`, so a probe here could only ever answer "renamed" as "absent",
        # and absent silently moves every overlay write back onto the direct path.
        runtime_submit=ipc.submit_runtime_mpv,
        # Same reasoning for the geometry provider: which implementation runs is composition's
        # call, not the SessionController's. A SessionController built directly gets whatever its caller injects.
        geometry_backend=_geometry_backend((options or ReaderOptions()).subtitle_geometry),
    )


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
