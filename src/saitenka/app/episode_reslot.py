"""What following mpv onto a newly loaded episode needs — the ports run mode and attach mode share.

The two modes re-slot for different reasons (run drives the advance itself; attach follows the
user's or SyncPlay's) but perform the same operation, in the same order, out of the same vocabulary.
Naming it once is what stops the second mode from being a re-derivation of the first — the class of
bug #100 was: a step added to one path and not the other, invisible until an episode advanced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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
