"""Golden-image test helpers + the shared render-profile matrix.

Golden helpers: anti-aliasing varies subtly across Pillow/FreeType versions, so goldens are compared
with a per-pixel mean-absolute-error tolerance rather than byte-exact. Set ``SAITENKA_UPDATE_GOLDEN=1``
to (re)write goldens instead of asserting — always eyeball the change before committing.

Render-profile matrix (``PROFILES`` / ``ENTRY_FACTORIES``): the one place the scale × width × entry
axes live, so a property extended to a new mode inherits the corners instead of drifting. Lives here,
NOT in ``conftest.py`` (which 131 test modules depend on) — see the section below.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pytest
from PIL import Image

from saitenka import otel_metrics
from saitenka.app.features.tooltip import tooltip_raster
from saitenka.app.session.mpv_gateway import MpvGateway
from saitenka.model import Theme
from saitenka.mpvio.ipc import IPCRequest
from saitenka.panel import Definition, Entry, Freq, panel_rows, render_panel
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome
from saitenka.runtime.mailbox import SessionMailbox

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.render.layout_backend import LayoutBackend


def await_ready(
    ready: Callable[[], bool],
    message: str,
    *,
    pump: Callable[[], None] = lambda: None,
    timeout: float = 5.0,
) -> None:
    """Wait on a deadline for work happening on another thread.

    Not `for _ in range(200): sleep(0.001)`. That is a *scheduling* budget wearing a timeout's
    clothes: under the whole suite at `-n auto` the awaited thread can lose more than 200ms before
    it is ever scheduled, so it fails on a busy machine and passes alone — which is why the ones it
    bit never reproduced in isolation. A deadline fails just as fast when the work is genuinely
    wedged and does not fail when the machine is merely busy, so the bound can be generous.

    `pump` runs before each check and at least once, for a consumer that has to be driven.
    """
    deadline = time.monotonic() + timeout
    while True:
        pump()
        if ready():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        time.sleep(0.001)


def drain_for(pump: Callable[[], None], *, seconds: float = 0.2) -> None:
    """Drive a consumer for a wall-clock window, then let the caller assert nothing arrived.

    The negative counterpart of `await_ready`, and a poll count is wrong here for the same reason:
    `for _ in range(200): sleep(0.001)` is 200 *scheduling slots*, not 200ms, so on a busy machine
    the thread that was supposed to get a chance to misbehave never ran and the test passes by not
    having looked. A window is short by design — a negative can only ever be "not within this long".
    """
    deadline = time.monotonic() + seconds
    while True:
        pump()
        if time.monotonic() >= deadline:
            return
        time.sleep(0.001)


REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("SAITENKA_UPDATE_GOLDEN") == "1"


def bare_gateway(ipc) -> MpvGateway:
    """Construct transport runtime machinery without an application reactor."""
    return MpvGateway(ipc, SessionMailbox())


def session_gateway(ipc) -> MpvGateway:
    """Construct the complete runtime required by a composed study session."""
    from saitenka.app.session.routes import install_session_reactor

    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    return gateway


# Goldens are blessed on macOS (canonical, committed at golden/<name>). Text-heavy panels can render a
# pixel wider under a different FreeType (e.g. Linux CI), which the MAE diff can't absorb once the size
# differs — so a platform may carry an override at golden/<plat>/<name>. Reads prefer the override and
# fall back to canonical; `SAITENKA_UPDATE_GOLDEN=1` on a non-canonical platform writes the override.
_PLATFORM_DIR = {"linux": "linux", "win32": "windows"}.get(
    sys.platform
)  # None on darwin (canonical)


def _golden_target(name: str) -> Path:
    if _PLATFORM_DIR is not None:
        override = GOLDEN_DIR / _PLATFORM_DIR / name
        if override.exists() or UPDATE:
            return override
    return GOLDEN_DIR / name


def tiny_font(family: str) -> bytes:
    """A real, valid font of a couple of kilobytes, advertising `family` and nothing else.

    Real because the family names are read out of an actual name table — a hand-built stub would
    test the reader against the reader's own assumptions. Subset because an unmodified face is two
    megabytes, and a `[Fonts]` section carrying one costs every cue in the test a re-parse.
    """
    from fontTools import subset
    from fontTools.ttLib import TTFont

    font = TTFont(REPO_ROOT / "src/saitenka/assets/fonts/NotoSans.ttf")
    subset.Subsetter(subset.Options(layout_features=[], notdef_outline=True)).subset(font)
    font["name"].names = [
        record for record in font["name"].names if record.nameID in {1, 2, 4, 6, 16}
    ]
    for record in font["name"].names:
        if record.nameID in {1, 4, 16}:
            record.string = family
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def uuencode(raw: bytes) -> str:
    """The inverse of libass's `decode_chars`: 3 bytes to 4 characters, big-endian, offset by 33.

    Stated here independently of the decoder under test — a decoder checked against its own inverse
    proves only that it is self-consistent.
    """
    chars: list[str] = []
    for start in range(0, len(raw), 3):
        group = raw[start : start + 3]
        value = sum(byte << (8 * (2 - index)) for index, byte in enumerate(group))
        chars += [chr(((value >> (6 * (3 - i))) & 63) + 33) for i in range(len(group) + 1)]
    return "\n".join("".join(chars[i : i + 80]) for i in range(0, len(chars), 80))


def ass_fonts_section(*families: str) -> str:
    """An ASS ``[Fonts]`` section supplying one real font per family, encoded as libass reads it."""
    entries = "".join(
        f"fontname: {family.replace(' ', '')}_0.ttf\n{uuencode(tiny_font(family))}\n"
        for family in families
    )
    return f"[Fonts]\n{entries}\n"


def bgra_to_image(bgra: np.ndarray) -> Image.Image:
    """Un-premultiply a premultiplied-BGRA array back to an RGBA image, for goldens on cached panels
    (which retain only the compressed BGRA). Exact where alpha is 0 or 255 (the bulk of a panel);
    ±1 at anti-aliased edges — well within the golden MAE tolerance."""
    b, g, r, a = (bgra[..., i].astype(np.uint16) for i in range(4))
    rgb = np.stack([r, g, b], axis=-1)
    safe_a = np.where(a == 0, 1, a)[..., None]
    rgb = np.where(a[..., None] > 0, np.minimum(255, rgb * 255 // safe_a), 0)
    rgba = np.dstack([rgb.astype(np.uint8), a.astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def mae(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute error per channel (0..255) between two RGBA images of equal size."""
    aa = np.asarray(a.convert("RGBA"), dtype=np.int16)
    bb = np.asarray(b.convert("RGBA"), dtype=np.int16)
    return float(np.abs(aa - bb).mean())


