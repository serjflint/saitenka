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
import os
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pytest
from PIL import Image

from saitenka.model import Theme
from saitenka.mpvio.gateway import MpvGateway
from saitenka.mpvio.ipc import IPCRequest
from saitenka.panel import Definition, Entry, Freq, panel_rows, render_panel
from saitenka.runtime.mailbox import SessionMailbox

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.render.layout_backend import LayoutBackend

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("SAITENKA_UPDATE_GOLDEN") == "1"


def runtime_gateway(ipc) -> MpvGateway:
    return MpvGateway(ipc, SessionMailbox())


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
        self.props: dict = {}
        self.commands: list[tuple] = []
        self.requests: list[IPCRequest] = []
        self._event_sink = None
        self._connection_sink = None
        self._legacy_event_source = None
        self._runtime_gateway = None
        self.runtime_outcomes: list[object] = []
        #: Named timers scheduled through the runtime port, newest per name. Nothing fires on a
        #: wall clock — a test calls `fire_runtime_timer` so ordering stays deterministic.
        self.timers: dict[str, tuple[object, Callable[[object], None]]] = {}

    def schedule_runtime_timer(self, *, timer: str, identity, on_finished, **_kwargs) -> bool:
        self.timers[timer] = (identity, on_finished)
        return True

    def cancel_runtime_timer(self, timer: str) -> bool:
        return self.timers.pop(timer, None) is not None

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
        if self._event_sink is None:
            self.events.append(event)
        else:
            self._event_sink(event, 0)

    def pump(self) -> None:
        """Real IPC reads the socket here; the fake's events are queued directly."""

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}

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

    def drain_events(
        self, timeout: float | None = 0.0, *, ordered_terminals: bool = False
    ) -> list[dict]:
        if self._legacy_event_source is not None:
            return self._legacy_event_source(timeout, ordered_terminals=ordered_terminals)
        evs, self.events = self.events, []
        return evs

    def install_runtime_ingress(self, event_sink, connection_sink, legacy_event_source, gateway):
        self._event_sink = event_sink
        self._connection_sink = connection_sink
        self._legacy_event_source = legacy_event_source
        self._runtime_gateway = gateway
        for event in self.events:
            event_sink(event, 0)
        self.events = []

    def dispatch_runtime_terminal(self, completion) -> None:
        if self._runtime_gateway is not None:
            self._runtime_gateway.dispatch_terminal(completion)

    def submit_runtime_mpv(self, **kwargs) -> bool:
        if self._runtime_gateway is None:
            return False
        return self._runtime_gateway.submit_mpv(**kwargs)

    def publish_legacy_command_outcome(self, outcome) -> None:
        if self._runtime_gateway is None:
            self.runtime_outcomes.append(outcome)
        else:
            self._runtime_gateway.publish_legacy_outcome(outcome)

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


def keybind_registry(ipc: FakeIPC) -> dict[str, str]:
    """The ``{key: message}`` map mpv would hold after registration, reconstructed from the recorded
    ``keybind`` commands. Honours later-binds-over-earlier and the ``keybind KEY ignore`` unbinds that
    surface teardown emits (a key bound then neutralised drops out). FakeIPC only *records* the bind
    string — it can't fire the handler — so this is the seam :func:`press` dispatches through."""
    reg: dict[str, str] = {}
    for cmd in ipc.commands:
        if len(cmd) >= 3 and cmd[0] == "keybind":
            key, spec = cmd[1], cmd[2]
            if isinstance(spec, str) and spec.startswith("script-message "):
                reg[key] = spec.removeprefix("script-message ")
            else:  # "ignore" (or any non-script-message) neutralises the key
                reg.pop(key, None)
    return reg


def press(reader, ipc: FakeIPC, key: str) -> None:
    """Fire the handler bound to ``key`` through the REAL dispatch chain — a synthetic mpv
    ``client-message`` drained by ``reader._drain_events()`` → ``_handle`` → ``_HANDLERS`` — the way an
    actual keypress does. This is the hop FakeIPC can't simulate on its own (it echoes the bind, never
    fires it), so a test that only checks ``ipc.commands`` proves saitenka *sent* the bind, not that a
    press *runs* the action. Raises :class:`KeyError` if ``key`` isn't currently bound — a dead shortcut
    is exactly the bug this catches (attach-mode mine keys, #244)."""
    reg = keybind_registry(ipc)
    if key not in reg:
        raise KeyError(f"{key!r} is not bound (registered: {sorted(reg)})")
    ipc.events.append({"event": "client-message", "args": [reg[key]]})
    reader._drain_events()


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
    """

    def __init__(self, size: tuple[int, int] = (20, 10)) -> None:
        self.requests: list = []
        self._size = size

    def render(self, request):
        from PIL import Image

        from saitenka.app.subtitle_raster import SubtitleRasterResult

        self.requests.append(request)
        return SubtitleRasterResult(Image.new("RGBA", self._size), ())

    @property
    def styles(self) -> list[str]:
        """The plain/styled decision behind each request, in order."""
        return [request.style.value for request in self.requests]
