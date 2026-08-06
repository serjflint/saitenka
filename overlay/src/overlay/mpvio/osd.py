"""Push a rendered RGBA panel into mpv's OSD via ``overlay-add`` (BGRA over IPC)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from overlay import otel_metrics
from overlay.bgra import (  # re-exported: `from overlay.mpvio.osd import to_bgra…`
    to_bgra,
    to_bgra_array,
)

if TYPE_CHECKING:
    from PIL import Image

    from overlay.mpvio.ipc import MpvIPC

__all__ = ["Overlay", "to_bgra", "to_bgra_array"]

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


def _oid_label(oid: int) -> str:
    """The overlay's logical name for telemetry (``SUB``/``TIP``/``HELP``/…), read straight off the
    ``OverlayId`` IntEnum the caller passed — so this low-level layer needs no import of the app-side
    enum (no mpvio→app dependency). A bare ``int`` falls back to its digits. Read BEFORE ``_oid`` shifts
    it, since the ``+`` offset returns a plain int that has lost the enum name."""
    return getattr(oid, "name", None) or str(int(oid))


def _set_draw_geometry(span: otel_metrics.SpanSetter, x: int, y: int, w: int, h: int) -> None:
    """Tag the draw span with the overlay's on-screen geometry. ``w``/``h`` are the ACTUAL uploaded
    pixel size, so they encode the effective ``ui_scale`` directly — a chrome overlay (help/sidebar/
    stats) that silently reverts to scale 1.0 shows up here as too-small ``w``/``h`` for its osd. Span
    attributes only (SpanSetter), never histogram labels — these are high-cardinality."""
    span.set("w", w)
    span.set("h", h)
    span.set("x", int(x))
    span.set("y", int(y))


class Overlay:
    """Manage one or more mpv OSD overlays keyed by id (0..63)."""

    def __init__(self, ipc: MpvIPC, id_base: int = 1):
        """``id_base`` shifts the physical mpv overlay ids so we can coexist with another script that
        owns the low ids (namespace hygiene). The controller keeps using its logical ids 1..6;
        base 1 (default) is a no-op offset → byte-identical to before."""
        self.ipc = ipc
        self.id_base = id_base
        self._files: dict[int, Path] = {}
        self._live: dict[int, tuple] = {}  # physical oid -> last overlay-add tail, for repaint()
        self.visible = True
        self.ops = 0  # bumped on every add/remove; the controller watches it to nudge a paused OSD

    def _oid(self, oid: int) -> int:
        """Map a logical overlay id (1-based) to the configured physical range."""
        return oid + (self.id_base - 1)

    def _add(self, oid: int, tail: tuple) -> dict:
        if self.visible:
            return self.ipc.command("overlay-add", oid, *tail)
        return {"error": "success"}

    def _tempfile(self, oid: int) -> Path:
        path = self._files.get(oid)
        if path is None:
            fd = tempfile.NamedTemporaryFile(  # noqa: SIM115  # path is process-lifetime, reused across calls
                prefix=f"saitenka-osd-{oid}-",
                suffix=".bgra",  # handle (mpv re-reads it)
                delete=False,
            )
            path = Path(fd.name)
            fd.close()
            self._files[oid] = path
        return path

    def show(self, img: Image.Image, x: int = 0, y: int = 0, oid: int = 0) -> dict:
        label = _oid_label(oid)
        oid = self._oid(oid)
        with otel_metrics.instrumented(
            otel_metrics.upload_duration_ms, "upload", oid=label
        ) as span:
            data, w, h, stride = to_bgra(img)
            path = self._tempfile(oid)
            path.write_bytes(data)
            tail = (int(x), int(y), str(path), 0, "bgra", w, h, stride)
            res = self._add(oid, tail)
            _set_draw_geometry(span, x, y, w, h)
        self._live[oid], self.ops = tail, self.ops + 1
        _warn_overlay_add(oid, w, h, res)
        return res

    def show_bgra(self, bgra: np.ndarray, x: int = 0, y: int = 0, oid: int = 0) -> dict:
        """Upload an already-BGRA (H, W, 4) array — skips the RGBA→BGRA premultiply (fast scroll)."""
        label = _oid_label(oid)
        oid = self._oid(oid)
        with otel_metrics.instrumented(
            otel_metrics.upload_duration_ms, "upload", oid=label
        ) as span:
            buf = np.ascontiguousarray(bgra)
            h, w = buf.shape[:2]
            path = self._tempfile(oid)
            path.write_bytes(buf.tobytes())
            tail = (int(x), int(y), str(path), 0, "bgra", w, h, w * 4)
            res = self._add(oid, tail)
            _set_draw_geometry(span, x, y, w, h)
        self._live[oid], self.ops = tail, self.ops + 1
        _warn_overlay_add(oid, w, h, res)
        return res

    def hide(self, oid: int = 0) -> dict:
        oid = self._oid(oid)
        res = self.ipc.command("overlay-remove", oid) if self.visible else {"error": "success"}
        self._live.pop(oid, None)
        self.ops += 1
        p = self._files.pop(oid, None)
        if p is not None and p.exists():
            p.unlink()
        return res

    def set_visible(self, *, visible: bool) -> None:
        """Hide/show Saitenka's surfaces while retaining their latest desired state."""
        if visible == self.visible:
            return
        self.visible = visible
        command = "overlay-add" if visible else "overlay-remove"
        for oid, tail in list(self._live.items()):
            self.ipc.command(command, oid, *tail) if visible else self.ipc.command(command, oid)
            self.ops += 1

    def repaint(self) -> None:
        """Re-issue every live overlay so mpv re-composites and PRESENTS them. While paused (esp. on
        Windows) mpv throttles OSD updates until *something* pokes the OSD (mpv #8172) — a new/updated
        overlay only shows on the next frame or input event (the "doesn't update until I move the
        mouse" bug). Re-adding the same overlays is that poke. Does NOT bump ``ops`` (it's the reaction
        to a change, not a new one), so it can't feed back into another nudge."""
        if self.visible:
            for oid, tail in list(self._live.items()):
                self.ipc.command("overlay-add", oid, *tail)

    def close(self) -> None:
        for oid in list(self._files):
            try:
                self.hide(oid)
            except Exception:
                log.debug("overlay hide on close failed", exc_info=True)
