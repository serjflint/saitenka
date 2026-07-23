"""Push a rendered RGBA panel into mpv's OSD via ``overlay-add`` (BGRA over IPC)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from overlay.app import otel_metrics
from overlay.mpvio.ipc import MpvIPC

log = logging.getLogger(__name__)

# Overlay ids we've already warned about, so a per-tick redraw (spinner/subtitle) can't flood the log.
_warned_oids: set[int] = set()


def _warn_overlay_add(oid: int, w: int, h: int, res: dict) -> None:
    """Log (once per overlay id) when mpv rejects an ``overlay-add`` — a NON-empty ``error`` other than
    ``success``. This separates 'mpv refused to draw' (bad format/size, unsupported on this build) from
    the IPC-timeout case the transport layer logs; together they pinpoint a 'plays but nothing draws'."""
    err = res.get("error")
    if err in (None, "success") or oid in _warned_oids:
        return
    _warned_oids.add(oid)
    log.warning("overlay-add rejected for oid=%d (%dx%d): %s", oid, w, h, res)


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
        with otel_metrics.instrumented(otel_metrics.upload_duration_ms, "upload"):
            data, w, h, stride = to_bgra(img)
            path = self._tempfile(oid)
            path.write_bytes(data)
            res = self.ipc.command(
                "overlay-add", oid, int(x), int(y), str(path), 0, "bgra", w, h, stride
            )
        _warn_overlay_add(oid, w, h, res)
        return res

    def show_bgra(self, bgra: np.ndarray, x: int = 0, y: int = 0, oid: int = 0) -> dict:
        """Upload an already-BGRA (H, W, 4) array — skips the RGBA→BGRA premultiply (fast scroll)."""
        oid = self._oid(oid)
        with otel_metrics.instrumented(otel_metrics.upload_duration_ms, "upload"):
            buf = np.ascontiguousarray(bgra)
            h, w = buf.shape[:2]
            path = self._tempfile(oid)
            path.write_bytes(buf.tobytes())
            res = self.ipc.command(
                "overlay-add", oid, int(x), int(y), str(path), 0, "bgra", w, h, w * 4
            )
        _warn_overlay_add(oid, w, h, res)
        return res

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
