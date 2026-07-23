"""OSD overlay-slot IDs: the small integer index ``Overlay.show``/``.hide`` use to distinguish each
on-screen element (subtitle, tooltip, toast, ...) from the others sharing mpv's OSD surface.

One shared enum so every module that draws or clears an overlay layer refers to the same slot
numbers — previously bare ``int`` constants defined separately in each subsystem's module
(``controller.py``, ``miner_ui.py``, ``nested_popup.py``), which only worked by accident (nothing
stopped two subsystems from picking the same number) and would only get worse as the split
continues. A leaf module with zero ``overlay.app.*`` imports, so anything can depend on it with no
cycle risk. :class:`OverlayId` is an :class:`~enum.IntEnum`, so it's a drop-in ``int`` everywhere an
``oid`` is expected (comparisons, dict keys, ``Overlay.show(..., oid=OverlayId.TIP)``).
"""

from __future__ import annotations

from enum import IntEnum


class OverlayId(IntEnum):
    SUB = 1
    TIP = 2
    TOAST = 3
    TRANS = 4
    PREVIEW = 5
    NESTED = 6
    LOADING = 9  # top-left "loading dictionaries" spinner during progressive startup