def assert_golden(img: Image.Image, name: str, tol: float = 2.0) -> None:
    """Compare ``img`` to the golden for ``name`` within mean-abs-error ``tol`` (or update it).

    Strict (size-exact + ``tol``) on the canonical platform or against an exact per-platform override —
    that's the behavior-review gate. When a non-canonical platform falls back to a canonical golden, a
    differently-built bundled FreeType shifts text by ≤1px and nudges anti-aliasing, so we allow a 1px
    size slack (compare the common box) and a relaxed tolerance rather than fail on sub-pixel drift."""
    path = _golden_target(name)
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        if not UPDATE:
            raise AssertionError(f"golden {name} was missing — created it; re-run to verify")
        return
    golden = Image.open(path)
    strict = _PLATFORM_DIR is None or path.parent.name == _PLATFORM_DIR
    if strict:
        assert img.size == golden.size, f"{name}: size {img.size} != golden {golden.size}"
        err = mae(img, golden)
        assert err <= tol, f"{name}: mean-abs-error {err:.3f} exceeds tol {tol}"
        return
    assert abs(img.width - golden.width) <= 1 and abs(img.height - golden.height) <= 1, (
        f"{name}: size {img.size} vs canonical {golden.size} differs by more than 1px"
    )
    w, h = min(img.width, golden.width), min(img.height, golden.height)
    box = (0, 0, w, h)
    err = mae(img.crop(box), golden.crop(box))
    limit = max(tol, 4.0)
    assert err <= limit, f"{name}: cross-platform mean-abs-error {err:.3f} exceeds {limit}"


