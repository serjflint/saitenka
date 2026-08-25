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
    rgba_to_bgra,
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


#: Frames kept per overlay id. Two — the one just published and the one `_live` still names until
#: `overlay-add` returns. The correctness comes from `_in_flight` (see :meth:`Overlay._sweep`), not
#: from this number; it only spares the common case a needless hold.
RETAINED_FRAMES = 2


def _tail_path(tail: tuple) -> Path:
    """The frame path inside an ``overlay-add`` tail — ``(x, y, path, ...)``."""
    return Path(str(tail[2]))


def _discard(path: Path) -> bool:
    """Delete a frame we are done with; report whether it is gone.

    Windows refuses to delete a file another process still holds open, and mpv holds a frame open
    across `overlay-add`. That is a `PermissionError` — which `missing_ok=True` does NOT cover, since
    it only suppresses `FileNotFoundError`. A frame we cannot delete yet is a file to retry, not an
    error to raise at whoever happened to publish next.
    """
    try:
        path.unlink(missing_ok=True)
    except (
        OSError
    ):  # PermissionError on Windows; a full-disk/EBUSY unlink is equally not fatal here
        log.debug("could not retire overlay frame %s", path, exc_info=True)
        return False
    return True


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
        #: Every frame published for an oid, oldest first — see :meth:`_write_frame` / :meth:`_sweep`.
        self._frame_history: dict[int, list[Path]] = {}
        #: Paths some command is currently naming; retirement must not delete these.
        self._in_flight: dict[Path, int] = {}
        #: Frames a sweep could not delete (Windows holds them open) — retried on the next sweep.
        self._pending_deletion: set[Path] = set()

        self._live: dict[int, tuple] = {}  # physical oid -> last overlay-add tail, for repaint()
        self.visible = True
        self.ops = 0  # bumped on every add/remove; the controller watches it to nudge a paused OSD
        self._interaction_presenter = _InteractionPresenter()
        self._defer_interaction = _defer_interaction_for(ipc)
        self._interaction_oids: set[int] = set()
        self.lifecycle_oids: set[int] = set()
        self._staged_lifecycle_paths: set[Path] = set()
        #: One lock over the path maps — `_files`, `_frame_history`, `_in_flight`,
        #: `_pending_deletion`, `_live`. `_files` used to be written under `_frame_lock` in
        #: `_write_frame`, under `_lifecycle_lock` in the commit/remove paths, and unlocked in `hide`.
        #: `visible` and `ops` are deliberately NOT covered: they are a flag and a counter no
        #: retirement decision reads.
        self._state_lock = threading.Lock()
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
        return self._prepare(to_bgra(img), x, y, oid=oid, revision=revision)

    def prepare_rgba(
        self, rgba: np.ndarray, x: int = 0, y: int = 0, *, oid: int = 0, revision: int
    ) -> PreparedOverlay:
        """`prepare` for a caller whose pixels are already an RGBA array — no PIL round trip."""
        return self._prepare(rgba_to_bgra(rgba), x, y, oid=oid, revision=revision)

    def _prepare(
        self,
        converted: tuple[bytes, int, int, int],
        x: int,
        y: int,
        *,
        oid: int,
        revision: int,
    ) -> PreparedOverlay:
        physical_oid = self._oid(oid)
        data, w, h, stride = converted
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
        with self._state_lock:
            self.lifecycle_oids.add(oid)
            self._staged_lifecycle_paths.add(path)
        return PreparedOverlay(
            physical_oid,
            path,
            tail,
            ("overlay-add", physical_oid, *tail),
        )

    def commit_prepared(self, prepared: PreparedOverlay) -> None:
        with self._state_lock:
            previous = self._files.get(prepared.oid)
            self._files[prepared.oid] = prepared.path
            self._staged_lifecycle_paths.discard(prepared.path)
            self._live[prepared.oid] = prepared.tail
            self.ops += 1
        if previous != prepared.path:
            self._retire(prepared.oid, previous)

    def discard_prepared(self, prepared: PreparedOverlay) -> None:
        with self._state_lock:
            if self._files.get(prepared.oid) == prepared.path:
                return
            self._staged_lifecycle_paths.discard(prepared.path)
        _discard(prepared.path)

    def commit_remove(self, oid: int) -> None:
        physical_oid = self._oid(oid)
        with self._state_lock:
            self._live.pop(physical_oid, None)
            self.ops += 1
            path = self._files.pop(physical_oid, None)
            self.lifecycle_oids.discard(oid)
        self._retire_frame_path(physical_oid)
        self._retire(physical_oid, path)

    def remove_lifecycle_now(self, oid: int) -> dict:
        """Synchronously place a final remove behind any queued add before detaching from mpv."""
        physical_oid = self._oid(oid)
        return self.ipc.command("overlay-remove", physical_oid)

    def _oid(self, oid: int) -> int:
        """Map a logical overlay id (1-based) to the configured physical range."""
        return oid + (self.id_base - 1)

    def _add(self, oid: int, tail: tuple) -> dict:
        if self.visible:
            return self._issue("overlay-add", oid, tail)
        return {"error": "success"}

    def _write_frame(self, oid: int, data: bytes) -> Path:
        """Publish ``data`` at a FRESH path. Nothing is ever written over a file mpv may be reading.

        mpv reads the named file **inside** `overlay-add` (`cmd_overlay_add` → `_platform_memmove`,
        the frame both SIGBUS reports fault in). An in-place rewrite races that read, and a pagein
        against a file being truncated is a bus error inside mpv rather than an error we can observe.
        Publishing by `os.replace` onto one stable path per oid fixed that on POSIX but not on
        Windows, where replacing a file another process holds open without FILE_SHARE_DELETE raises
        `PermissionError` — and the share mode belongs to mpv, so we cannot ask for it.

        A new path per frame has neither problem: the old inode is never touched, and no rename lands
        on a live file. What it costs is that frames must now be retired deliberately — see
        :meth:`_sweep`, which is where this design can actually break.

        Cost is not a reason to prefer the old form: a fresh inode per frame measured at 0.4 ms
        against 0.5 ms for reusing one, for a 1.1 MB payload.
        """
        path = self._new_frame_path(oid)
        try:
            path.write_bytes(data)  # outside the lock: no other thread can name this path yet
        except Exception:
            path.unlink(missing_ok=True)  # nothing has named it yet, so a plain unlink is safe
            raise
        with self._state_lock:
            self._frame_history.setdefault(oid, []).append(path)
            self._files[oid] = path
            # Held on the CALLER's behalf, released once it has assigned `_live`. A hold that lasted
            # only for `overlay-add` leaves a gap: `_live` is written after the command returns, so a
            # second publisher on this oid can sweep the path in between and leave `_live` naming a
            # file that is gone. `show`/`show_bgra` release it in a `finally`.
            self._hold([path])
        self._sweep(oid)
        return path

    def _new_frame_path(self, oid: int) -> Path:
        with tempfile.NamedTemporaryFile(
            prefix=f"saitenka-osd-{oid}-", suffix=".bgra", delete=False
        ) as staged:
            return Path(staged.name)

    def _sweep(self, oid: int, retain: int | None = None) -> None:
        """Delete this oid's frames beyond the retention window, plus anything a previous sweep could
        not delete.

        A path is kept while some command still names it. A count alone would only *narrow* the
        window — the same objection that rejected an alternating pair of slots — because `repaint`
        re-issues `_live`'s tail from another thread and `_live` is assigned only after `overlay-add`
        returns. `_in_flight` closes it: every issuer registers the path it is about to name under the
        same lock acquisition that reads it, so retirement can never pull a path out from under a
        command already carrying it.
        """
        # Resolved per call, not as a default argument: a default binds RETAINED_FRAMES at import, so
        # anything varying the constant would silently keep sweeping at the original depth.
        # `max(1, ...)`: `history[:-0]` is empty and `history[-0:]` is the whole list, so a literal
        # zero would retain everything — the opposite of what asking for none means.
        retain = max(1, RETAINED_FRAMES if retain is None else retain)
        with self._state_lock:
            history = self._frame_history.get(oid)
            stale = set(self._pending_deletion)
            if history is not None:
                stale |= set(history[:-retain])
                self._frame_history[oid] = history[-retain:]
            # Spared: paths a command is currently carrying, and paths `_live` still names. The
            # second is not redundant — `_live[oid]` is assigned only after `overlay-add` returns, so
            # with two publishers on one oid it can lag several frames behind the history head. A
            # hold taken at repaint time cannot save a path that was already deleted before it.
            held = {path for path in stale if path in self._in_flight} | {
                path for path in stale if path in self._live_paths()
            }
            self._pending_deletion = held
            stale -= held
        failed = {path for path in stale if not _discard(path)}
        if failed:
            with self._state_lock:
                self._pending_deletion |= failed

    def _live_paths(self) -> set[Path]:
        """Paths the current `_live` tails name. Caller must hold ``_state_lock``."""
        return {_tail_path(tail) for tail in self._live.values()}

    def _publish_live(self, oid: int, tail: tuple) -> None:
        with self._state_lock:
            self._live[oid] = tail
            self.ops += 1

    def _hold(self, paths: list[Path]) -> list[Path]:
        """Register ``paths`` against retirement. Caller must hold ``_state_lock``."""
        for path in paths:
            self._in_flight[path] = self._in_flight.get(path, 0) + 1
        return paths

    def _release(self, paths: list[Path]) -> None:
        with self._state_lock:
            for path in paths:
                remaining = self._in_flight.get(path, 0) - 1
                if remaining > 0:
                    self._in_flight[path] = remaining
                else:
                    self._in_flight.pop(path, None)

    def _issue(self, command: str, oid: int, tail: tuple) -> dict:
        """Send a command naming a frame path, holding that path for the duration of the call."""
        with self._state_lock:
            paths = self._hold([_tail_path(tail)])
        try:
            return self.ipc.command(command, oid, *tail)
        finally:
            self._release(paths)

    def _reissue_live(self) -> int:
        """Re-send every live tail; returns how many went out.

        The snapshot of ``_live`` and the hold on the paths it names happen under ONE lock
        acquisition. Reading first and registering second would leave exactly the window this is
        meant to close.
        """
        with self._state_lock:
            pending = list(self._live.items())
            paths = self._hold([_tail_path(tail) for _, tail in pending])
        try:
            for oid, tail in pending:
                self.ipc.command("overlay-add", oid, *tail)
        finally:
            self._release(paths)
        return len(pending)

    def _retire_frame_path(self, oid: int) -> None:
        with self._state_lock:
            self._pending_deletion |= set(self._frame_history.pop(oid, ()))
        self._sweep(oid)

    def _retire(self, oid: int, path: Path | None) -> None:
        """Retire one frame through the same guards a swept frame gets.

        Deleting it outright is what `commit_prepared` / `commit_remove` / `hide` used to do, and it
        made `_sweep`'s guarantee false: the path they drop is the LAST published one, which is
        exactly the path a concurrent `repaint` is most likely to be carrying. Queue it instead, so
        it goes when nothing names it.
        """
        if path is None:
            return
        with self._state_lock:
            self._pending_deletion.add(path)
        self._sweep(oid)

    def show(self, img: Image.Image, x: int = 0, y: int = 0, oid: int = 0) -> dict:
        label = _oid_label(oid)
        oid = self._oid(oid)
        with otel_metrics.instrumented(
            otel_metrics.upload_duration_ms, "upload", oid=label
        ) as span:
            data, w, h, stride = to_bgra(img)
            path = self._write_frame(oid, data)
            try:
                tail = (int(x), int(y), str(path), 0, "bgra", w, h, stride)
                res = self._add(oid, tail)
                _set_draw_geometry(span, x, y, w, h)
                self._publish_live(oid, tail)
            finally:
                self._release([path])
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
            try:
                tail = (int(x), int(y), str(path), 0, "bgra", w, h, w * 4)
                res = self._add(oid, tail)
                _set_draw_geometry(span, x, y, w, h)
                self._publish_live(oid, tail)
            finally:
                self._release([path])
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
        return self._hide_physical(self._oid(oid))

    def _hide_physical(self, oid: int) -> dict:
        """``hide`` for an id that is ALREADY physical — `_files`/`_live` are keyed that way, so a
        caller iterating them must not send it back through the `id_base` shift a second time."""
        # Removal is idempotent.  Always send it: an in-flight deferred add can finish after visibility
        # was turned off, and skipping this command would leave those pixels stuck in mpv.
        res = self.ipc.command("overlay-remove", oid)
        with self._state_lock:
            self._live.pop(oid, None)
            self.ops += 1
            p = self._files.pop(oid, None)
        self._retire_frame_path(oid)
        self._retire(oid, p)
        return res

    def set_visible(self, *, visible: bool) -> None:
        """Hide/show Saitenka's surfaces while retaining their latest desired state."""
        if visible == self.visible:
            return
        self.visible = visible
        if visible:
            self.ops += self._reissue_live()
        else:
            for oid in [oid for oid, _ in list(self._live.items())]:
                self.ipc.command("overlay-remove", oid)
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
            self._reissue_live()

    def close(self) -> None:
        self._interaction_presenter.close()
        for oid in list(self._files):  # physical ids — see `_hide_physical`
            try:
                self._hide_physical(oid)
            except Exception:
                log.debug("overlay hide on close failed", exc_info=True)
        with self._state_lock:
            staged, self._staged_lifecycle_paths = self._staged_lifecycle_paths, set()
            staged |= self._pending_deletion | {
                path for history in self._frame_history.values() for path in history
            }
            self._pending_deletion, self._frame_history = set(), {}
            self._in_flight.clear()  # nothing can be in flight once the presenter is closed
        for path in staged:
            _discard(path)
