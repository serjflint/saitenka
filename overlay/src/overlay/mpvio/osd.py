"""Push a rendered RGBA panel into mpv's OSD via ``overlay-add`` (BGRA over IPC)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from overlay.mpvio.ipc import MpvIPC

log = logging.getLogger(__name__)


# Precomputed premultiply table — _PREMUL_LUT[alpha, value] == value * alpha // 255.
# One flat np.take replaces the uint16 widen+multiply+divide per pixel (~64 KB, fits in L2).
_PREMUL_LUT = (
    np.arange(256, dtype=np.uint16)[:, None] * np.arange(256, dtype=np.uint16)[None, :] // 255
).astype(np.uint8)
_PREMUL_FLAT = np.ascontiguousarray(_PREMUL_LUT.ravel())


def to_bgra_array(img: Image.Image, premultiply: bool = True) -> np.ndarray:
    """RGBA image → a contiguous premultiplied **BGRA** array (H, W, 4) for mpv's ``overlay-add``.

    Exposed so callers can convert a tall panel ONCE and then upload scrolled viewport *slices* of it
    without re-converting (fast scrolling). Premultiply is a 256×256 LUT gather (byte-identical to
    the reference ``value * alpha // 255``)."""
    arr = np.asarray(img.convert("RGBA"))
    if premultiply:
        idx = arr[:, :, 3:4].astype(np.uint16) * 256 + arr[:, :, :3]
        rgb = _PREMUL_FLAT.take(idx)
        arr = np.dstack([rgb, arr[:, :, 3]])
    return np.ascontiguousarray(arr[:, :, [2, 1, 0, 3]])


def to_bgra(img: Image.Image, premultiply: bool = True) -> tuple[bytes, int, int, int]:
    """Convert an RGBA image to the (data, w, h, stride) mpv's ``overlay-add bgra`` expects."""
    bgra = to_bgra_array(img, premultiply)
    return bgra.tobytes(), img.width, img.height, img.width * 4


class Overlay:
    """Manage one or more mpv OSD overlays keyed by id (0..63)."""

    def __init__(self, ipc: MpvIPC, id_base: int = 1):
        """``id_base`` shifts the physical mpv overlay ids so we can coexist with another script that
        owns the low ids (namespace hygiene). The controller keeps using its logical ids 1..6;
        base 1 (default) is a no-op offset → byte-identical to before."""
        self.ipc = ipc
        self.id_base = id_base
        self._files: dict[int, Path] = {}

    def _oid(self, oid: int) -> int:
        """Map a logical overlay id (1-based) to the configured physical range."""
        return oid + (self.id_base - 1)

    def _tempfile(self, oid: int) -> Path:
        path = self._files.get(oid)
        if path is None:
            fd = tempfile.NamedTemporaryFile(  # noqa: SIM115 — delete=False: the PATH outlives the
                prefix=f"saitenka-osd-{oid}-",
                suffix=".bgra",  # handle (mpv re-reads it)
                delete=False,
            )
            path = Path(fd.name)
            fd.close()
            self._files[oid] = path
        return path

    def show(self, img: Image.Image, x: int = 0, y: int = 0, oid: int = 0) -> dict:
        oid = self._oid(oid)
        data, w, h, stride = to_bgra(img)
        path = self._tempfile(oid)
        path.write_bytes(data)
        return self.ipc.command(
            "overlay-add", oid, int(x), int(y), str(path), 0, "bgra", w, h, stride
        )

    def show_bgra(self, bgra: np.ndarray, x: int = 0, y: int = 0, oid: int = 0) -> dict:
        """Upload an already-BGRA (H, W, 4) array — skips the RGBA→BGRA premultiply (fast scroll)."""
        oid = self._oid(oid)
        buf = np.ascontiguousarray(bgra)
        h, w = buf.shape[:2]
        path = self._tempfile(oid)
        path.write_bytes(buf.tobytes())
        return self.ipc.command(
            "overlay-add", oid, int(x), int(y), str(path), 0, "bgra", w, h, w * 4
        )

    def hide(self, oid: int = 0) -> dict:
        oid = self._oid(oid)
        res = self.ipc.command("overlay-remove", oid)
        p = self._files.pop(oid, None)
        if p is not None and p.exists():
            p.unlink()
        return res

    def close(self) -> None:
        for oid in list(self._files):
            try:
                self.hide(oid)
            except Exception:
                log.debug("overlay hide on close failed", exc_info=True)