def validate_ctf_document(data: dict) -> None:
    """Assert *data* is a structurally valid Chrome Trace Format document — the shape Perfetto /
    ``chrome://tracing`` actually require, not just "some events landed". Deliberately NOT a
    byte/value golden comparison: ``ts``/``dur``/``span_id``/``trace_id``/``tid`` are inherently
    non-deterministic run to run (real timestamps, random IDs, real native thread ids), so this
    checks shape/types/required-keys-per-``ph``, not exact values. See ``tests/golden/sample_trace.json``
    for a real example this validates (bundled, not compared value-for-value)."""
    assert "traceEvents" in data, "missing top-level 'traceEvents' key"
    events = data["traceEvents"]
    assert isinstance(events, list), "'traceEvents' must be a list"
    for i, e in enumerate(events):
        assert isinstance(e, dict), f"event {i} is not an object"
        for key in ("name", "ph", "ts", "pid"):
            assert key in e, f"event {i} ({e.get('name')!r}) missing required key {key!r}"
        assert isinstance(e["name"], str) and e["name"], f"event {i} has an empty/non-string name"
        assert isinstance(e["ts"], int | float), f"event {i} 'ts' must be numeric"
        assert isinstance(e["pid"], int), f"event {i} 'pid' must be an int"
        ph = e["ph"]
        if ph == "X":  # complete (duration) event — spans
            for key in ("dur", "tid"):
                assert key in e, f"event {i} (X/{e['name']}) missing required key {key!r}"
            assert isinstance(e["dur"], int | float) and e["dur"] >= 0, (
                f"event {i} 'dur' invalid: {e.get('dur')!r}"
            )
            assert isinstance(e["tid"], int), f"event {i} 'tid' must be an int"
        elif ph == "C":  # counter event — gauges/counters
            assert "args" in e and "value" in e.get("args", {}), (
                f"event {i} (C/{e['name']}) missing args.value"
            )
            assert isinstance(e["args"]["value"], int | float), (
                f"event {i} counter value not numeric: {e['args']['value']!r}"
            )
        else:
            raise AssertionError(
                f"event {i} has unsupported ph={ph!r} (this app only produces X/C)"
            )


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
        #: The real transport lets a consumer WAIT for an event rather than ask repeatedly. A fake
        #: that always returns instantly cannot tell a blocking loop from a spinning one, so it
        #: mirrors the signal here — under a lock, like production, or an emit racing a drain is a
        #: lost wake and the consumer sleeps through an event that already arrived.
        self._event_arrived = threading.Event()
        self._events_lock = threading.Lock()
        self.props: dict = {}
        #: What mpv's `~~` placeholders expand to; `None` is the `--no-config` answer.
        self.config_dir: str | Path | None = None
        self.commands: list[tuple] = []
        self.requests: list[IPCRequest] = []
        self._event_sink = None
        self._connection_sink = None
        self._session_loop = None
        self._runtime_gateway = None
        self.runtime_outcomes: list[object] = []
        #: Both are on every `MpvIPC` from construction, so a stand-in that omits them is a fake
        #: production would not recognise. They were reachable only through a `getattr(ipc, …, x)`
        #: probe, which answered "absent" for this fake and for a rename alike.
        self.connected_at: float | None = None  # set once the transport connects; never here
        self._bytes_read = 0  # a fake reads nothing off a wire, and 0 is what that means
        #: Named timers scheduled through the runtime port, newest per name. Nothing fires on a
        #: wall clock — a test calls `fire_runtime_timer` so ordering stays deterministic.
        self.timers: dict[str, tuple[object, Callable[[object], None]]] = {}
        #: Every schedule/cancel by timer name, in order. A ledger, not a live view: "retired
        #: exactly once" is a statement about the calls, which `timers` alone cannot answer.
        self.timer_log: list[tuple[str, str]] = []

    def schedule_runtime_timer(self, *, timer: str, identity, on_finished, **_kwargs) -> bool:
        self.timers[timer] = (identity, on_finished)
        self.timer_log.append(("schedule", timer))
        return True

    def cancel_runtime_timer(self, timer: str) -> bool:
        self.timer_log.append(("cancel", timer))
        return self.timers.pop(timer, None) is not None

    def timer_calls(self, timer: str) -> list[str]:
        return [action for action, name in self.timer_log if name == timer]

    def fire_runtime_timer(self, timer: str, *, outcome=None) -> bool:
        """Deliver a scheduled timer's due event, as the gateway would."""
        from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

        entry = self.timers.pop(timer, None)
        if entry is None:
            return False
        identity, on_finished = entry
        on_finished(
            EffectFinished(
                EffectId(0), Owner.SUBTITLE, identity, outcome or EffectOutcome.SUCCEEDED
            )
        )
        return True

    def set_prop(self, name: str, value) -> None:
        """Simulate mpv: update the property AND emit a buffered property-change event."""
        self.props[name] = value
        self.emit({"event": "property-change", "name": name, "data": value})

    def emit(self, event: dict) -> None:
        with self._events_lock:
            if self._event_sink is None:
                self.events.append(event)
            else:
                self._event_sink(event, 0)
            self._event_arrived.set()

    def pump(self) -> None:
        """Real IPC reads the socket here; the fake's events are queued directly."""

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        if args and args[0] == "expand-path":
            return {"data": self._expanded(str(args[1]))}
        return {"data": None}

    def expand_path(self, path: str) -> str | None:
        """mpv's `expand-path`, over a config directory a test can point somewhere real.

        Through `command` like `MpvIPC.expand_path`, so a subclass simulating mpv sees the read; a
        fake with a second path for one channel reads as a production bug. With no config directory
        set the `~~` prefix simply falls away, which is what mpv answers under `--no-config`: every
        platform path is NULL there, so the placeholder resolves to a bare relative name.
        """
        reply = self.command("expand-path", path)
        data = reply.get("data") if isinstance(reply, dict) else None
        return data if isinstance(data, str) and data else None

    def _expanded(self, path: str) -> str:
        if not path.startswith("~~"):
            return path
        stripped = path.removeprefix("~~").removeprefix("/")
        if self.config_dir is None:
            return stripped
        return str(Path(self.config_dir) / stripped)

    def probe(self, name: str) -> dict:
        return self.command("get_property", name)

    def query(self, name: str) -> object | None:
        # Through `command`, like `command_async`: a subclass that simulates mpv state must see
        # every read, and a fake with a second path for one channel reads as a production bug.
        # The error check mirrors `MpvIPC.query` — a fake that answered past an error would let a
        # caller depend on a payload production discards.
        reply = self.probe(name)
        if not isinstance(reply, dict) or reply.get("error") not in {None, "success"}:
            return None
        return reply.get("data")

    def command_async(self, *args, expected_connection_epoch=None):
        del expected_connection_epoch
        # Delegate to `command` rather than recording directly: mpv has one channel, and a subclass
        # that simulates state (track selection, sub-add/remove) must see async writes too.
        reply = self.command(*args)
        future: Future[dict] = Future()
        future.set_result({"error": "success", **reply})
        request = IPCRequest(len(self.requests), 0, future)
        self.requests.append(request)
        return request

    def receive_session(self, timeout: float | None, handle) -> None:
        loop = self._session_loop
        if loop is not None:
            loop.receive(timeout, handle)
            return
        if timeout:
            with self._events_lock:
                pending = bool(self.events)
            if not pending:
                self._event_arrived.wait(timeout)
        with self._events_lock:
            evs, self.events = self.events, []
            self._event_arrived.clear()
        for event in evs:
            handle(event)

    @property
    def session_loop(self):
        return self._session_loop

    def drain_events(self, timeout: float | None = 0.0) -> list:
        events: list = []
        self.receive_session(timeout, events.append)
        return events

    def install_runtime_ingress(self, event_sink, connection_sink, session_loop, gateway):
        self._event_sink = event_sink
        self._connection_sink = connection_sink
        self._session_loop = session_loop
        self._runtime_gateway = gateway
        for event in self.events:
            event_sink(event, 0)
        self.events = []

    def register_runtime_observers(self, names: tuple[str, ...]) -> dict[str, dict]:
        """Register observers the way production does — through the gateway when one is wired.

        Without this the fake forces `register_observer_set` down its no-gateway fallback, which
        issues the same commands but never tells the gateway which observers exist, so reconnect
        replay is silently not exercised.
        """
        gateway = self._runtime_gateway
        if gateway is None:
            return {}
        return gateway.register_observers(names)

    def publish_runtime_event(self, event) -> bool:
        if self._runtime_gateway is None:
            return False
        return self._runtime_gateway.publish_session_event(event)

    def deliver_runtime_event(self, event) -> bool:
        if self._runtime_gateway is None:
            return False
        return self._runtime_gateway.deliver_session_event(event)

    def register_session_resource(self, name: str, resource: object) -> bool:
        if self._runtime_gateway is None:
            return False
        self._runtime_gateway.session_resources[name] = resource
        return True

    def submit_runtime_mpv(self, **kwargs) -> bool:
        if self._runtime_gateway is not None:
            return self._runtime_gateway.submit_mpv(**kwargs)
        return self._submit_inline(**kwargs)

    def _submit_inline(self, *, identity, command, on_finished, **_kwargs) -> bool:
        """Run a correlated command and complete it before returning.

        A fake without a gateway used to refuse the submit, which left every caller on a
        synchronous fallback that production never takes. Completing inline keeps the egress
        identical to production's while staying deterministic; delivery goes through `command` so
        this fake's own mpv-state simulation sees the write, and reply errors map the way
        `MpvGateway._reply` maps them.
        """
        from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome, Owner

        outcome, error, result = EffectOutcome.SUCCEEDED, None, None
        try:
            reply = self.command(*command)
        except Exception:  # noqa: BLE001  # the gateway reports a dead pipe, it never raises
            outcome, error = EffectOutcome.FAILED, EffectError.DISCONNECTED
        else:
            if isinstance(reply, dict):
                result = reply.get("data")
                if reply.get("error") not in {None, "success"}:
                    outcome = EffectOutcome.FAILED
                    error = {
                        "disconnected": EffectError.DISCONNECTED,
                        "timeout": EffectError.TIMEOUT,
                        "overloaded": EffectError.OVERLOADED,
                    }.get(reply.get("error"), EffectError.INVALID_RESULT)
        on_finished(
            EffectFinished(
                EffectId(0), Owner.SUBTITLE, identity, outcome, result=result, error=error
            )
        )
        return True

    def publish_command_outcome(self, outcome) -> None:
        if self._runtime_gateway is None:
            self.runtime_outcomes.append(outcome)
        else:
            self._runtime_gateway.publish_command_outcome(outcome)

    def register_runtime_job_lane(self, name, policy, handler) -> bool:
        if self._runtime_gateway is None:
            return False
        self._runtime_gateway.register_job_lane(name, policy, handler)
        return True

    def submit_runtime_job(self, **kwargs) -> bool:
        if self._runtime_gateway is None:
            return False
        return self._runtime_gateway.submit_job(**kwargs)

    def close_runtime_job_lane(self, name, timeout=2.0) -> bool:
        if self._runtime_gateway is None:
            return False
        return self._runtime_gateway.close_job_lane(name, timeout)

    def wake_session_runtime(self) -> bool:
        if self._runtime_gateway is None:
            return False
        self._runtime_gateway.mailbox.wake()
        return True

    def close_session_runtime(self) -> bool:
        reactor = getattr(self._runtime_gateway, "session_reactor", None)
        if reactor is None:
            return False
        reactor.close()
        return True

    def session_runtime_census(self) -> dict[str, int]:
        gateway = self._runtime_gateway
        return {} if gateway is None else gateway.runtime_census()

    def route_session_playback(self, envelope) -> object | None:
        """Mirror the transport's `Owner.PLAYBACK` port, including its no-reactor refusal.

        A fake that always refused would keep every controller test on the SessionController-owned store and
        leave the routed path — the one production takes — exercised only where a test installs a
        gateway by hand.
        """
        reactor = getattr(self._runtime_gateway, "session_reactor", None)
        if reactor is None:
            return None
        if envelope is not None:
            reactor.handle(envelope)
        return reactor.state.playback

    def route_session_lifecycle(self, envelope) -> object | None:
        """Mirror the transport's `Owner.SESSION` port, refusal included — as above."""
        reactor = getattr(self._runtime_gateway, "session_reactor", None)
        if reactor is None:
            return None
        if envelope is not None:
            reactor.handle(envelope)
        return reactor.state.session

    def route_session_subtitle(self, envelope) -> object | None:
        """Mirror the transport's `Owner.SUBTITLE` port, refusal included — as above."""
        reactor = getattr(self._runtime_gateway, "session_reactor", None)
        if reactor is None:
            return None
        if envelope is not None:
            reactor.handle(envelope)
        return reactor.state.subtitle

    def route_session_interaction(self, envelope) -> object | None:
        """Mirror the transport's `Owner.INTERACTION` port, refusal included — as above."""
        reactor = getattr(self._runtime_gateway, "session_reactor", None)
        if reactor is None:
            return None
        if envelope is not None:
            reactor.handle(envelope)
        return reactor.state.interaction

    def route_session_presentation(self, envelope) -> object | None:
        """Mirror the transport's `Owner.PRESENTATION` port, refusal included — as above."""
        reactor = getattr(self._runtime_gateway, "session_reactor", None)
        if reactor is None:
            return None
        if envelope is not None:
            reactor.handle(envelope)
        return reactor.state.presentation


