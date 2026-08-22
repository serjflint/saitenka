"""Push a rendered RGBA panel into mpv's OSD via ``overlay-add`` (BGRA over IPC)."""

from __future__ import annotations

import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from saitenka import otel_metrics
from saitenka.bgra import (  # re-exported: `from saitenka.mpvio.osd import to_bgra…`
    to_bgra,
    to_bgra_array,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL import Image

    from saitenka.mpvio.ipc import MpvIPC

__all__ = ["Overlay", "PreparedOverlay", "to_bgra", "to_bgra_array"]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedOverlay:
    oid: int
    path: Path
    tail: tuple[object, ...]
    command: tuple[object, ...]


# Overlay ids we've already warned about, so a per-tick redraw (spinner/subtitle) can't flood the log.
_warned_oids: set[int] = set()


def _defer_interaction_for(ipc: object) -> bool:
    from saitenka.mpvio.ipc import MpvIPC

    return isinstance(ipc, MpvIPC)


class _InteractionPresenter:
    """Newest-wins resource slots for interaction overlay staging and IPC."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._desired: dict[int, tuple[int, Callable[[], None]]] = {}
        self._sequence = 0
        self._closed = False
        self._thread: threading.Thread | None = None

    def submit(self, oid: int, operation: Callable[[], None]) -> None:
        with self._condition:
            if self._closed:
                return
            self._sequence += 1
            self._desired[oid] = (self._sequence, operation)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="saitenka-interaction-presenter",
                    daemon=True,
                )
                self._thread.start()
            self._condition.notify()

    def close(self, timeout: float = 1.0) -> None:
        with self._condition:
            self._closed = True
            self._desired.clear()
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while item := self._next():
            oid, sequence, operation = item
            try:
                operation()
            except Exception:
                log.exception("interaction overlay presentation failed for oid=%d", oid)
            with self._condition:
                current = self._desired.get(oid)
                if current is not None and current[0] == sequence:
                    self._desired.pop(oid, None)

    def _next(self):
        with self._condition:
            while not self._desired and not self._closed:
                self._condition.wait()
            if self._closed:
                return None
            oid, (sequence, operation) = min(self._desired.items(), key=lambda item: item[1][0])
            return oid, sequence, operation


def _warn_overlay_add(oid: int, w: int, h: int, res: dict) -> None:
    """Log (once per overlay id) when mpv rejects an ``overlay-add`` — a NON-empty ``error`` other than
    ``success``. This separates 'mpv refused to draw' (bad format/size, unsupported on this build) from
    the IPC-timeout case the transport layer logs; together they pinpoint a 'plays but nothing draws'."""
    err = res.get("error")
    if err in {None, "success"} or oid in _warned_oids:
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

    def __init__(self, ipc: MpvIPC, id_base: int = 1, *, runtime_submit=None):
        """``id_base`` shifts the physical mpv overlay ids so we can coexist with another script that
        owns the low ids (namespace hygiene). The controller keeps using its logical ids 1..6;
        base 1 (default) is a no-op offset → byte-identical to before.

        ``runtime_submit`` is the correlated-command port, supplied by composition. It is a
        constructor argument rather than something detected on ``ipc``, because a probe makes egress
        depend on which methods a collaborator happens to expose: handing any fake the port would
        silently move overlay writes onto the gateway and change what every caller observes."""
        self.ipc = ipc
        self.id_base = id_base
        self._runtime_submit = runtime_submit
        self._files: dict[int, Path] = {}
        #: One stable path per oid, only ever published onto by rename — see :meth:`_write_frame`.
        self._frame_paths: dict[int, Path] = {}
        self._frame_lock = threading.Lock()
        self._live: dict[int, tuple] = {}  # physical oid -> last overlay-add tail, for repaint()
        self.visible = True
        self.ops = 0  # bumped on every add/remove; the controller watches it to nudge a paused OSD
        self._interaction_presenter = _InteractionPresenter()
        self._defer_interaction = _defer_interaction_for(ipc)
        self._interaction_oids: set[int] = set()
        self.lifecycle_oids: set[int] = set()
        self._staged_lifecycle_paths: set[Path] = set()
        self._lifecycle_lock = threading.Lock()
        self._compat_effect_id = 1_000_000

    def next_compat_effect_id(self):
        from saitenka.runtime import EffectId

        effect_id = EffectId(self._compat_effect_id)
        self._compat_effect_id += 1
        return effect_id

    def submit_surface_transaction(
        self, *, owner, identity, command: tuple[object, ...], on_finished
    ) -> None:
        from saitenka.runtime import EffectError, EffectFinished, EffectOutcome

        submit = self._runtime_submit
        if submit is not None:
            accepted = submit(
                owner=owner,
                identity=identity,
                command=command,
                timeout_s=10.0,
                on_finished=on_finished,
            )
            if accepted:
                return
            reply = {"error": "disconnected"}
        elif not isinstance(command[1], int):
            raise ValueError("surface command requires an integer overlay id")
        elif command[0] == "overlay-add":
            reply = self._add(command[1], tuple(command[2:]))
        else:
            logical_oid = command[1] - (self.id_base - 1)
            reply = self.hide(logical_oid)
        succeeded = reply.get("error") in {None, "success"}
        on_finished(
            EffectFinished(
                self.next_compat_effect_id(),
                owner,
                identity,
                EffectOutcome.SUCCEEDED if succeeded else EffectOutcome.FAILED,
                error=None if succeeded else EffectError.INVALID_RESULT,
            )
        )

    def physical_oid(self, oid: int) -> int:
        return self._oid(oid)

    def prepare(
        self, img: Image.Image, x: int = 0, y: int = 0, *, oid: int = 0, revision: int
    ) -> PreparedOverlay:
        physical_oid = self._oid(oid)
        data, w, h, stride = to_bgra(img)
        with tempfile.NamedTemporaryFile(
            prefix=f"saitenka-osd-{physical_oid}-r{revision}-",
            suffix=".bgra",
            delete=False,
        ) as staged:
            path = Path(staged.name)
        try:
            path.write_bytes(data)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        tail: tuple[object, ...] = (int(x), int(y), str(path), 0, "bgra", w, h, stride)
        with self._lifecycle_lock:
            self.lifecycle_oids.add(oid)
            self._staged_lifecycle_paths.add(path)
        return PreparedOverlay(
            physical_oid,
            path,
            tail,
            ("overlay-add", physical_oid, *tail),
        )

    def commit_prepared(self, prepared: PreparedOverlay) -> None:
        with self._lifecycle_lock:
            previous = self._files.get(prepared.oid)
            self._files[prepared.oid] = prepared.path
            self._staged_lifecycle_paths.discard(prepared.path)
            self._live[prepared.oid] = prepared.tail
            self.ops += 1
        if previous is not None and previous != prepared.path:
            previous.unlink(missing_ok=True)

    def discard_prepared(self, prepared: PreparedOverlay) -> None:
        with self._lifecycle_lock:
            if self._files.get(prepared.oid) == prepared.path:
                return
            self._staged_lifecycle_paths.discard(prepared.path)
        prepared.path.unlink(missing_ok=True)

    def commit_remove(self, oid: int) -> None:
        physical_oid = self._oid(oid)
        with self._lifecycle_lock:
            self._live.pop(physical_oid, None)
            self.ops += 1
            path = self._files.pop(physical_oid, None)
            self.lifecycle_oids.discard(oid)
        self._retire_frame_path(physical_oid)
        if path is not None and path.exists():
            path.unlink()

    def remove_lifecycle_now(self, oid: int) -> dict:
        """Synchronously place a final remove behind any queued add before detaching from mpv."""
        physical_oid = self._oid(oid)
        return self.ipc.command("overlay-remove", physical_oid)

    def _oid(self, oid: int) -> int:
        """Map a logical overlay id (1-based) to the configured physical range."""
        return oid + (self.id_base - 1)

    def _add(self, oid: int, tail: tuple) -> dict:
        if self.visible:
            return self.ipc.command("overlay-add", oid, *tail)
        return {"error": "success"}

    def _write_frame(self, oid: int, data: bytes) -> Path:
        """Publish ``data`` at this oid's stable path by ``os.replace``, never by rewriting it.

        mpv reads the named file **inside** `overlay-add` (`cmd_overlay_add` → `_platform_memmove`,
        the frame both SIGBUS reports fault in). Any in-place rewrite therefore races that read, and
        a pagein against a file being truncated is a bus error inside mpv rather than an error we can
        observe. A rename never mutates the inode mpv opened, so the race has no window left to hit —
        which an alternating pair of slots could only ever narrow, since it cannot know when the read
        happens. Two writers is what made the narrowed window reachable: `repaint` re-issues
        `_live`'s tail from the reader thread, and it names the very slot the upload thread picks next.

        Cost is not why the old form was in place: interleaved, replace and in-place rewrite are both
        ~0.7 ms p50 for a 2.5 MB frame here. The 0.8-vs-3.2 ms gap that chose in-place came from
        timing each strategy in its own sequential block, which measures the page cache.
        """
        with self._frame_lock:
            path = self._frame_paths.get(oid)
            if path is None:
                path = self._frame_paths[oid] = self._new_frame_path(oid)
            staging = path.with_name(path.name + ".staging")
            staging.write_bytes(data)
            Path(staging).replace(path)
            self._files[oid] = path
            return path

    def _new_frame_path(self, oid: int) -> Path:
        with tempfile.NamedTemporaryFile(
            prefix=f"saitenka-osd-{oid}-", suffix=".bgra", delete=False
        ) as staged:
            return Path(staged.name)

    def _retire_frame_path(self, oid: int) -> None:
        with self._frame_lock:
            path = self._frame_paths.pop(oid, None)
        if path is not None:
            path.with_name(path.name + ".staging").unlink(missing_ok=True)

    def show(self, img: Image.Image, x: int = 0, y: int = 0, oid: int = 0) -> dict:
        label = _oid_label(oid)
        oid = self._oid(oid)
        with otel_metrics.instrumented(
            otel_metrics.upload_duration_ms, "upload", oid=label
        ) as span:
            data, w, h, stride = to_bgra(img)
            path = self._write_frame(oid, data)
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
            path = self._write_frame(oid, buf.tobytes())
            tail = (int(x), int(y), str(path), 0, "bgra", w, h, w * 4)
            res = self._add(oid, tail)
            _set_draw_geometry(span, x, y, w, h)
        self._live[oid], self.ops = tail, self.ops + 1
        _warn_overlay_add(oid, w, h, res)
        return res

    def show_bgra_interactive(
        self,
        bgra: np.ndarray,
        x: int = 0,
        y: int = 0,
        oid: int = 0,
        *,
        on_presented=None,
    ) -> dict:
        """Stage and publish TIP/NESTED pixels off the event thread on real mpv sessions."""
        if not self._defer_interaction:
            result = self.show_bgra(bgra, x, y, oid=oid)
            if on_presented is not None:
                on_presented(result or {"error": "success"})
            return result or {"error": "success"}
        payload = np.ascontiguousarray(bgra).copy()
        physical_oid = self._oid(oid)
        self._interaction_oids.add(oid)

        def present() -> None:
            try:
                result = self.show_bgra(payload, x, y, oid=oid)
            except Exception:
                if on_presented is not None:
                    on_presented({"error": "failed"})
                raise
            else:
                if on_presented is not None:
                    on_presented(result)

        self._interaction_presenter.submit(physical_oid, present)
        return {"error": "deferred"}

    def hide_interactive(self, oid: int = 0) -> dict:
        if not self._defer_interaction:
            return self.hide(oid)
        physical_oid = self._oid(oid)

        def remove() -> None:
            self.hide(oid)

        self._interaction_presenter.submit(physical_oid, remove)
        return {"error": "deferred"}

    def hide(self, oid: int = 0) -> dict:
        oid = self._oid(oid)
        # Removal is idempotent.  Always send it: an in-flight deferred add can finish after visibility
        # was turned off, and skipping this command would leave those pixels stuck in mpv.
        res = self.ipc.command("overlay-remove", oid)
        self._live.pop(oid, None)
        self.ops += 1
        p = self._files.pop(oid, None)
        self._retire_frame_path(oid)
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
        if not visible:
            for oid in tuple(self._interaction_oids):
                self.hide_interactive(oid)

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
        self._interaction_presenter.close()
        for oid in list(self._files):
            try:
                self.hide(oid)
            except Exception:
                log.debug("overlay hide on close failed", exc_info=True)
        with self._lifecycle_lock:
            staged, self._staged_lifecycle_paths = self._staged_lifecycle_paths, set()
        for path in staged:
            path.unlink(missing_ok=True)
