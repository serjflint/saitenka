"""The subtitle raster, behind an injectable strategy so tests can run the reader loop headless.

``SessionController.renderer`` holds a :class:`SubtitleRenderer` (the real blit); pass :class:`NullRenderer` to
suppress the raster and assert state only — the public seam that replaces monkeypatching the private
``draw_subtitle`` (#50). The strategy takes the ``SessionController`` as its host, matching the collaborator
pattern the other app modules use.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from saitenka_subtitles import decoration, overpaint, whole_cue

from saitenka import otel_metrics
from saitenka.app import subtitle_raster
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.prepared_osd import OsdInputs, PreparedOsdCache
from saitenka.app.subtitle_geometry_diagnostics import cue_digest
from saitenka.app.subtitle_ownership import (
    ASK_MPV,
    ActionKind,
    AskMpv,
    EventKind,
    OwnershipAction,
    OwnershipContext,
    OwnershipEvent,
    OwnershipMode,
    OwnershipState,
    PixelOwner,
    SelectedSid,
    Visibility,
    reduce_ownership,
)
from saitenka.app.subtitles import box_for_token
from saitenka.runtime import EffectError, EffectFinished, EffectOutcome, Owner
from saitenka.runtime.surfaces import (
    SurfaceAction,
    SurfaceRuntime,
    SurfaceStatus,
    SurfaceTransactionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka_tokenize.japanese import Token

    from saitenka.app.color_accounting import ColorWrite
    from saitenka.app.color_telemetry import ColorTelemetry
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.subtitles import WordBox
    from saitenka.app.timed_osd import TimedOsd
    from saitenka.runtime.surfaces import SurfaceTransaction

log = logging.getLogger("saitenka.app.subtitle_render")

SUB_ID = OverlayId.SUB
OWNERSHIP_RETRY_TIMER = "subtitle:ownership-retry"


@dataclass(frozen=True, slots=True)
class OwnershipRetryDue:
    """Identity of one ownership retry deadline; the effect id fences a late due."""

    effect_id: int


NATIVE_FOCUS_ID = 1_001
#: The hidden slot the layout calibration lays a payload out on. Nothing is ever drawn there —
#: `compute_bounds` answers with the box and leaves the surface untouched.
_FOCUS_SLOT = "subtitle-native-focus"
_VISIBILITY_ASSERT = "ownership:assert-native-visibility"
_VISIBILITY_READBACK = "ownership:readback-visibility"


def _color_is_up(cue: str | None, *, landed: bool) -> None:
    """Close *cue*'s wait for color. `None` or a write that never landed closes nothing — a wait
    left open is a cue still owed color, which is exactly what the counter is there to show."""
    if cue is not None and landed:
        otel_metrics.record_color_up(cue)


def _send_visibility(ipc, identity: str, *, visible: bool, on_outcome=None) -> None:
    """One correlated `sub-visibility` write. Not awaited: mpv has a single ordered outbound
    channel, so a later read still observes it — what the correlation buys is a terminal outcome
    instead of a discarded reply."""

    def finished(completion: EffectFinished) -> None:
        applied = completion.outcome is EffectOutcome.SUCCEEDED
        if not applied:
            log.warning(
                "subtitle visibility write %s did not apply: %s", identity, completion.outcome
            )
        if on_outcome is not None:
            on_outcome(applied)

    if not ipc.submit_runtime_mpv(
        owner=Owner.SUBTITLE,
        identity=identity,
        command=("set_property", "sub-visibility", visible),
        timeout_s=10.0,
        on_finished=finished,
    ):
        log.warning("subtitle visibility write %s was not admitted", identity)
        if on_outcome is not None:
            on_outcome(False)  # noqa: FBT003  # the applied flag is the whole payload


@dataclass(frozen=True, slots=True)
class DrawRequest:
    """Everything the legacy draw path needs about the cue — the seam that stops it reading a host.

    Built once per draw by :func:`build_draw_request`, so the values it used to pull off the SessionController
    one at a time arrive together and cannot drift apart mid-render. Frozen: the raster can run off
    the main thread, and a request that changed under it would raster one cue's text with another's
    styles.
    """

    text: str
    lines: Sequence[Sequence[Token]]
    osd: tuple[int, int]
    sub_size: int
    bg_opacity: int
    bottom_margin: int
    secondary_role: bool
    upgrade_pending: bool
    annotation_degraded: bool
    annotation_visible: bool
    hover: int
    hover_span: tuple[int, int] | None
    styles: list | None
    #: The CURRENT cue's hit boxes. The legacy path produces them and the native focus path reads
    #: them, so they travel in the request rather than being re-read off a host mid-draw.
    boxes: list[WordBox] = field(default_factory=list)
    #: Tokens the geometry owed a box for this cue, or `None` on the legacy path, which has no
    #: geometry to owe anything. Carried so a draw is self-describing: `measured_boxes=0` reads the
    #: same for a line whose geometry has not landed and for a music marker that owes nothing.
    owed_color: int | None = None
    paused: bool = False
    #: Geometry eligibility does not authorize painting over authored subtitle effects.
    paint_allowed: bool = True
    #: Independent shadow placement when native logical regions own interaction.
    paint_boxes: list[WordBox] | None = None
    color_telemetry: ColorTelemetry | None = None
    color_occurrence: int | None = None
    color_token_indices: frozenset[int] | None = None
    paint_reason: str = "unknown"
    whole_cue: whole_cue.WholeCue | None = None
    whole_cue_origin: tuple[int, int] = (0, 0)
    coloring: str = "whole-cue-auto"
    osd_shaper: str = "unknown"
    record_whole_cue: Callable[[dict], None] | None = None
    whole_cue_identity: tuple[str, float | None, int] | None = None


@dataclass(frozen=True, slots=True)
class DrawResult:
    """What a draw produced: the hit boxes, where it landed, and its surface transaction."""

    boxes: list[WordBox]
    origin: tuple[int, int]
    transaction: SurfaceTransaction | None


FOCUS_PAD = 3


def place_subtitle(
    size: tuple[int, int], osd: tuple[int, int], bottom_margin: int
) -> tuple[int, int]:
    """Top-left for a rendered cue: centred horizontally, sitting ``bottom_margin`` above the bottom.

    Not clamped. A cue wider than the video is a raster-side bug (the raster wraps to the OSD width),
    and clamping here would hide it by silently shifting the line instead.
    """
    return (osd[0] - size[0]) // 2, osd[1] - size[1] - bottom_margin


def focus_rect(boxes, hover: int, span: tuple[int, int] | None) -> tuple[int, int, int, int] | None:
    """The padded rectangle to highlight under the hovered word, or None when there is nothing to
    highlight.

    ``span`` covers a multi-token dictionary term (コンサート over the over-split コン), so the
    highlight is the UNION of its boxes — highlighting only `hover` would underline half a word the
    tooltip is showing whole. A span whose boxes are absent yields None rather than an empty union:
    the cue was re-rendered under the hover and the old indices no longer address anything.
    """
    if hover < 0 or box_for_token(boxes, hover) is None:
        return None
    lo, hi = span or (hover, hover + 1)
    selected = [box for box in boxes if lo <= box.index < hi]
    if not selected:
        return None
    left = min(box.x for box in selected)
    top = min(box.y for box in selected)
    right = max(box.x + box.w for box in selected)
    bottom = max(box.y + box.h for box in selected)
    return left, top, right - left + 2 * FOCUS_PAD, bottom - top + 2 * FOCUS_PAD


def _record_color_demand(request: DrawRequest, span: otel_metrics.SpanSetter) -> None:
    if request.color_telemetry is None or request.color_occurrence is None:
        return
    span.set("color_session", request.color_telemetry.session)
    span.set("occurrence", request.color_occurrence)
    if request.styles is None or request.color_token_indices is None:
        return
    demand = frozenset(
        index for index in request.color_token_indices if _token_color(request, index) is not None
    )
    device, reason = whole_cue_device(request)
    permitted = (
        demand if device != "none" else None if reason == "pending-whole-cue" else frozenset()
    )
    request.color_telemetry.qualify(request.color_occurrence, demand, permitted)


def _color_submission(
    request: DrawRequest | None,
    device: str,
    *,
    clear: bool = False,
) -> tuple[ColorTelemetry, ColorWrite | None] | None:
    if request is None or request.color_telemetry is None or request.color_occurrence is None:
        return None
    tokens: frozenset[int] = frozenset()
    tracker = request.color_telemetry
    if not clear:
        selected, _reason = whole_cue_device(request)
        tokens = (
            frozenset(index for index, _rgb in whole_cue_colors(request))
            if selected == device
            else frozenset()
        )
    return tracker, tracker.submit(request.color_occurrence, device, tokens)


def whole_cue_colors(request: DrawRequest) -> tuple[tuple[int, int], ...]:
    if not request.paint_allowed:
        return ()
    indices = request.color_token_indices
    if indices is None:
        indices = frozenset(box.index for box in request.boxes)
    return tuple(
        (index, color)
        for index in sorted(indices)
        if (color := _token_color(request, index)) is not None
    )


def whole_cue_device(request: DrawRequest) -> tuple[str, str]:
    cue = request.whole_cue
    if (
        request.coloring != "boxes-only"
        and cue is None
        and request.paint_reason
        in {
            "missing",
            "missing-coherent-evidence",
            "no-scan-regions",
        }
    ):
        return "none", "pending-whole-cue"
    if request.coloring == "boxes-only" or not request.paint_allowed:
        return "none", "boxes-only" if request.coloring == "boxes-only" else request.paint_reason
    if cue is None:
        return "none", "pending-whole-cue"
    reason = cue.osd_reason if request.osd_shaper == "complex" else "osd-shaper"
    if request.coloring != "whole-cue-overpaint" and reason == "eligible" and cue.events:
        return "overprint", "eligible"
    if request.coloring != "whole-cue-osd" and cue.layers:
        if not whole_cue.raster_fits(cue):
            return "none", "composite-budget"
        return "overpaint", reason
    return "none", reason if reason != "eligible" else "raster-evicted"


def _record_whole_cue_decision(request: DrawRequest) -> None:
    device, reason = whole_cue_device(request)
    cue = request.whole_cue
    if request.record_whole_cue is None:
        return
    identity = request.whole_cue_identity
    request.record_whole_cue(
        {
            "event": "decision",
            "requested": request.coloring,
            "device": device,
            "reason": reason,
            "blockers": tuple(cue.blockers if cue else ())
            + ((reason,) if reason != "eligible" else ())
            + (("osd-shaper",) if request.osd_shaper != "complex" else ()),
            "text_hash": identity[0] if identity else None,
            "cue_start_ms": identity[1] if identity else None,
            "generation": identity[2] if identity else None,
            "occurrence": request.color_occurrence,
            "color_session": request.color_telemetry.session if request.color_telemetry else None,
            "scan_available": bool(request.boxes),
            "frame_size": request.osd,
            "osd_resolution": cue.resolution if cue else (0, 0),
            **(dict(cue.evidence) if cue else {}),
        }
    )


def _focus_in_space(rect, source: tuple[int, int], target: tuple[int, int]):
    if rect is None or source == target:
        return rect
    sx, sy = target[0] / source[0], target[1] / source[1]
    return round(rect[0] * sx), round(rect[1] * sy), round(rect[2] * sx), round(rect[3] * sy)


def _color_settled(
    submission: tuple[ColorTelemetry, ColorWrite | None] | None, *, accepted: bool
) -> None:
    if submission is not None:
        tracker, write = submission
        tracker.settle(write, accepted=accepted)


def _token_color(request: DrawRequest, index: int) -> int | None:
    """The reading-state color for one token, as 24-bit RGB, or `None` when it has none."""
    return _token_rgb(request, index, "color")


def _token_underline(request: DrawRequest, index: int) -> int | None:
    """The JLPT level underline for one token, or `None` when it has no level.

    Separate from the reading state and additive to it (`app/scoring.py`): a word can be both due
    for review and N3. The standard renderer has always drawn both; reading only the color here is
    what made the level invisible whenever this mode was on.
    """
    return _token_rgb(request, index, "underline")


def _token_rgb(request: DrawRequest, index: int, field: str) -> int | None:
    styles = request.styles
    if not styles or not 0 <= index < len(styles):
        return None
    rgba = getattr(styles[index], field, None)
    if rgba is None:
        return None
    return (rgba[0] << 16) | (rgba[1] << 8) | rgba[2]


def _trace_overpaint_placement(request: DrawRequest, image: overpaint.Overpaint) -> None:
    """Where device 2's raster lands, beside the two spaces it has to reconcile.

    The masks are measured against the geometry frame and the slot is addressed in mpv's OSD space.
    Those differ by the letterbox whenever the video does not fill the window, and nothing downstream
    can tell a correct placement from one that skipped the conversion — the raster simply appears
    somewhere. So the placement, the boxes it came from, and both spaces are recorded together.
    """
    height, width = image.rgba.shape[0], image.rgba.shape[1]
    with otel_metrics.traced("subtitle_overpaint_placement") as span:
        span.set("x", image.x)
        span.set("y", image.y)
        span.set("width", width)
        span.set("height", height)
        span.set("osd_width", request.osd[0])
        span.set("osd_height", request.osd[1])
        span.set("token_count", len(request.boxes))
        if request.boxes:
            span.set("boxes_left", min(box.x for box in request.boxes))
            span.set("boxes_top", min(box.y for box in request.boxes))
            span.set("boxes_right", max(box.x + box.w for box in request.boxes))
            span.set("boxes_bottom", max(box.y + box.h for box in request.boxes))
            span.set("box_height_max", max(box.h for box in request.boxes))
    log.debug(
        "overpaint placed: %dx%d at (%d,%d) from %d box(es) spanning y %s..%s; osd %dx%d",
        width,
        height,
        image.x,
        image.y,
        len(request.boxes),
        min((box.y for box in request.boxes), default="-"),
        max((box.y + box.h for box in request.boxes), default="-"),
        request.osd[0],
        request.osd[1],
    )


def focus_drawing(rect: tuple[int, int, int, int]) -> str:
    """``rect`` as an ASS vector drawing — the translucent highlight mpv paints natively."""
    left, top, width, height = rect
    return (
        rf"{{\an7\pos({left - FOCUS_PAD},{top - FOCUS_PAD})\bord2\shad0"
        rf"\1c&H5AD6FF&\1a&HDF&\3c&H5AD6FF&\3a&H23&\p1}}"
        f"m 0 0 l {width} 0 l {width} {height} l 0 {height}"
    )


def _record_osd_warmup(record, completion, started: float, *, accepted: bool) -> None:
    bounds = completion.result if completion is not None else None
    values: list[float] = []
    if isinstance(bounds, dict):
        for key in ("x0", "y0", "x1", "y1"):
            value = bounds.get(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                break
            values.append(float(value))
    rendered = bool(
        accepted
        and completion is not None
        and completion.outcome is EffectOutcome.SUCCEEDED
        and len(values) == 4
        and values[2] > values[0]
        and values[3] > values[1]
    )
    evidence: dict = {
        "event": "subtitle_osd_warmup",
        "validation_scope": "ass-render-completion-not-display",
        "color_status": "complete" if rendered else "unknown",
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "accepted": accepted,
    }
    if rendered:
        evidence["osd_bounds"] = values
    record(evidence)
    with otel_metrics.traced("subtitle_osd_warmup") as span:
        for key, value in evidence.items():
            span.set(key, value)


def _trace_focus_write(
    transaction: SurfaceTransaction,
    completion: EffectFinished | None,
    tail: tuple[object, ...],
    started: float,
    *,
    accepted: bool,
    colored: bool,
    warm_record: Callable[[dict], None] | None = None,
) -> None:
    if warm_record is not None:
        _record_osd_warmup(warm_record, completion, started, accepted=accepted)
    with otel_metrics.traced("surface_write") as span:
        span.set("round_trip_ms", round((time.perf_counter() - started) * 1000.0, 3))
        span.set("route", "correlated")
        span.set("command", "osd-overlay")
        span.set("slot", _FOCUS_SLOT)
        span.set("surface_revision", transaction.revision)
        if completion is not None:
            span.set("effect_id", completion.effect_id.value)
        span.set("admitted", completion is not None)
        span.set("accepted", accepted)
        span.set("device", "overprint" if colored else "focus")
        span.set(
            "validation_scope",
            "ass-render-completion-not-display"
            if tail[-2:] == (True, True)
            else "upload-acknowledgment-not-pixels",
        )
        span.set("outcome", completion.outcome.value if completion is not None else "not-admitted")
        payload = next((part for part in tail[2:3] if isinstance(part, str)), "")
        span.set("events", len(payload.splitlines()) if payload else 0)


class NoPixelOwnership:
    """For a renderer that owns no mpv-side pixels: the ownership members are no-ops, on purpose.

    Inherit it to mean it. A renderer that simply lacks the method means the same thing to the
    reader and nothing at all to a type checker.
    """

    def cue_changed(self, _target: SubtitleTarget, /, *, nonempty: bool) -> None: ...

    def connection_replaced(self, _target: SubtitleTarget, /) -> None: ...

    def degrade_geometry(self, _target: SubtitleTarget, /) -> None: ...

    def use_native(self, _target: SubtitleTarget, /) -> bool:
        return True  # nothing to prove, so geometry is never withheld on ownership grounds


class SubtitleRenderer(NoPixelOwnership):
    """Rasterize the current cue and blit it as the SUB overlay — the real draw path."""

    def activate(self, target: SubtitleTarget, _sid: SelectedSid = ASK_MPV) -> bool:
        """Ask mpv to stop drawing its own subtitles, unless the overlay is hidden and they are mpv's.

        `True` unconditionally: a refused write is reported on its own terminal, and `False` would
        ask the caller for a fallback draw — the legacy render already is the fallback, and while
        hidden nothing may be drawn at all.
        """
        if self._suspended:
            return True
        if not hasattr(self, "_restore_visibility"):
            self._restore_visibility = target.get("sub-visibility")
        _send_visibility(target.ipc, "subtitle:hide-for-legacy-render", visible=False)
        return True

    def deactivate(self, target: SubtitleTarget) -> None:
        restore = getattr(self, "_restore_visibility", None)
        if restore is not None:
            _send_visibility(target.ipc, "subtitle:restore-visibility", visible=bool(restore))

    def suspend_for_overlay(self, target: SubtitleTarget) -> None:
        self._suspended = True
        # Removed, not just hidden: showing the overlay re-issues every retained surface, and this
        # one would bring back the cue from before the hide.
        self.retire(target.surfaces)
        _send_visibility(target.ipc, "subtitle:suspend-for-overlay", visible=True)

    def resume_after_overlay(self, _target: SubtitleTarget) -> bool:
        """Owes the caller a redraw: the cue on screen now has not been drawn, and the redraw's own
        `activate` takes the pixels back from mpv."""
        self._suspended = False
        return True

    def __init__(self, provider: subtitle_raster.SubtitleRasterPort | None = None) -> None:
        self.provider: subtitle_raster.SubtitleRasterPort = (
            provider or subtitle_raster.PillowRasterProvider()
        )
        self._closed = False
        self._logged_first = False
        self._suspended = False

    def close(self) -> None:
        """Quarantine the surface and release the provider. A cue that arrives after this — a late
        annotation publishing its upgrade — must not stage pixels onto a slot the close path has
        already emptied."""
        self._closed = True
        self.provider.close()

    def render(
        self,
        request: DrawRequest,
        surfaces,
        *,
        on_settled: Callable[[bool], None] | None = None,
    ) -> DrawResult | None:
        """Raster ``request`` and present it. Returns where it landed; the caller owns the write-back.

        Returning rather than assigning is what makes the geometry a value: the hit boxes and the
        origin belong to the cue that produced them, so a stale cue's boxes cannot outlive it by
        having been written onto a host mid-render.
        """
        if self._closed:
            if on_settled is not None:
                on_settled(False)  # noqa: FBT003  # the settlement flag is the whole payload
            return None
        # Plain covers the secondary track and any cue still awaiting (or denied) its annotation:
        # the cue shows at cue time and reader_deps re-renders it annotated once deps land.
        raster = subtitle_raster.build_request(
            subtitle_raster.raster_style(
                secondary_role=request.secondary_role,
                upgrade_pending=request.upgrade_pending,
                annotation_degraded=request.annotation_degraded,
            ),
            subtitle_raster.RasterContent(
                request.text,
                request.lines,
                request.osd[0],
                request.sub_size,
                # configurable box alpha (0 = fully transparent)
                (0, 0, 0, request.bg_opacity),
            ),
            subtitle_raster.AnnotationOverlay(
                request.annotation_visible,
                request.hover,
                request.hover_span,
                request.styles,
            ),
        )
        with otel_metrics.instrumented(otel_metrics.subtitle_render_duration_ms, "subtitle_render"):
            sr = self.provider.render(raster)
        ox, oy = place_subtitle(
            (sr.image.width, sr.image.height), request.osd, request.bottom_margin
        )
        if not self._logged_first:
            self._logged_first = True
            log.info(
                "first subtitle drawn (%dx%d at %d,%d)", sr.image.width, sr.image.height, ox, oy
            )
        # Revision-fenced: a newer cue's transaction supersedes this one's acknowledgement rather
        # than racing it onto the same mpv slot. `on_settled` is how staging learns pixels exist.
        return DrawResult(
            list(sr.boxes),
            (ox, oy),
            surfaces.present(
                sr.image, ox, oy, oid=SUB_ID, owner=Owner.SUBTITLE, on_settled=on_settled
            ),
        )

    def draw(
        self,
        request: DrawRequest,
        surfaces=None,
        _ipc=None,
        /,
        *,
        on_settled: Callable[[bool], None] | None = None,
    ) -> DrawResult | None:
        """Render the cue. No host: the request IS the snapshot, and the result IS the geometry.

        `_ipc` is unused here and present because the protocol has one member per renderer, not one
        per renderer's needs — the native focus path writes to mpv directly.
        """
        if self._suspended:
            if on_settled is not None:
                on_settled(False)  # noqa: FBT003  # the settlement flag is the whole payload
            return None
        return self.render(request, surfaces, on_settled=on_settled)

    @property
    def logged_first(self) -> bool:
        """Whether a first-subtitle line has already been logged, for the caller to carry back."""
        return self._logged_first

    def clear(self, surfaces=None, _ipc=None, /) -> None:
        self.retire(surfaces)

    def retire(self, surfaces) -> None:
        """Take the cue's pixels down. Separate from `clear` so the ownership FSM, which has the
        surface layer but no host, can retire legacy pixels without one."""
        surfaces.remove(SUB_ID, owner=Owner.SUBTITLE)


class SubtitleEgress(Protocol):
    """The transport calls a renderer makes.

    Declared rather than `object`, for the reason `RuntimeJobPort` is: a stand-in that has no timer
    port must *refuse* one, and a `getattr` probe cannot tell that apart from a renamed method.
    """

    def query(self, name: str) -> object | None: ...

    def submit_runtime_mpv(self, **kwargs: object) -> bool: ...

    def schedule_runtime_timer(self, **kwargs: object) -> bool: ...

    def cancel_runtime_timer(self, timer: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class SubtitleTarget:
    """What a subtitle renderer acts on, once it stops acting on the host.

    Measured, not assembled: outside `build_draw_request` — which has its own seam because the soft
    path reads fifteen presentation facts — every renderer in the module reaches exactly these.
    Two contracts, mpv-read and presentation, plus the two things the geometry owner is asked for.

    `refresh`, `source` and `native_unsupported` rather than the geometry object, because handing
    the object over would also hand over `refresh(reader)`, which takes the host: the member would
    smuggle back in what the cut removes. `refresh` is a no-op when there is no geometry owner,
    which is the same guard the call site used to spell inline.
    """

    ipc: SubtitleEgress
    get: Callable[[str], object]
    prop: Callable[[str], object]
    surfaces: LifecycleSurfaces
    refresh: Callable[[], None]
    draw_request: Callable[[], DrawRequest]
    source: object = None
    #: This source can never produce geometry (see `NativeSubtitleGeometry.source_unsupported`).
    #: A snapshot, not a callable: it is read inside the same call that builds the target.
    native_unsupported: bool = False
    #: The user asked for the legacy renderer. Separate from `native_unsupported` because the two
    #: are different facts with the same consequence, and a report has to tell them apart: one is a
    #: choice and the other is a track the native path cannot serve.
    legacy_forced: bool = False


class NullRenderer(NoPixelOwnership):
    """No-op draw strategy: run the reader's hover/nav/prefetch logic without rasterizing."""

    def draw(self, _request: DrawRequest, _surfaces=None, _ipc=None, /, **_ports) -> None:
        """Nothing is rastered, so nothing settles — the caller's `on_settled` never fires.

        `**_ports` rather than naming `on_settled`: it would be an unused argument the lint flags,
        and swallowing the protocol's keyword ports is exactly what a no-op renderer does.
        """
        return

    def clear(self, _surfaces=None, _ipc=None, /) -> None:
        pass

    def close(self) -> None:
        pass

    # --- nothing is drawn, so nothing is owned ------------------------------------------------
    # `activate` returning True is what keeps the coordinator from falling back to a draw this
    # renderer would discard anyway.

    @property
    def logged_first(self) -> bool:
        return False

    def activate(self, _target: SubtitleTarget, _sid: SelectedSid = ASK_MPV, /) -> bool:
        return True

    def deactivate(self, _target: SubtitleTarget, /) -> None: ...

    def suspend_for_overlay(self, _target: SubtitleTarget, /) -> None: ...

    def resume_after_overlay(self, _target: SubtitleTarget, /) -> bool:
        return False