def keybind_registry(ipc: FakeIPC) -> dict[str, str]:
    """The ``{key: message}`` map mpv would hold after registration, reconstructed from the recorded
    ``keybind`` commands. Honours later-binds-over-earlier and the ``keybind KEY ignore`` unbinds that
    surface teardown emits (a key bound then neutralised drops out). FakeIPC only *records* the bind
    string — it can't fire the handler — so this is the seam :func:`press` dispatches through."""
    reg: dict[str, str] = {}
    for cmd in ipc.commands:
        if len(cmd) >= 3 and cmd[0] == "define-section":
            # The "global" scope installs as ONE section rather than a keybind per key. Parsed here
            # so `press` dispatches through the same registry either way — a test asserting a
            # shortcut works must not have to know which form registered it.
            for line in str(cmd[2]).splitlines():
                key, _, spec = line.partition(" ")
                if spec.startswith("script-message "):
                    reg[key] = spec.removeprefix("script-message ")
            continue
        if len(cmd) >= 3 and cmd[0] == "keybind":
            key, spec = cmd[1], cmd[2]
            if isinstance(spec, str) and spec.startswith("script-message "):
                reg[key] = spec.removeprefix("script-message ")
            else:  # "ignore" (or any non-script-message) neutralises the key
                reg.pop(key, None)
    return reg


