"""What following mpv onto a newly loaded episode needs — the ports run mode and attach mode share.

The two modes re-slot for different reasons (run drives the advance itself; attach follows the
user's or SyncPlay's) but perform the same operation, in the same order, out of the same vocabulary.
Naming it once is what stops the second mode from being a re-derivation of the first — the class of
bug #100 was: a step added to one path and not the other, invisible until an episode advanced.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.subtitle_modes import ProviderFetch


@dataclass(frozen=True, slots=True)
class ReslotPorts:
    """Performing one re-slot: close the finished episode out, rebind, re-select, restart.

    Every member is an act. `start_stats` is one rather than the episode, the enabled flag, the
    path read and the persist timer it is assembled from — a caller holding those four could open a
    history row on its own terms, and only one set of terms is correct.
    """

    ipc: object
    #: Returns the finished row's summary, which only the close path reads.
    finish_stats: Callable[[], object]
    start_stats: Callable[[], None]
    rebind_episode: Callable[[], None]
    rebuild_index: Callable[[], None]
    configure_mode: Callable[..., None]
    configure_retry: Callable[..., object]
    configure_picker: Callable[..., object]
    fetch_japanese: Callable[[ProviderFetch], None]
    start_prefetch: Callable[[], object]
    toast: Callable[..., None]


@dataclass(frozen=True, slots=True)
class WatchPorts:
    """Wiring the hooks that make a re-slot happen at all.

    Separate from `ReslotPorts` because installation happens once per session and a re-slot happens
    per episode — and because attach installs only the first of these: the user owns advancing
    there, which is the #62 SyncPlay gate expressed as a missing member rather than a comment.
    """

    install_reslot_hook: Callable[..., None]
    set_advance_hook: Callable[[Callable[[], bool]], None]
    prop: Callable[[str], object]
    current_media_path: Callable[[], Path | None]


class EpisodeWatch:
    """Own the session-lived hooks that follow mpv across episode files."""

    def __init__(
        self,
        *,
        prop: Callable[[str], object],
        replace_source: Callable[..., None],
        mark_authored_probe_dirty: Callable[[], None],
    ) -> None:
        self._prop = prop
        self._replace_source = replace_source
        self._mark_authored_probe_dirty = mark_authored_probe_dirty
        self.reslot_hook: Callable[[Path], None] | None = None
        self.advance_hook: Callable[[], bool] | None = None
        self._slotted_path: Path | None = None

    def current_media_path(self) -> Path | None:
        raw = self._prop("path")
        if not raw:
            return None
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            working_directory = self._prop("working-directory")
            if working_directory:
                path = Path(str(working_directory)) / path
        return path

    def install_reslot_hook(self, hook: Callable[[Path], None], *, initial: Path) -> None:
        self.reslot_hook = hook
        self._slotted_path = self.current_media_path() or Path(str(initial)).expanduser()

    def set_advance_hook(self, hook: Callable[[], bool]) -> None:
        self.advance_hook = hook

    def advance_if_reached(self, *, reached: bool) -> None:
        if reached and self.advance_hook is not None:
            self.advance_hook()

    def file_loaded(self) -> None:
        self._mark_authored_probe_dirty()
        if self.reslot_hook is None:
            return
        path = self.current_media_path()
        if path is None or path == self._slotted_path:
            return
        self._replace_source(path, reason="file-loaded")
        self._slotted_path = path
        self.reslot_hook(path)

    def ports(self) -> WatchPorts:
        return WatchPorts(
            install_reslot_hook=self.install_reslot_hook,
            set_advance_hook=self.set_advance_hook,
            prop=self._prop,
            current_media_path=self.current_media_path,
        )
