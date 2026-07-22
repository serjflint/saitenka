"""Golden-image test helpers: tolerance diff + update-on-demand.

Anti-aliasing varies subtly across Pillow/FreeType versions, so goldens are compared with a
per-pixel mean-absolute-error tolerance rather than byte-exact. Set ``SAITENKA_UPDATE_GOLDEN=1`` to
(re)write goldens instead of asserting — always eyeball the change before committing.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
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


@contextlib.contextmanager
def use_platform(platform: str, *, userprofile: str = r"C:\Users\Tester"):
    r"""Make path-resolution code see ``platform`` — flipping ALL THREE layers that matter, because
    patching ``sys.platform`` alone lies: ``platformdirs`` binds its OS class at *import* time from
    the real ``sys.platform``, so ``config_dir``/``data_dir``/``cache_dir`` would keep returning the
    host's dirs no matter what ``sys.platform`` says.

    Layers flipped for ``win32``:
      1. ``sys.platform`` — our own branches (``_pick``, ``mpv_config_dir``, …). NB: we do NOT touch
         ``os.name`` — pathlib reads it at ``Path()`` construction, so ``os.name = "nt"`` forces
         ``WindowsPath``, which raises ``UnsupportedOperation`` on POSIX. Code gated on ``os.name``
         (``long_path``'s ``\\?\`` prefixing) is therefore real-Windows residue, not simulable here.
      2. ``platformdirs.PlatformDirs`` → the real ``Windows`` resolver, fed via the officially
         supported ``WIN_PD_OVERRIDE_*`` env vars (platformdirs >=4.9). Those short-circuit the
         ctypes/registry backend (which raises off-Windows) *before* it runs — the public seam, no
         private-attribute patching — while staying *faithful*: ``roaming=`` still routes to a
         different CSIDL, so a stray ``roaming=True`` or a reroute through ``%APPDATA%`` is caught.
      3. Our own code reads ``%USERPROFILE%``/``%LOCALAPPDATA%``/``%APPDATA%`` directly
         (``mpv_config_dir`` et al.), so set those too; XDG/``SAITENKA_*`` overrides are cleared so
         they can't leak in.

    Filesystem *semantics* (separators, case-insensitivity) are a SEPARATE concern — opt in with
    ``pyfakefs`` ``fs.os = OSType.WINDOWS`` alongside this. Non-``win32`` just sets ``sys.platform``.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", platform)
        if platform == "win32":
            local = rf"{userprofile}\AppData\Local"
            roaming = rf"{userprofile}\AppData\Roaming"
            # Layer 3 — our own os.environ reads.
            mp.setenv("USERPROFILE", userprofile)
            mp.setenv("LOCALAPPDATA", local)
            mp.setenv("APPDATA", roaming)
            for var in (
                "SAITENKA_HOME",
                "SAITENKA_CACHE_DIR",
                "MPV_HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_CACHE_HOME",
            ):
                mp.delenv(var, raising=False)
            # Layer 2 — force the module-level user_*_dir() onto the Windows class (host is not
            # Windows) and drive it through the public WIN_PD_OVERRIDE_* seam.
            import platformdirs
            from platformdirs.windows import Windows

            mp.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", local)
            mp.setenv("WIN_PD_OVERRIDE_APPDATA", roaming)
            mp.setenv("WIN_PD_OVERRIDE_COMMON_APPDATA", r"C:\ProgramData")
            mp.setattr(platformdirs, "PlatformDirs", Windows)
        yield mp


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


class FakeTransport:
    """In-memory ``Transport`` double (see ``overlay.mpvio.transport.Transport``) for the transport
    contract suite: the 'server' side pushes bytes to the client with :meth:`feed`; bytes the client
    writes are captured in :attr:`sent`. Blocking :meth:`read` releases on ``feed``/``close``, so it
    drives ``MpvIPC``'s reader thread exactly like a real socket — deterministically, with no OS handle
    (identical behaviour on every platform, unlike a real named pipe)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._inbox = bytearray()  # server → client (what the reader will read)
        self.sent = bytearray()  # client → server (assert on this)
        self._closed = False

    def feed(self, data: bytes) -> None:
        """Server side: make ``data`` available to the client's next ``read``(s)."""
        with self._cond:
            self._inbox.extend(data)
            self._cond.notify_all()

    def read(self, n: int) -> bytes:
        with self._cond:
            while not self._inbox and not self._closed:
                self._cond.wait()
            chunk = bytes(self._inbox[:n])
            del self._inbox[:n]
            return chunk  # b"" only once closed AND drained → EOF

    def write(self, data: bytes) -> None:
        with self._cond:
            self.sent.extend(data)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
