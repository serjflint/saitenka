"""Golden-image test helpers: tolerance diff + update-on-demand.

Anti-aliasing varies subtly across Pillow/FreeType versions, so goldens are compared with a
per-pixel mean-absolute-error tolerance rather than byte-exact. Set ``SAITENKA_UPDATE_GOLDEN=1`` to
(re)write goldens instead of asserting — always eyeball the change before committing.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("SAITENKA_UPDATE_GOLDEN") == "1"


def mae(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute error per channel (0..255) between two RGBA images of equal size."""
    aa = np.asarray(a.convert("RGBA"), dtype=np.int16)
    bb = np.asarray(b.convert("RGBA"), dtype=np.int16)
    return float(np.abs(aa - bb).mean())


def assert_golden(img: Image.Image, name: str, tol: float = 2.0) -> None:
    """Compare ``img`` to ``tests/golden/<name>`` within mean-abs-error ``tol`` (or update it)."""
    path = GOLDEN_DIR / name
    if UPDATE or not path.exists():
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        img.save(path)
        if not UPDATE:
            raise AssertionError(f"golden {name} was missing — created it; re-run to verify")
        return
    golden = Image.open(path)
    assert img.size == golden.size, f"{name}: size {img.size} != golden {golden.size}"
    err = mae(img, golden)
    assert err <= tol, f"{name}: mean-abs-error {err:.3f} exceeds tol {tol}"


class FakeIPC:
    """Minimal mpv IPC stand-in with property-change emission (Stage 7c).

    ``props`` feeds ``get_property`` (the pre-observe fallback path); ``set_prop`` additionally
    queues a ``property-change`` event the way mpv's ``observe_property`` does, so controller tests
    can run on the event-driven path. All commands are recorded in ``commands``."""

    def __init__(self):
        self.events: list[dict] = []
        self.props: dict = {}
        self.commands: list[tuple] = []

    def set_prop(self, name: str, value) -> None:
        """Simulate mpv: update the property AND emit a buffered property-change event."""
        self.props[name] = value
        self.events.append({"event": "property-change", "name": name, "data": value})

    def pump(self) -> None:
        """Real IPC reads the socket here; the fake's events are queued directly."""

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}

    def drain_events(self) -> list[dict]:
        evs, self.events = self.events, []
        return evs