def press(reader, ipc: FakeIPC, key: str) -> None:
    """Fire the handler bound to ``key`` through the REAL dispatch chain — a synthetic mpv
    ``client-message`` published to the session mailbox and settled by the owner-thread turn. This
    is the hop FakeIPC cannot infer from a recorded bind, so a test that only checks
    ``ipc.commands`` proves saitenka sent the bind, not that a press runs the action. Raises
    :class:`KeyError` if ``key`` is not currently bound."""
    reg = keybind_registry(ipc)
    if key not in reg:
        raise KeyError(f"{key!r} is not bound (registered: {sorted(reg)})")
    ipc.emit({"event": "client-message", "args": [reg[key]]})
    reader.pump()


class FakeTransport:
    """In-memory ``Transport`` double (see ``saitenka.mpvio.transport.Transport``) for the transport
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

    def snapshot(self) -> bytes:
        """What the client has written so far, copied under the lock.

        The writer is another thread, so ``bytes(fake.sent)`` from the test races the ``extend``
        that grows it — no-GIL has no bytecode-level shelter for the copy.
        """
        with self._cond:
            return bytes(self.sent)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


# --- Shared render-profile matrix (scale × width × entry shape) -------------------------------------
# The banded-engine PBT was written at ``Theme()`` (scale 1.0) and a single fixed ``WIDTH``, so every
# earlier property ran where the crisp NATIVE path (only live at scale>1) is a no-op. These factories +
# curated PROFILES carry the scale/width/entry axes the later modes added. Extend the LIST, not each
# test — a new mode/cache = one appended row and every property that draws from PROFILES inherits it.


def short_entry() -> Entry:
    """A stubby entry (one short def) — a width change with almost no scroll range."""
    return Entry(
        headword=["手", {"tag": "rt", "content": "て"}],
        defs=[Definition("辞書", ["からだの先の部分。"])],
    )


def tall_entry(n_defs: int = 8) -> Entry:
    """Many long paragraphs → a real scroll range (the canonical banded-PBT shape)."""
    para = "これはとても長い定義の説明でありスクロールが必要になるほど縦に伸びる本文です。" * 2
    return Entry(
        headword=["本命", {"tag": "rt", "content": "ほんめい"}],
        defs=[Definition(f"辞書{i}", [para]) for i in range(n_defs)],
    )


def cjk_links_entry(n_defs: int = 8) -> Entry:
    """CJK body with an inline cross-reference link → scan cells AND link boxes (the hit-parity shape)."""
    body = [
        "追いかけると同義語は",
        {"tag": "a", "href": "?query=見る", "content": "見る"},
        "。長い説明文が続く。" * 2,
    ]
    return Entry(
        headword=["掛ける", {"tag": "rt", "content": "かける"}],
        defs=[Definition(f"辞書{i}", body) for i in range(n_defs)],
    )


def many_homograph_entry(n_defs: int = 12) -> Entry:
    """A synthetic polysemous word: many varied CJK defs + links, the tallest-wrap shape. (The real
    掛ける/する parity is the skip-if-no-DB integration test — this is its deterministic stand-in.)"""
    bodies = [
        ["ある動作をすること。", {"tag": "a", "href": "?query=為る", "content": "為る"}, "の意。"],
        ["水などを上からそそぐ。「水を—」" * 2],
        [
            "ある状態にする。「鍵を—」また、",
            {"tag": "a", "href": "?query=掛かる", "content": "掛かる"},
        ],
        ["長い説明文がここに続いて縦に伸びる本文。" * 3],
    ]
    return Entry(
        headword=["掛ける", {"tag": "rt", "content": "かける"}],
        defs=[Definition(f"語義{i}", bodies[i % len(bodies)]) for i in range(n_defs)],
    )


def chip_heavy_entry(n_defs: int = 5) -> Entry:
    """Header dense with chips/pills — word tags, a three-way freq row, a reading-label pill, an
    inflection chain, and per-def defTag chips — the chip-row shape Phase B reflows to width."""
    return Entry(
        headword=["恐らく", {"tag": "rt", "content": "おそらく"}],
        tags=["副", "常用", "★", "priority form"],
        freqs=[
            Freq("JPDB", "1234", (200, 80, 120, 255)),
            Freq("BCCWJ", "5678", (80, 140, 200, 255)),
            Freq("アニメ", "342", (120, 170, 90, 255)),
        ],
        reading_label=("大辞泉", "おそらく"),
        inflection_chain=["-て", "-いる", "-た"],
        defs=[
            Definition(f"辞書{i}", ["おおかた。たぶん。恐らく間違いない。"], tags=["★", "文語"])
            for i in range(n_defs)
        ],
    )


def ruby_heavy_entry(n_defs: int = 4) -> Entry:
    """Furigana ruby boxes packed inline — the nested-flex ruby-clearance shape Phase B targets."""
    body = [
        "彼は",
        {"tag": "rt", "content": "かれ"},
        "、",
        {"tag": "rt", "content": "まいにち"},
        "毎日",
        {"tag": "rt", "content": "べんきょう"},
        "勉強する。",
    ]
    return Entry(
        headword=["勉強", {"tag": "rt", "content": "べんきょう"}],
        defs=[Definition(f"辞書{i}", body, tags=["★"]) for i in range(n_defs)],
    )


def wide_cjk_entry(n_defs: int = 3) -> Entry:
    """Long unbroken CJK paragraphs → the tallest wrap-by-width range (kinsoku under pressure)."""
    para = "親譲りの無鉄砲で小供の時から損ばかりしている。" * 4
    return Entry(
        headword=["無鉄砲", {"tag": "rt", "content": "むてっぽう"}],
        defs=[Definition(f"辞書{i}", [para]) for i in range(n_defs)],
    )


ENTRY_FACTORIES = {
    "short": short_entry,
    "tall": tall_entry,
    "cjk_links": cjk_links_entry,
    "many_homograph": many_homograph_entry,
    "chip_heavy": chip_heavy_entry,
    "ruby_heavy": ruby_heavy_entry,
    "wide_cjk": wide_cjk_entry,
}


class Profile(NamedTuple):
    """A (theme, width, entry-shape) corner of the render config space. Carries the production ``Theme``
    itself (not a bare scale float) — so ``profile.theme`` is passed straight through to ``panel_rows`` /
    the windowed engine with no re-construction, and a profile can vary any theme axis, not just scale.
    ``width`` stays a sibling: it's the OTHER, independent geometry axis (one theme renders many widths)."""

    theme: Theme
    width: int
    entry_key: str

    def entry(self) -> Entry:
        return ENTRY_FACTORIES[self.entry_key]()

    @property
    def id(self) -> str:  # a readable pytest node-id suffix
        return f"s{self.theme.scale}-w{self.width}-{self.entry_key}"

    def windowed(self, *, backend: LayoutBackend | None = None, **tuning):
        """Build the ``WindowedPanel`` for this corner — the ONE place ``panel_rows`` and
        ``WindowedPanel`` are fed ``(width, theme)``, so the two can't silently disagree (the hi-dpi
        footgun where a test lays out at one geometry and windows at another). ``backend`` selects the
        layout engine (the outer matrix axis); extra kwargs pass through (e.g. ``tuning=``)."""
        from saitenka.render.banded import WindowedPanel

        rows = panel_rows(self.entry(), self.width, self.theme)
        return WindowedPanel(rows, self.width, self.theme, layout_backend=backend, **tuning)

    def reference_render(self) -> Image.Image:
        """The one-shot ``render_panel`` image at THIS corner's ``(width, theme)`` — what the windowed
        viewport is diffed against. Same single-source-of-geometry contract as ``windowed``."""
        return render_panel(self.entry(), width=self.width, theme=self.theme)

    def reference_total(self) -> int:
        """Full reference height — the scroll extent the windowed engine must reproduce."""
        return self.reference_render().height


