"""Production reader assembly at the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from concurrent.futures import Future

    from saitenka.app.config import ReaderOptions
    from saitenka.app.controller import Reader
    from saitenka.app.loading import StartupHintLease
    from saitenka.app.profiles import Profile
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
    from saitenka.mpvio.ipc import MpvIPC


@dataclass(frozen=True, slots=True)
class ReaderServices:
    scorer: object | None = None
    anki: object | None = None
    mining: object | None = None
    dictionaries: object | None = None
    tts: bool | None = None


def create_reader(
    ipc: MpvIPC,
    *,
    services: ReaderServices | None = None,
    options: ReaderOptions | None = None,
    renderer: SubtitleRenderer | NullRenderer | None = None,
    profile: Profile | None = None,
    startup_hint_lease: StartupHintLease | None = None,
    tokenizer_warm: Future[None] | None = None,
) -> Reader:
    from saitenka.app.controller import Reader

    resolved = services or ReaderServices()
    return Reader(
        ipc,
        scorer=resolved.scorer,
        anki=resolved.anki,
        mine_cfg=resolved.mining,
        dict_set=resolved.dictionaries,
        tts_ok=resolved.tts,
        options=options,
        renderer=renderer,
        profile=profile,
        startup_hint_lease=startup_hint_lease,
        tokenizer_warm=tokenizer_warm,
        # This factory is the composition layer, so it is where the correlated-command port is
        # resolved. A session assembled here uses gateway egress; a Reader built directly (tests,
        # prewarm) writes straight to mpv unless its caller says otherwise.
        runtime_submit=getattr(ipc, "submit_runtime_mpv", None),
    )
