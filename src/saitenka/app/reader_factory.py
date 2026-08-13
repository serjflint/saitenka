"""Production reader assembly at the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.config import ReaderOptions
    from saitenka.app.controller import Reader
    from saitenka.app.profiles import Profile
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
    from saitenka.mpvio.ipc import MpvIPC


@dataclass(frozen=True, slots=True)
class ReaderServices:
    scorer: object | None = None
    anki: object | None = None
    mining: object | None = None
    dictionaries: object | None = None


def create_reader(
    ipc: MpvIPC,
    *,
    services: ReaderServices | None = None,
    options: ReaderOptions | None = None,
    renderer: SubtitleRenderer | NullRenderer | None = None,
    profile: Profile | None = None,
) -> Reader:
    from saitenka.app.controller import Reader

    resolved = services or ReaderServices()
    return Reader(
        ipc,
        scorer=resolved.scorer,
        anki=resolved.anki,
        mine_cfg=resolved.mining,
        dict_set=resolved.dictionaries,
        options=options,
        renderer=renderer,
        profile=profile,
    )