def layout_backends() -> list[tuple[str, LayoutBackend]]:
    """(name, backend) pairs for the layout-engine matrix axis: the pure-Python default + the independent
    flex-column solver always, plus the Rust ``taffy`` engine when the ``layout-engine`` wheel is present
    (skipped otherwise, like ``test_layout_backend``). All three place row-stack geometry identically, so
    a per-backend sweep proves the display↔hit seam holds under each — the Rust path included."""
    from saitenka.render.layout_backend import (
        DefaultLayoutBackend,
        FlexColumnBackend,
        TaffyLayoutBackend,
    )

    backends: list[tuple[str, LayoutBackend]] = [
        ("default", DefaultLayoutBackend()),
        ("flex", FlexColumnBackend()),
    ]
    if importlib.util.find_spec("taffylite") is not None:
        backends.append(("taffy", TaffyLayoutBackend()))
    return backends


# Curated corners, NOT the full Cartesian product — each row targets a distinct interaction the
# post-PBT modes (and Phase-B layout work) added. ``Profile(Theme(), 384, "tall")`` reproduces the exact
# pre-existing assertions.
PROFILES: list[Profile] = [
    Profile(Theme(), 384, "tall"),  # the pre-existing baseline — keeps old coverage byte-identical
    Profile(Theme(scale=2.0), 640, "cjk_links"),  # hi-dpi crisp path + links + reference tip width
    Profile(
        Theme(scale=1.76), 640, "many_homograph"
    ),  # the real fractional bug scale, tallest wrap
    Profile(Theme(), 640, "short"),  # width change WITHOUT scale — isolates wrap-by-width
    Profile(
        Theme(scale=1.5), 512, "chip_heavy"
    ),  # chip/pill rows at hi-dpi — Phase-B chip-wrap shape
    Profile(Theme(), 384, "ruby_heavy"),  # dense inline furigana — Phase-B ruby-clearance shape
    Profile(Theme(scale=2.0), 384, "wide_cjk"),  # narrow + hi-dpi → the tallest kinsoku wrap
]