class NativeVisibleRenderer:
    """Keep mpv pixels visible while geometry independently supplies interaction boxes."""

    def __init__(
        self,
        fallback: SubtitleRenderer | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        coloring: str = "whole-cue-auto",
    ) -> None:
        self.coloring = coloring
        self._prepared_osd = PreparedOsdCache()
        self._whole_image_key: tuple | None = None
        self._whole_image: overpaint.Overpaint | None = None
        self._whole_image_cue: whole_cue.WholeCue | None = None
        self._fallback = fallback or SubtitleRenderer()
        self._state = OwnershipState()
        self._native_ready = False  # compatibility diagnostic: pixel admission, not box readiness
        self._visibility: bool | None = None
        self._activation_failure_reported = False
        self._restore_visibility: bool | None = None
        self._selection: str | None = None
        self._retry_effect_id: int | None = None
        self._retry_immediate: int | None = None
        self._clock = clock
        # The focus highlight is its own presentation slot: a hide issued while a show is still in
        # flight must not land after it, and the revision fence is what orders them.
        self._focus = SurfaceRuntime()
        #: Whether device 2's raster is on screen, so a cue that needs none takes the last one down
        #: rather than leaving it over the words that follow.
        self._overpaint_shown = False
        #: Digest of the last cue whose draw actually carried boxes — see `lost_color`.
        self._painted_cue: str | None = None
        #: The payload currently live on the focus slot, so an identical rewrite can be skipped.
        #: `None` means "assume nothing is up" — the safe direction, since a needless write costs
        #: milliseconds and a skipped necessary one costs the color.
        self._focus_payload: tuple[object, ...] | None = None
        #: Whether anything is on the focus slot at all. Starts `True`: an earlier session (attach
        #: mode reconnects to a running mpv) may have left a payload up, and one removal is cheap.
        self._focus_up = True
        #: A cue change owes the slot a removal, but only if the draw that follows in the same
        #: turn does not replace the payload. Removing first and presenting second was two writes
        #: mpv parsed in sequence, and a frame composited between them showed the cue white.
        self._focus_clear_pending = False
        #: Set by `cue_changed` and cleared by the cue's first payload, its flush, or a clear: while
        #: it holds, a removal — a draw with nothing to show, an ownership clear — is deferred to
        #: the flush instead of paid, so a payload arriving later in the same turn replaces it.
        self._defer_focus_clear = False
        #: Digest of the cue whose payload is on the focus slot, so a cue turn can tell "the
        #: previous line's color" from "this line's own, pre-armed by a seek".
        self._focus_cue: str | None = None
        #: Whether a saitenka overlay window (or `Alt+o`) has taken the session's pixels down. The
        #: focus slot is not a `LifecycleSurfaces` one, so nothing else stops a redraw repainting it.
        self._suspended = False
        #: Placement and pixels of the raster currently on the slot, so a redraw that changes
        #: neither can skip the upload. `None` whenever the slot is down — see `_drop_overpaint`.
        self._overpaint_published: tuple[int, int, bytes] | None = None
        self._overpaint_operation: otel_metrics.DeferredSpan | None = None
        self._color_request: DrawRequest | None = None
        self.timed: TimedOsd | None = None
        self._overpaint_acknowledged = False
        self._overpaint_color_pending: tuple[ColorTelemetry, ColorWrite | None] | None = None
        self._focus_color_pending: tuple[ColorTelemetry, ColorWrite | None] | None = None

    @property
    def ownership_state(self) -> OwnershipState:
        return self._state

    @property
    def assertion_in_flight(self) -> bool:
        """A correlated visibility assertion is awaiting its terminal, so ownership is undecided
        rather than refused."""
        return self._state.active_effect_kind == ActionKind.ASSERT_NATIVE_VISIBILITY

    @staticmethod
    def _reply_accepted(reply: object) -> bool:
        return not isinstance(reply, dict) or reply.get("error") in {None, "success"}

    @staticmethod
    def _visibility_of(value: object) -> Visibility:
        """Decode one mpv `sub-visibility` value. Anything that is not an explicit bool is UNKNOWN —
        never legacy proof."""
        if value is True:
            return Visibility.TRUE
        if value is False:
            return Visibility.FALSE
        return Visibility.UNKNOWN

    def _read_visibility(self, ipc) -> Visibility:
        # The blanket stays here rather than moving into `query`: "anything I could not read is
        # UNKNOWN, never legacy proof" is this machine's invariant, and hanging it on the
        # transport's catch list would make a stand-in that raises something else hand the pixels
        # away. `query` already discards a payload that arrived beside an error.
        try:
            value = ipc.query("sub-visibility")
        except Exception:  # noqa: BLE001  # an unreadable boundary is unknown, never legacy proof
            return Visibility.UNKNOWN
        return self._visibility_of(value)

    def _trace_ownership(
        self,
        event: str,
        *,
        owner_before: PixelOwner,
        accepted: bool | None = None,
        visibility: Visibility | None = None,
        effect_id: int | None = None,
        deferred: bool | None = None,
    ) -> None:
        with otel_metrics.traced("subtitle_pixel_ownership") as span:
            span.set("event", event)
            span.set("mode", self._state.context.mode)
            span.set("owner_before", owner_before)
            span.set("owner_after", self._state.owner)
            span.set("visibility", visibility or self._state.visibility)
            span.set("connection_epoch", self._state.context.connection_epoch)
            span.set("ownership_epoch", self._state.context.ownership_epoch)
            span.set("selection_present", self._state.context.selection is not None)
            span.set("retry_attempts", self._state.retry_attempts_used)
            span.set("retry_exhausted", self._state.retry_exhausted)
            if accepted is not None:
                span.set("accepted", accepted)
            if effect_id is not None:
                span.set("effect_id", effect_id)
            if deferred is not None:
                # Whether the answer arrived after its caller returned — the window in which
                # consumers were told "not yet" and a re-drive is owed.
                span.set("deferred", deferred)

    def _assert_native(self, target: SubtitleTarget, action: OwnershipAction) -> None:
        """Assert native visibility, then read back what mpv actually holds.

        Two correlated hops when the gateway admits them, the synchronous trio otherwise. The
        readback is not redundant with the write's outcome — mpv can accept the set and still
        report FALSE, which is the case that hands ownership to legacy.

        Written as closures rather than helper methods because each closes over `action`,
        `owner_before` and `deferred` — the assertion's in-flight state, which is what makes them
        callable from a terminal that arrives later.
        """
        owner_before = self._state.owner
        exhausted_before = self._state.retry_exhausted
        # Must precede the write, and stays synchronous: it is the sole source of the value close
        # replays, so issued concurrently or after it reads back our own `true` and close then
        # restores the wrong visibility to the user's mpv. A sync read is queued ahead of a later
        # async write on the same ordered outbound channel, so ordering holds.
        self._capture_restore_visibility(target.ipc)
        # True once this call has handed a "not yet" back to its caller. Only then does a settle
        # owe a re-drive; a result that lands before the return (no gateway, or a fake completing
        # inline) is still the caller's own answer, and refreshing there would arm a geometry
        # deadline from inside set_subtitle that the coalescing contract forbids.
        deferred = False

        def settle(*, accepted: bool, visibility: Visibility, reply: object) -> None:
            followups = self._apply_assertion_result(action, visibility)
            self._record_assertion_result(
                action,
                reply=reply,
                accepted=accepted,
                visibility=visibility,
                owner_before=owner_before,
                exhausted_before=exhausted_before,
                deferred=deferred,
            )
            self._execute(target, followups)
            established = (
                self._state.owner == PixelOwner.NATIVE and owner_before != PixelOwner.NATIVE
            )
            if deferred and established:
                # Every consumer that asked `use_native` mid-flight was told "not yet" and
                # published nothing. The refresh is the seam that rebuilds hit boxes.
                target.refresh()

        def confirm(*, accepted: bool, reply: object) -> Callable[[EffectFinished], None]:
            def confirmed(read: EffectFinished) -> None:
                settle(
                    accepted=accepted,
                    visibility=self._visibility_of(read.result)
                    if read.outcome is EffectOutcome.SUCCEEDED
                    else Visibility.UNKNOWN,
                    reply=reply,
                )

            return confirmed

        def read_back(write: EffectFinished) -> None:
            # The readback runs whether or not the write was accepted: mpv's actual state decides
            # ownership, and a FALSE readback is legacy proof even when our write was refused.
            # `accepted` only feeds the diagnostic — report the typed error the terminal carries,
            # since the code is what tells a dead pipe from a rejected value.
            accepted = write.outcome is EffectOutcome.SUCCEEDED
            reply = write.result if accepted else {"error": str(write.error or write.outcome)}
            if not target.ipc.submit_runtime_mpv(
                owner=Owner.SUBTITLE,
                identity=_VISIBILITY_READBACK,
                command=("get_property", "sub-visibility"),
                timeout_s=10.0,
                on_finished=confirm(accepted=accepted, reply=reply),
            ):
                # An unread boundary is UNKNOWN, never legacy proof — the FSM's bounded retry
                # decides what happens next.
                settle(accepted=accepted, visibility=Visibility.UNKNOWN, reply=reply)

        if target.ipc.submit_runtime_mpv(
            owner=Owner.SUBTITLE,
            identity=_VISIBILITY_ASSERT,
            command=("set_property", "sub-visibility", True),
            timeout_s=10.0,
            on_finished=read_back,
        ):
            deferred = self._state.owner != PixelOwner.NATIVE
            return
        # No egress at all: there is nothing to assert against and nothing to read back, so the
        # boundary is UNKNOWN. Never legacy proof — the FSM's bounded retry decides what follows.
        settle(accepted=False, visibility=Visibility.UNKNOWN, reply={"error": "not-admitted"})

    def _capture_restore_visibility(self, ipc) -> None:
        if self._restore_visibility is not None:
            return
        initial = self._read_visibility(ipc)
        if initial != Visibility.UNKNOWN:
            self._restore_visibility = initial == Visibility.TRUE

    def _apply_assertion_result(
        self, action: OwnershipAction, visibility: Visibility
    ) -> tuple[OwnershipAction, ...]:
        self._state, followups = reduce_ownership(
            self._state,
            OwnershipEvent(
                EventKind.ASSERTION_RESULT,
                context=action.context,
                effect_id=action.effect_id,
                visibility=visibility,
            ),
        )
        self._visibility = self._visibility_value(visibility)
        self._native_ready = self._state.owner == PixelOwner.NATIVE
        return followups

    @staticmethod
    def _visibility_value(visibility: Visibility) -> bool | None:
        if visibility == Visibility.TRUE:
            return True
        if visibility == Visibility.FALSE:
            return False
        return None

    def _record_assertion_result(
        self,
        action: OwnershipAction,
        *,
        reply: object,
        accepted: bool,
        visibility: Visibility,
        owner_before: PixelOwner,
        exhausted_before: bool,
        deferred: bool = False,
    ) -> None:
        self._trace_ownership(
            "native-visibility-assertion",
            owner_before=owner_before,
            accepted=accepted,
            visibility=visibility,
            effect_id=action.effect_id,
            deferred=deferred,
        )
        if self._state.retry_exhausted and not exhausted_before:
            if otel_metrics.subtitle_pixel_retry_exhausted is not None:
                otel_metrics.subtitle_pixel_retry_exhausted.add(1)
            log.error("native subtitle visibility retries exhausted; pixel owner remains unknown")
        if self._native_ready:
            self._activation_failure_reported = False
        elif not accepted and not self._activation_failure_reported:
            self._activation_failure_reported = True
            failure = reply.get("error") if isinstance(reply, dict) else "unknown"
            log.warning("mpv rejected subtitle visibility assertion: %s", failure)

    def _stage_legacy(self, target: SubtitleTarget, action: OwnershipAction) -> None:
        """Stage legacy pixels, then hide mpv's — never the other way round.

        The hide may not precede a confirmed commit: mpv's subtitles would vanish while ours are
        still pending, leaving the frame with no subtitle at all. So the commit outcome gates the
        hide, and a failed commit rolls back to the last confirmed surface.
        """

        def settled(*, committed: bool) -> None:
            if committed and self._state.visibility != Visibility.FALSE:
                # mpv is still showing its own; hide them now that ours are acknowledged, and let
                # that write's outcome decide whether the handoff completed.
                self._hide_mpv_subtitles(target.ipc, on_finished=lambda ok: finish(accepted=ok))
            else:
                finish(accepted=committed)

        def finish(*, accepted: bool) -> None:
            owner_before = self._state.owner
            if not accepted:
                self._fallback.clear(target.surfaces, target.ipc)
            self._state, followups = reduce_ownership(
                self._state,
                OwnershipEvent(
                    EventKind.LEGACY_STAGE_RESULT,
                    context=action.context,
                    effect_id=action.effect_id,
                    accepted=accepted,
                ),
            )
            self._visibility = False if accepted else None
            self._native_ready = False
            is_rehandoff = action.kind == ActionKind.RESTAGE_LEGACY
            self._trace_ownership(
                "legacy-rehandoff-result" if is_rehandoff else "legacy-stage-result",
                owner_before=owner_before,
                accepted=accepted,
                effect_id=action.effect_id,
            )
            if (
                accepted
                and not is_rehandoff
                and self._state.context.mode == OwnershipMode.NATIVE_VISIBLE
            ):
                self._record_catastrophic_fallback()
            self._execute(target, followups)

        try:
            self._fallback.draw(
                target.draw_request(),
                target.surfaces,
                target.ipc,
                on_settled=lambda ok: settled(committed=ok),
            )
        except Exception:  # noqa: BLE001  # rollback preserves the last confirmed surface
            settled(committed=False)

    def _hide_mpv_subtitles(self, ipc, *, on_finished) -> None:
        """Hide mpv's subtitles once ours are confirmed. Correlated: whether the handoff completed
        is the write's terminal outcome, not a discarded reply."""
        _send_visibility(
            ipc,
            "subtitle:hide-for-legacy",
            visible=False,
            on_outcome=on_finished,
        )

    @staticmethod
    def _record_catastrophic_fallback() -> None:
        if otel_metrics.subtitle_pixel_catastrophic_fallbacks is not None:
            otel_metrics.subtitle_pixel_catastrophic_fallbacks.add(1)
        log.critical(
            "native subtitle pixels confirmed absent; committed catastrophic legacy recovery"
        )

    def _execute(self, target: SubtitleTarget, actions: tuple[OwnershipAction, ...]) -> None:
        pending = list(actions)
        while pending:
            batch, pending = pending, []
            for action in batch:
                self._apply_action(target, action)
            # A retry the timer port refused runs after its batch commits, so an immediate retry
            # never re-enters mid-batch. The FSM's bounded attempt count stops it looping.
            if (effect_id := self._retry_immediate) is not None:
                self._retry_immediate = None
                pending.extend(self._retry_actions(effect_id))

    def _apply_action(self, target: SubtitleTarget, action: OwnershipAction) -> None:
        if action.kind == ActionKind.ASSERT_NATIVE_VISIBILITY:
            self._assert_native(target, action)
        elif action.kind == ActionKind.CLEAR_LEGACY:
            self._fallback.clear(target.surfaces, target.ipc)
        elif action.kind == ActionKind.CLEAR_INTERACTION:
            if self._defer_focus_clear:
                self._focus_clear_pending = True
            else:
                self._hide_focus(target.ipc)
            self._hide_overpaint(target.surfaces)
        elif action.kind in {ActionKind.STAGE_LEGACY, ActionKind.RESTAGE_LEGACY}:
            if self.timed is not None:
                self.timed.invalidate("legacy-ownership")
            self._stage_legacy(target, action)
        elif action.kind == ActionKind.SHOW_MPV:
            _send_visibility(target.ipc, "ownership:show-mpv", visible=True)
        elif action.kind == ActionKind.SCHEDULE_RETRY:
            self._arm_retry(target, action)
        elif action.kind == ActionKind.CANCEL_RETRY:
            self._retry_effect_id = None
            self._retry_immediate = None
            target.ipc.cancel_runtime_timer(OWNERSHIP_RETRY_TIMER)
        elif action.kind == ActionKind.RESTORE_VISIBILITY:
            restore = True if self._restore_visibility is None else self._restore_visibility
            _send_visibility(target.ipc, "ownership:restore-visibility", visible=restore)

    def _arm_retry(self, target: SubtitleTarget, action: OwnershipAction) -> None:
        """Arm the ownership retry as a named deadline, fenced by its effect id."""
        effect_id = action.effect_id
        self._retry_effect_id = effect_id
        if effect_id is None:  # the FSM always ids a scheduled retry; nothing to fence without one
            return

        def due(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._execute(target, self._retry_actions(effect_id))

        if target.ipc.schedule_runtime_timer(
            owner=Owner.SUBTITLE,
            identity=OwnershipRetryDue(effect_id),
            timer=OWNERSHIP_RETRY_TIMER,
            due_at=self._clock() + (action.delay_ms or 0) / 1_000,
            on_finished=due,
        ):
            return
        # No timer port: run the retry immediately rather than dropping it. Losing the delay shows
        # up in tests; losing the retry would silently strand pixel ownership.
        self._retry_immediate = effect_id

    def _retry_actions(self, effect_id: int | None) -> tuple[OwnershipAction, ...]:
        """Reduce one due retry. A due for a superseded schedule is inert."""
        if effect_id is None or effect_id != self._retry_effect_id:
            return ()
        self._retry_effect_id = None
        self._state, actions = reduce_ownership(
            self._state,
            OwnershipEvent(
                EventKind.RETRY_DUE,
                context=self._state.context,
                effect_id=effect_id,
            ),
        )
        return actions

    def _ensure_selection(self, target: SubtitleTarget, sid: SelectedSid = ASK_MPV) -> None:
        """Publish a selection change if one happened.

        The change is enough on its own: `_change_context` re-runs `_start_mode`, which re-shows
        mpv's pixels. Chaining a verify onto it would assert twice for one fact.

        `sid` is the caller's declared selection. Reading it here instead is only safe when nobody
        has just written it — mpv echoes `sid` asynchronously, so a reconfigure that re-reads sees
        the track it is replacing and concludes nothing moved.
        """
        selection = repr(
            (
                target.prop("sid") if isinstance(sid, AskMpv) else sid,
                target.source,
                # In the selection because a forced switch has to READ as a selection change: this
                # method returns early on an unchanged one, so a flag outside it would toggle
                # nothing until the next track load.
                target.legacy_forced,
            )
        )
        if selection == self._selection:
            return
        self._selection = selection
        # A source geometry can never accept (an .srt, say) would otherwise leave the episode with
        # mpv's pixels and no hit boxes for its whole run, and a user who asks for the legacy
        # renderer is asking for the same thing deliberately. Choosing the mode HERE is what keeps
        # the module rule intact — "geometry availability never selects the renderer": both inputs
        # are evaluated once per selection, not geometry outcomes that could flip between cues.
        mode = (
            OwnershipMode.LEGACY_OVERLAY
            if target.native_unsupported or target.legacy_forced
            else OwnershipMode.NATIVE_VISIBLE
        )
        context = OwnershipContext(
            self._state.context.connection_epoch,
            self._state.context.ownership_epoch + 1,
            mode,
            selection,
        )
        self._state, actions = reduce_ownership(
            self._state,
            OwnershipEvent(EventKind.SELECTION_CHANGED, context=context),
        )
        self._execute(target, actions)

    def activate(self, target: SubtitleTarget, sid: SelectedSid = ASK_MPV) -> bool:
        """Own the pixels, idempotently. `False` means the caller must draw them itself.

        A reconfigure arrives here as a selection change, but only if it *declares* the track it
        selected: mpv has not echoed the write yet when it calls, so reading `sid` back would
        compare the incoming track against itself.
        """
        self._ensure_selection(target, sid)
        if (
            not self._state.native_pixels_established
            and self._state.active_assertion_id is None
            and self._state.retry_effect_id is None
            and not self._state.retry_exhausted
        ):
            self._state, actions = reduce_ownership(
                self._state, OwnershipEvent(EventKind.ENSURE_MODE)
            )
            self._execute(target, actions)
        return self._state.owner == PixelOwner.NATIVE

    def _verify_native(self, target: SubtitleTarget) -> bool:
        """Re-prove ownership against mpv rather than trusting the established flag."""
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.VERIFY_NATIVE)
        )
        self._execute(target, actions)
        return self._state.owner == PixelOwner.NATIVE

    def connection_replaced(self, target: SubtitleTarget) -> None:
        if self.timed is not None:
            self.timed.connection_replaced()
        self._focus_up = True  # a reconnected mpv may still show what the old connection put up
        context = OwnershipContext(
            self._state.context.connection_epoch + 1,
            self._state.context.ownership_epoch + 1,
            self._state.context.mode,
            self._state.context.selection,
        )
        owner_before = self._state.owner
        self._state, actions = reduce_ownership(
            self._state,
            OwnershipEvent(EventKind.CONNECTION_REPLACED, context=context),
        )
        self._trace_ownership("connection-replaced", owner_before=owner_before)
        self._execute(target, actions)

    def use_native(self, target: SubtitleTarget) -> bool:
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.GEOMETRY_READY)
        )
        self._execute(target, actions)
        return self.activate(target)

    def degrade_geometry(self, target: SubtitleTarget) -> None:
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.GEOMETRY_DEGRADED)
        )
        self._execute(target, actions)

    def cue_changed(self, target: SubtitleTarget, *, nonempty: bool) -> None:
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.CUE_CHANGED, nonempty=nonempty)
        )
        # The payload dedupe is NOT reset here: a split observation burst (sub-text, then its
        # timing) reconciles one cue twice, and the second draw's bytes are the first's. The slot
        # forgets its payload when it is emptied, which is the only time a rewrite is owed.
        self._defer_focus_clear = True
        self._execute(target, actions)
        if nonempty:
            self.activate(target)

    def flush_focus(self, ipc) -> None:
        """Pay a removal a cue change deferred, if no draw since has replaced the payload.

        Called after the cue's own draw: a cue that lands without geometry, or whose annotation is
        still computing, has drawn nothing, and the previous cue's glyphs must not stay painted
        over the new line.
        """
        self._defer_focus_clear = False
        if self._focus_clear_pending and ipc is not None:
            self._hide_focus(ipc)

    def draw(
        self, request: DrawRequest, surfaces=None, ipc=None, /, *, on_settled=None
    ) -> DrawResult | None:
        """Focus box over mpv's own pixels, or the legacy render when mpv does not own them."""
        # The focus slot is written straight through the IPC runtime, so the surface layer's
        # `set_visible(False)` does not cover it: without this, any redraw after `Alt+o` repainted
        # the token colors and the JLPT rules onto a session the user had just hidden, and they
        # stayed until a cue change happened to produce an empty payload.
        # `path` is the only per-cue signal that separates the two engines. `subtitle_renderer_forced`
        # counts the switch, once, so a session that ran entirely on the legacy renderer and one that
        # never touched it differ by a single event — and neither carries what the draw cost.
        with otel_metrics.traced("subtitle_draw") as span:
            self._color_request = request
            _record_color_demand(request, span)
            span.set("path", self._draw_path())
            # The tokenizer's count, not `len(request.boxes)`. Measured boxes are usually absent on
            # the legacy path and merely often present on the native one — 6 of 36 against 25 of 48
            # in one session — so they count what geometry happened to have landed, not what the cue
            # holds. A field trace read `tokens=0` for all 33 legacy draws before this.
            span.set("tokens", sum(len(line) for line in request.lines))
            span.set("measured_boxes", len(request.boxes))
            span.set("scan_tokens", len({b.index for b in request.boxes if b.w > 0 and b.h > 0}))
            span.set("paint_allowed", request.paint_allowed)
            # The one attribute that makes the wait a viewer sees derivable from the trace alone:
            # group draws by cue, take the first, take the first with boxes, subtract. Without it the
            # draws are an undifferentiated stream and the pair cannot be found. A digest rather than
            # the text — a span attribute has no cardinality limit, but a subtitle line is the user's
            # content and does not belong in a bundle that gets shared.
            span.set("cue", cue_digest(request.text))
            # How many tokens this cue OWES a color, from the styles it is drawing with. Without
            # it a draw is not self-describing: `measured_boxes=0` reads the same for a line whose
            # geometry has not landed and for a music marker that owes nothing, and telling them
            # apart meant joining to a decision span that is often absent — "no geometry decision
            # recorded" was the readout's most common verdict on the cue nobody could explain.
            span.set("owed_color", request.owed_color)
            span.set("requested_color_tokens", request.owed_color)
            span.set("permitted_color_tokens", request.owed_color if request.paint_allowed else 0)
            span.set("suppressed_color_tokens", 0 if request.paint_allowed else request.owed_color)
            # Color that was on screen for this cue and is not now. The wait-to-color reading takes
            # the FIRST colored draw and stops, so a cue losing its color later is invisible to it;
            # three such drops were found by hand and none of them by the readout.
            digest = cue_digest(request.text)
            lost = bool(
                self._painted_cue == digest
                and request.owed_color
                and (not request.paint_allowed or not request.boxes)
            )
            span.set("lost_color", lost)
            if request.paint_allowed and request.boxes:
                self._painted_cue = digest
            elif lost or not request.paint_allowed:
                self._painted_cue = None
            # How many authored events the drawn boxes came from. `active_events` on the geometry
            # side says how many the snapshot was measured against; this says how many reached the
            # screen, and a bundle where they disagree is a frame drawn with another frame's boxes
            # — the mispairing that reads as "geometry that never arrived".
            span.set("box_events", len({box.event_id for box in request.boxes if box.event_id}))
            return self._draw(request, surfaces, ipc, on_settled=on_settled)

    def _draw_path(self) -> str:
        if self._suspended:
            return "suspended"
        if self._state.owner == PixelOwner.LEGACY:
            return "legacy"
        return "native" if self._state.owner == PixelOwner.NATIVE else "unowned"

    def _draw(
        self, request: DrawRequest, surfaces=None, ipc=None, /, *, on_settled=None
    ) -> DrawResult | None:
        if self._suspended:
            self.flush_focus(ipc)
            return None
        if self._state.owner == PixelOwner.LEGACY:
            self.flush_focus(ipc)
            return self._fallback.draw(request, surfaces, ipc, on_settled=on_settled)
        # `activate` drives the ownership FSM and needs the host, so the coordinator runs it just
        # before this and we read only its outcome. Splitting them is what lets `draw` be host-free.
        if self._state.owner != PixelOwner.NATIVE:
            self.flush_focus(ipc)
            return None
        _record_whole_cue_decision(request)
        if not request.paint_allowed:
            self._refuse_paint(surfaces, ipc)
            return None
        rect = (
            focus_rect(request.boxes, request.hover, request.hover_span)
            if request.hover >= 0 and box_for_token(request.boxes, request.hover) is not None
            else None
        )
        overprint, resolution = self._draw_whole_cue(request, surfaces)
        overprint = self._timed_overprint(request, overprint, resolution)
        rect = _focus_in_space(rect, request.osd, resolution)
        drawing = overprint
        if rect is not None:
            # One slot, one payload: the highlight and the color are drawn together so a repaint
            # can never leave one of them showing the previous cue.
            drawing = f"{focus_drawing(rect)}\n{overprint}" if overprint else focus_drawing(rect)
        digest = cue_digest(request.text)
        if not drawing:
            if self._defer_focus_clear and self._focus_up and self._focus_cue == digest:
                # The slot already holds this cue's own color: a seek pre-armed it, and mpv is now
                # re-reporting the cue in halves (text first, rows a turn later) with nothing to
                # measure against yet. The removal a cue change deferred is not owed to a payload
                # the cue itself put up; the geometry that lands next replaces or dedupes it.
                self._focus_clear_pending = False
                self._defer_focus_clear = False
            elif self._defer_focus_clear:
                # Inside a cue change the first draw often has boxes and no styles yet; the styled
                # draw follows in the same call. Removing here put a white frame between them.
                self._focus_clear_pending = True
            else:
                self._hide_focus(ipc)
            return None
        self._focus_cue = digest
        self._submit_focus(
            ipc,
            SurfaceAction.PRESENT,
            (
                NATIVE_FOCUS_ID,
                "ass-events",
                drawing,
                resolution[0],
                resolution[1],
                1,
            ),
            colored=bool(overprint),
        )
        return None

    def _whole_raster(self, request: DrawRequest, colors, span) -> overpaint.Overpaint | None:
        cue = request.whole_cue
        if cue is None:
            return None
        key = (id(cue), colors, request.whole_cue_origin)
        span.set("cache_hit", key == self._whole_image_key)
        if key != self._whole_image_key:
            image = whole_cue.compose(cue, colors)
            self._whole_image = (
                None
                if image is None
                else overpaint.Overpaint(
                    image.x + request.whole_cue_origin[0],
                    image.y + request.whole_cue_origin[1],
                    image.rgba,
                )
            )
            self._whole_image_key = key
            self._whole_image_cue = cue
        return self._whole_image

    def _draw_whole_cue(self, request: DrawRequest, surfaces) -> tuple[str, tuple[int, int]]:
        device, reason = whole_cue_device(request)
        colors = whole_cue_colors(request)
        cue = request.whole_cue
        with otel_metrics.traced("subtitle_whole_cue") as span:
            span.set("requested", self.coloring)
            span.set("device", device)
            span.set("reason", reason)
            _record_color_demand(request, span)
            if device == "overpaint" and cue is not None:
                self._publish_overpaint(
                    request, surfaces, image=self._whole_raster(request, colors, span)
                )
            else:
                self._hide_overpaint(surfaces)
        return self._whole_osd(request, device, colors)

    def _refuse_paint(self, surfaces, ipc) -> None:
        # A refusal also retires same-cue paint retained during a split observation burst.
        self._defer_focus_clear = False
        self._hide_focus(ipc)
        self._hide_overpaint(surfaces)

    def _timed_overprint(
        self, request: DrawRequest, overprint: str, resolution: tuple[int, int]
    ) -> str:
        if self.timed is not None and whole_cue_device(request)[0] == "overprint":
            owned, acknowledged = self.timed.present(
                request.whole_cue_identity,
                overprint,
                resolution,
                occurrence=request.color_occurrence,
            )
            if owned:
                if overprint:
                    submission = _color_submission(request, "overprint")
                    if acknowledged:
                        _color_settled(submission, accepted=True)
                    _color_is_up(cue_digest(request.text), landed=acknowledged)
                overprint = ""
        elif self.timed is not None and whole_cue_device(request)[0] == "overpaint":
            self.timed.retire(request.whole_cue_identity)
        return overprint

    def _whole_osd(self, request: DrawRequest, device: str, colors) -> tuple[str, tuple[int, int]]:
        cue = request.whole_cue
        # Underline colors are independent of the fill color and remain vector decorations.
        resolution = cue.resolution if device == "overprint" and cue is not None else request.osd
        sx, sy = resolution[0] / request.osd[0], resolution[1] / request.osd[1]
        rules = tuple(
            decoration.TokenRule(
                round(box.x * sx), round(box.y * sy), round(box.w * sx), round(box.h * sy), color
            )
            for box in (request.boxes if request.paint_boxes is None else request.paint_boxes)
            if device != "none"
            and request.paint_allowed
            and (color := _token_underline(request, box.index)) is not None
        )
        artifact = self._prepared_osd.prepare(
            OsdInputs(
                cue.events if device == "overprint" and cue is not None else (),
                cue.mapping if cue is not None else (1, 1, 0, 0),
                resolution,
                colors,
                rules,
            )
        )
        return artifact.payload, artifact.resolution

    def prepare_osd(self, request: DrawRequest) -> tuple[str, tuple[int, int]] | None:
        """Warm final ASS bytes without touching an mpv surface or occurrence accounting."""
        device, _reason = whole_cue_device(request)
        if device == "overprint":
            return self._whole_osd(request, device, whole_cue_colors(request))
        return None

    @property
    def can_stage_timed(self) -> bool:
        return not self._suspended and self._state.owner == PixelOwner.NATIVE

    def clear(self, surfaces=None, ipc=None, /) -> None:
        self._defer_focus_clear = False  # a clear is the cue turn's end, deferred or not
        self._hide_focus(ipc)
        self._hide_overpaint(surfaces)
        self._fallback.clear(surfaces, ipc)

    def _hide_overpaint(self, surfaces) -> None:
        if surfaces is None or not self._overpaint_shown:
            return
        self._drop_overpaint()
        submission = _color_submission(self._color_request, "overpaint", clear=True)
        surfaces.remove(
            OverlayId.OVERPAINT,
            owner=Owner.SUBTITLE,
            on_settled=lambda accepted: _color_settled(submission, accepted=accepted),
        )

    def _drop_overpaint(self) -> None:
        """Forget what is on the slot, because nothing is.

        Every path that takes the raster down goes through here — `clear`, `suspend_for_overlay`,
        and a cue with no raster of its own. Leaving the memo standing would tell the next publish
        of the same cue that its pixels are already up, and the raster would never come back after
        a tooltip closed.
        """
        self._overpaint_shown = False
        self._overpaint_acknowledged = False
        self._end_overpaint_operation("cancelled")

    def _end_overpaint_operation(self, outcome: str) -> None:
        if self._overpaint_operation is not None:
            self._overpaint_operation.finish(outcome=outcome)
            self._overpaint_operation = None

    def close(self) -> None:
        self._end_overpaint_operation("shutdown-aborted")
        if self.timed is not None:
            self.timed.close()
        self._whole_image_key = self._whole_image = self._whole_image_cue = None
        self._fallback.close()

    @property
    def logged_first(self) -> bool:
        """The fallback's, since it is the one that rasters — and logs — when mpv is not the owner."""
        return self._fallback.logged_first

    def deactivate(self, target: SubtitleTarget) -> None:
        if self.timed is not None:
            self.timed.invalidate("deactivate")
        self._defer_focus_clear = False  # a close pays its removal now, whatever turn was open
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.CLOSE_REQUESTED)
        )
        try:
            self._execute(target, actions)
            self._submit_focus(target.ipc, SurfaceAction.REMOVE, (NATIVE_FOCUS_ID, "none", ""))
        except (OSError, ValueError):
            log.info("could not finish native subtitle ownership teardown")
        self._state, _ = reduce_ownership(self._state, OwnershipEvent(EventKind.CLOSE_FINISHED))

    def suspend_for_overlay(self, target: SubtitleTarget) -> None:
        if self.timed is not None:
            self.timed.invalidate("suspend")
        self._suspended = True
        self._defer_focus_clear = False
        self._hide_focus(target.ipc)
        self._hide_overpaint(target.surfaces)
        self._fallback.clear(target.surfaces, target.ipc)
        _send_visibility(target.ipc, "subtitle:suspend-native-for-overlay", visible=True)

    def resume_after_overlay(self, target: SubtitleTarget) -> bool:
        self._suspended = False
        if self.timed is not None:
            self.timed.refresh()
        if self._state.owner == PixelOwner.LEGACY:
            self._state, actions = reduce_ownership(
                self._state, OwnershipEvent(EventKind.LEGACY_REHANDOFF)
            )
            self._execute(target, actions)
            return False
        # The track can change while the overlay is up, so publish the selection first — `reassert`
        # did, and dropping it left the epoch naming a track that is gone.
        self._ensure_selection(target)
        # Verify, not activate: `suspend_for_overlay` set sub-visibility behind the FSM's back, so
        # the established flag is stale by construction and only mpv can settle it.
        self._verify_native(target)
        return False

    def _publish_overpaint(
        self, request: DrawRequest, surfaces, *, image: overpaint.Overpaint | None = None
    ) -> None:
        """Put device 2's raster on its own slot, or take it down when this cue has none.

        Both halves matter: a cue that needs no raster must actively remove the previous one, or the
        last attachment-only cue's colors stay painted over the words of every cue after it.
        """
        if surfaces is None:
            return
        if image is None:
            self._hide_overpaint(surfaces)
            return
        # A hover redraws the whole cue, and the raster is the one part of it a hover cannot
        # change — the highlight is device 1's slot, not this one. One live cue published fifteen
        # byte-identical frames against seven tooltips; each is an upload of the cue's pixels to
        # mpv for a screen that already shows them.
        published = (image.x, image.y, image.rgba.tobytes())
        if self._overpaint_shown and published == self._overpaint_published:
            self._overpaint_color_pending = _color_submission(request, "overpaint")
            if self._overpaint_acknowledged:
                _color_settled(self._overpaint_color_pending, accepted=True)
            return
        self._overpaint_shown = True
        self._overpaint_published = published
        self._overpaint_acknowledged = False
        self._overpaint_color_pending = _color_submission(request, "overpaint")
        _trace_overpaint_placement(request, image)
        self._end_overpaint_operation("superseded")
        operation = otel_metrics.DeferredSpan(
            "subtitle_device_upload",
            device="overpaint",
            validation_scope="upload-acknowledgment-not-pixels",
        )
        self._overpaint_operation = operation

        def settled(accepted: bool) -> None:  # noqa: FBT001 -- surface settlement callback contract
            operation.finish(
                outcome="acknowledged" if accepted else "failed", bytes=len(published[2])
            )
            if self._overpaint_published is published:
                self._overpaint_acknowledged = accepted
                _color_settled(self._overpaint_color_pending, accepted=accepted)
            if not accepted and self._overpaint_published is published:
                self._overpaint_published = None

        # The array path, not `present`: these pixels were composited into numpy and never were a
        # PIL image. Wrapping one to unwrap it again is two copies of every cue.
        surfaces.present_rgba(
            image.rgba,
            image.x,
            image.y,
            oid=OverlayId.OVERPAINT,
            owner=Owner.SUBTITLE,
            on_settled=otel_metrics.bind_context(settled),
        )
        if otel_metrics.subtitle_overpaint_frames is not None:
            otel_metrics.subtitle_overpaint_frames.add(1)

    def _hide_focus(self, ipc) -> None:
        self._focus_clear_pending = False
        self._focus_cue = None
        if not self._focus_up:
            return  # the slot is already empty; mpv would parse a removal of nothing
        # `none` destroys mpv's per-slot libass renderer and font caches.
        self._submit_focus(ipc, SurfaceAction.REMOVE, (NATIVE_FOCUS_ID, "ass-events", ""))

    def warm_osd(
        self, target: SubtitleTarget, cue: whole_cue.WholeCue, record: Callable[[dict], None]
    ) -> None:
        """Render lookahead in the presentation slot without exposing its pixels."""
        if (
            self.coloring not in {"whole-cue-osd", "whole-cue-auto"}
            or self._suspended
            or self._focus_up
            or cue.osd_reason != "eligible"
        ):
            return
        colors = tuple((index, 0xFFFFFF) for event in cue.events for _, _, index in event.spans)
        payload = whole_cue.osd_payload(cue, colors)
        if not payload:
            return
        self._submit_focus(
            target.ipc,
            SurfaceAction.REMOVE,
            (NATIVE_FOCUS_ID, "ass-events", payload, *cue.resolution, 1, True, True),
            warm_record=record,
        )

    def _color_wait_cue(self, *, colored: bool) -> str | None:
        """The cue this write would put color on screen for, or `None` when it carries no color —
        a hover highlight shares the slot, and settling a wait on one would time the wrong thing."""
        return self._focus_cue if colored else None

    def _submit_focus(
        self,
        ipc,
        action: SurfaceAction,
        tail: tuple[object, ...],
        *,
        colored: bool = False,
        warm_record: Callable[[dict], None] | None = None,
    ) -> None:
        """One fenced write to the focus slot. A stale acknowledgement is dropped by the runtime,
        so an overtaken highlight can never repaint over the current one.

        *colored* separates the payloads that carry the overprint from a hover highlight drawn on
        the same slot: only the former ends a cue's wait for its color.
        """
        submission = (
            None
            if warm_record is not None
            else _color_submission(
                self._color_request,
                "overprint",
                clear=action is SurfaceAction.REMOVE,
            )
        )
        if action is SurfaceAction.PRESENT and tail == self._focus_payload:
            self._focus_color_pending = submission
            # Byte-identical to what is already up. `cue_redraw` and `subtitle_geometry_apply` both
            # draw in the same millisecond and build the same payload, so every cue was paying mpv
            # twice to composite one picture — measured at 2-33 ms per write, doubled.
            if otel_metrics.subtitle_focus_writes_skipped is not None:
                otel_metrics.subtitle_focus_writes_skipped.add(1)
            # The pixels this cue wants are already up — a seek pre-armed them. The wait ends
            # here, not at a write that never had to happen.
            settled = self._focus.snapshot(_FOCUS_SLOT)
            if settled is not None and settled.status is SurfaceStatus.PRESENT:
                _color_settled(submission, accepted=True)
                _color_is_up(self._color_wait_cue(colored=colored), landed=True)
            # What is up is what this cue wants: a removal the cue change deferred is not owed.
            self._focus_clear_pending = False
            self._defer_focus_clear = False
            return
        transaction = self._focus.request(_FOCUS_SLOT, action)
        self._focus_color_pending = submission
        # Read now, not in the callback: by the time mpv answers, the slot may hold a later cue.
        colored_cue = self._color_wait_cue(colored=colored)
        # Timed here as well as in `LifecycleSurfaces`, because the subtitle's own overlay write
        # does not go through that layer — it is the payload that carries the color, and it was the
        # one write on the draw path with nothing measuring what mpv did with it.
        started = time.perf_counter()

        def finished(completion: EffectFinished) -> None:
            accepted = self._focus.finish(
                SurfaceTransactionOutcome(transaction, completion.outcome, completion.error)
            )
            _trace_focus_write(
                transaction,
                completion,
                tail,
                started,
                accepted=accepted,
                colored=colored,
                warm_record=warm_record,
            )
            if not accepted:
                return
            _color_settled(
                self._focus_color_pending, accepted=completion.outcome is EffectOutcome.SUCCEEDED
            )
            _color_is_up(colored_cue, landed=completion.outcome is EffectOutcome.SUCCEEDED)
            if completion.outcome is not EffectOutcome.SUCCEEDED:
                self._focus_payload = None  # never skip against a write that did not land
                self._focus_cue = None  # nor keep a color the slot may not hold
                self._focus_up = True  # nor a removal against a slot whose state is unknown

        self._focus_payload = tail if action is SurfaceAction.PRESENT else None
        self._focus_clear_pending = False
        self._defer_focus_clear = False  # the cue has its payload; later removals are real
        self._focus_up = action is SurfaceAction.PRESENT
        if not ipc.submit_runtime_mpv(
            owner=Owner.SUBTITLE,
            identity=transaction,
            command=("osd-overlay", *tail),
            timeout_s=10.0,
            on_finished=otel_metrics.bind_context(finished),
        ):
            _color_settled(submission, accepted=False)
            _trace_focus_write(
                transaction,
                None,
                tail,
                started,
                accepted=False,
                colored=colored,
                warm_record=warm_record,
            )
            self._focus_payload = None
            self._focus_cue = None
            self._focus_up = True  # the write never left: the slot holds whatever it held
            self._focus.finish(
                SurfaceTransactionOutcome(
                    transaction, EffectOutcome.FAILED, EffectError.DISCONNECTED
                )
            )