class RecordingRasterProvider:
    """A raster provider that records requests instead of rasterizing.

    Proves the provider-neutral contract: the reducer's plain/styled choice and the request it
    assembles are observable without Pillow, and a fake satisfies exactly what the shipping
    provider does.

    Pass ``delegate`` to record in front of a real provider instead of standing in for one — that is
    how the same neutrality assertions run against ``PillowRasterProvider``, which records nothing
    itself. Recording is the observation, not the substitution.
    """

    def __init__(self, size: tuple[int, int] = (20, 10), *, delegate=None) -> None:
        self.requests: list = []
        self.closed = False
        self._size = size
        self._delegate = delegate

    def render(self, request):
        from PIL import Image

        from saitenka.app.subtitle_raster import SubtitleRasterResult

        self.requests.append(request)
        if self._delegate is not None:
            return self._delegate.render(request)
        return SubtitleRasterResult(Image.new("RGBA", self._size), ())

    def close(self) -> None:
        self.closed = True
        if self._delegate is not None:
            self._delegate.close()

    @property
    def styles(self) -> list[str]:
        """The plain/styled decision behind each request, in order."""
        return [request.style.value for request in self.requests]


def record_spans(monkeypatch) -> list[dict]:
    """Capture every ``traced(...)`` span (name + static attrs + in-block ``.set`` attrs) without
    standing up an OTel provider — ``instrumented`` composes ``traced``, so this sees the real path."""
    spans: list[dict] = []

    @contextlib.contextmanager
    def _fake_traced(name, **attrs):
        rec = {"name": name, "attrs": dict(attrs)}
        spans.append(rec)

        class _Setter:
            def set(self, key, value):
                rec["attrs"][key] = value

        yield _Setter()

    monkeypatch.setattr(otel_metrics, "traced", _fake_traced)
    return spans


class ManualRenderAheadSubmitter:
    """Hold each render-ahead submission so a test fires its terminal when it chooses.

    Lives here because two files need it: a second copy is how a harness and the thing it stands in
    for drift, which this migration has now paid for five times.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return True

    def finish(self, *, outcome=EffectOutcome.SUCCEEDED, run=True):
        call = self.calls.pop(0)
        request = call["request"]
        result = (
            tooltip_raster.run_render_ahead(request, threading.Event())
            if run and outcome is EffectOutcome.SUCCEEDED
            else None
        )
        call["on_finished"](
            EffectFinished(
                EffectId(1),
                call["owner"],
                call["identity"],
                outcome,
                result=result,
                error=EffectError.INTERNAL if outcome is EffectOutcome.FAILED else None,
            )
        )


def requires_libass():
    """Skip unless a real libass runtime is loadable, and return the wrapper.

    `pytest.importorskip("libasslite")` answers the wrong question. The wrapper is pure Python and
    imports fine on a machine that has no libass at all — it `dlopen`s the library only when a
    renderer is built. So a CI runner with the wheel and no `libass.so` sailed past the guard and
    failed inside the first render instead of skipping, and the diagnosis pointed at the test.
    """
    import pytest

    libasslite = pytest.importorskip("libasslite")
    try:
        libasslite.library_version()
    except RuntimeError as error:  # no libass on this host
        pytest.skip(f"libass runtime unavailable: {error}")
    return libasslite


#: The family an ASS `Fontname` must name to select the pinned face — and it is the *style-qualified*
#: name because libass matches on nameID 1, which the bundled variable font sets to its default
#: instance (wght=100). `"Noto Sans JP"` is nameID 16 and selects nothing: measured, `NONE` +
#: that name renders zero layers, while this one renders two. Same trap the SVG rasterizer hit (#283).
PINNED_FAMILY = "Noto Sans JP Thin"


def pinned_face() -> tuple[str, bytes]:
    """The one face every libass measurement here is allowed to see.

    Text extents are a function of the font, so a renderer left to ask the host resolves a different
    face per platform and the same document measures differently on each — which is what
    `test_the_blur_refusal_is_measured_not_assumed`'s `Spacing: 10` was already working around, and
    what failed on the macOS runner anyway. The bundled Noto Sans JP ships in the wheel (OFL 1.1) and
    covers kana, JIS kanji and the punctuation/symbol range subtitles actually use.
    """
    from saitenka.resources import asset

    path = asset("fonts", "NotoSansJP.ttf")
    return (path.name, path.read_bytes())


def pinned_font_setup():
    """`FontSetup` confining a geometry backend to :func:`pinned_face`.

    `NONE` is the point: with any provider left on, libass falls back to a host face for a glyph the
    pinned one lacks, and the platform dependence comes straight back for exactly the documents that
    would otherwise expose it.
    """
    from saitenka.subtitles import FontProvider, FontSetup

    return FontSetup(default_family=PINNED_FAMILY, font_provider=FontProvider.NONE)


def pinned_ass_renderer(libasslite, ass: bytes, **kwargs):
    """An `AssRenderer` measuring against :func:`pinned_face` alone, whatever the host has installed."""
    return libasslite.AssRenderer(
        ass,
        [pinned_face()],
        font_provider=libasslite.FontProvider.NONE,
        default_family=PINNED_FAMILY,
        **kwargs,
    )
