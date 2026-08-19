"""The subtitle raster, behind an injectable strategy so tests can run the reader loop headless.

``Reader.renderer`` holds a :class:`SubtitleRenderer` (the real blit); pass :class:`NullRenderer` to
suppress the raster and assert state only — the public seam that replaces monkeypatching the private
``_draw_subtitle`` (#50). The strategy takes the ``Reader`` as its host, matching the collaborator
pattern the other app modules use.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import subtitle_raster
from saitenka.app.languages import SECOND_LANG
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitle_ownership import (
    ActionKind,
    EventKind,
    OwnershipAction,
    OwnershipContext,
    OwnershipEvent,
    OwnershipMode,
    OwnershipState,
    PixelOwner,
    Visibility,
    reduce_ownership,
)
from saitenka.app.subtitles import box_for_token
from saitenka.runtime import EffectError, EffectFinished, EffectOutcome, Owner
from saitenka.runtime.surfaces import (
    SurfaceAction,
    SurfaceRuntime,
    SurfaceTransactionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.runtime.surfaces import SurfaceTransaction

log = logging.getLogger("saitenka.app.subtitle_render")

SUB_ID = OverlayId.SUB
OWNERSHIP_RETRY_TIMER = "subtitle:ownership-retry"


@dataclass(frozen=True, slots=True)
class OwnershipRetryDue:
    """Identity of one ownership retry deadline; the effect id fences a late due."""

    effect_id: int


NATIVE_FOCUS_ID = 1_001

_FOCUS_SLOT = "subtitle-native-focus"
_VISIBILITY_ASSERT = "ownership:assert-native-visibility"
_VISIBILITY_READBACK = "ownership:readback-visibility"


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


class SubtitleRenderer:
    """Rasterize the current cue and blit it as the SUB overlay — the real draw path."""

    def activate(self, reader: Reader) -> bool:
        if not hasattr(self, "_restore_visibility"):
            self._restore_visibility = reader._get("sub-visibility")
        reply = reader.ipc.command(
            "set_property",
            "sub-visibility",
            False,  # noqa: FBT003  # mpv IPC wire value
        )
        return not isinstance(reply, dict) or reply.get("error") in {None, "success"}

    def deactivate(self, reader: Reader) -> None:
        restore = getattr(self, "_restore_visibility", None)
        if restore is not None:
            try:
                reader.ipc.command("set_property", "sub-visibility", restore)
            except (OSError, ValueError):
                log.info("could not restore mpv subtitle visibility during close")

    def suspend_for_overlay(self, reader: Reader) -> None:
        _send_visibility(reader.ipc, "subtitle:suspend-for-overlay", visible=True)

    def resume_after_overlay(self, reader: Reader) -> None:
        _send_visibility(reader.ipc, "subtitle:resume-after-overlay", visible=False)

    def __init__(self, provider: subtitle_raster.SubtitleRasterPort | None = None) -> None:
        self.provider: subtitle_raster.SubtitleRasterPort = (
            provider or subtitle_raster.PillowRasterProvider()
        )
        self._closed = False

    def close(self) -> None:
        """Quarantine the surface and release the provider. A cue that arrives after this — a late
        annotation publishing its upgrade — must not stage pixels onto a slot the close path has
        already emptied."""
        self._closed = True
        self.provider.close()

    def draw(
        self, reader: Reader, *, on_settled: Callable[[bool], None] | None = None
    ) -> SurfaceTransaction | None:
        if self._closed:
            if on_settled is not None:
                on_settled(False)  # noqa: FBT003  # the settlement flag is the whole payload
            return None
        # Plain covers the secondary track and any cue still awaiting (or denied) its annotation:
        # the cue shows at cue time and reader_deps re-renders it annotated once deps land.
        request = subtitle_raster.build_request(
            subtitle_raster.raster_style(
                secondary_role=reader.subtitle_language == SECOND_LANG,
                upgrade_pending=reader._sub_pending is not None,
                annotation_degraded=reader._annotation_degraded,
            ),
            subtitle_raster.RasterContent(
                reader.sub_text,
                reader.lines,
                reader.osd[0],
                reader.sub_size,
                # configurable box alpha (0 = fully transparent)
                (0, 0, 0, reader.sub_bg_opacity),
            ),
            subtitle_raster.AnnotationOverlay(
                subtitle_raster.annotation_visible(
                    mode=reader.annotation_mode, hover_annotation=reader._annotation_hover
                ),
                reader.hover,
                reader._hover_span,
                reader.styles,
            ),
        )
        with otel_metrics.instrumented(otel_metrics.subtitle_render_duration_ms, "subtitle_render"):
            sr = self.provider.render(request)
        reader.boxes = list(sr.boxes)
        ox = (reader.osd[0] - sr.image.width) // 2
        oy = reader.osd[1] - sr.image.height - reader.bottom_margin
        reader.sub_origin = (ox, oy)
        if not reader._first_sub_logged:
            reader._first_sub_logged = True
            log.info(
                "first subtitle drawn (%dx%d at %d,%d)", sr.image.width, sr.image.height, ox, oy
            )
        # Revision-fenced: a newer cue's transaction supersedes this one's acknowledgement rather
        # than racing it onto the same mpv slot. `on_settled` is how staging learns pixels exist.
        return reader.lifecycle_surfaces.present(
            sr.image, ox, oy, oid=SUB_ID, owner=Owner.SUBTITLE, on_settled=on_settled
        )

    def clear(self, reader: Reader) -> None:
        reader.lifecycle_surfaces.remove(SUB_ID, owner=Owner.SUBTITLE)


class NullRenderer:
    """No-op draw strategy: run the reader's hover/nav/prefetch logic without rasterizing."""

    def draw(self, reader: Reader) -> None:
        pass

    def clear(self, reader: Reader) -> None:
        pass

    def close(self) -> None:
        pass


class NativeVisibleRenderer:
    """Keep mpv pixels visible while geometry independently supplies interaction boxes."""

    def __init__(
        self,
        fallback: SubtitleRenderer | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
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
        try:
            reply = ipc.command("get_property", "sub-visibility")
        except Exception:  # noqa: BLE001  # an unreadable boundary is unknown, never legacy proof
            return Visibility.UNKNOWN
        if not isinstance(reply, dict) or reply.get("error") not in {None, "success"}:
            return Visibility.UNKNOWN
        return self._visibility_of(reply.get("data"))

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

    def _assert_native(self, reader: Reader, action: OwnershipAction) -> None:
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
        self._capture_restore_visibility(reader.ipc)
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
            self._execute(reader, followups)
            established = (
                self._state.owner == PixelOwner.NATIVE and owner_before != PixelOwner.NATIVE
            )
            if deferred and established and reader.native_geometry is not None:
                # Every consumer that asked `use_native` mid-flight was told "not yet" and
                # published nothing. The refresh is the seam that rebuilds hit boxes.
                reader.native_geometry.refresh(reader)

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
            if not reader.ipc.submit_runtime_mpv(
                owner=Owner.SUBTITLE,
                identity=_VISIBILITY_READBACK,
                command=("get_property", "sub-visibility"),
                timeout_s=10.0,
                on_finished=confirm(accepted=accepted, reply=reply),
            ):
                # An unread boundary is UNKNOWN, never legacy proof — the FSM's bounded retry
                # decides what happens next.
                settle(accepted=accepted, visibility=Visibility.UNKNOWN, reply=reply)

        if reader.ipc.submit_runtime_mpv(
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

    def _stage_legacy(self, reader: Reader, action: OwnershipAction) -> None:
        """Stage legacy pixels, then hide mpv's — never the other way round.

        The hide may not precede a confirmed commit: mpv's subtitles would vanish while ours are
        still pending, leaving the frame with no subtitle at all. So the commit outcome gates the
        hide, and a failed commit rolls back to the last confirmed surface.
        """

        def settled(*, committed: bool) -> None:
            if committed and self._state.visibility != Visibility.FALSE:
                # mpv is still showing its own; hide them now that ours are acknowledged, and let
                # that write's outcome decide whether the handoff completed.
                self._hide_mpv_subtitles(reader.ipc, on_finished=lambda ok: finish(accepted=ok))
            else:
                finish(accepted=committed)

        def finish(*, accepted: bool) -> None:
            owner_before = self._state.owner
            if not accepted:
                self._fallback.clear(reader)
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
            self._execute(reader, followups)

        try:
            self._fallback.draw(reader, on_settled=lambda ok: settled(committed=ok))
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

    def _execute(self, reader: Reader, actions: tuple[OwnershipAction, ...]) -> None:
        pending = list(actions)
        while pending:
            batch, pending = pending, []
            for action in batch:
                self._apply_action(reader, action)
            # A retry the timer port refused runs after its batch commits, so an immediate retry
            # never re-enters mid-batch. The FSM's bounded attempt count stops it looping.
            if (effect_id := self._retry_immediate) is not None:
                self._retry_immediate = None
                pending.extend(self._retry_actions(effect_id))

    def _apply_action(self, reader: Reader, action: OwnershipAction) -> None:
        if action.kind == ActionKind.ASSERT_NATIVE_VISIBILITY:
            self._assert_native(reader, action)
        elif action.kind == ActionKind.CLEAR_LEGACY:
            self._fallback.clear(reader)
        elif action.kind == ActionKind.CLEAR_INTERACTION:
            self._hide_focus(reader.ipc)
        elif action.kind in {ActionKind.STAGE_LEGACY, ActionKind.RESTAGE_LEGACY}:
            self._stage_legacy(reader, action)
        elif action.kind == ActionKind.SHOW_MPV:
            reader.ipc.command(
                "set_property",
                "sub-visibility",
                True,  # noqa: FBT003  # mpv IPC wire value
            )
        elif action.kind == ActionKind.SCHEDULE_RETRY:
            self._arm_retry(reader, action)
        elif action.kind == ActionKind.CANCEL_RETRY:
            self._retry_effect_id = None
            self._retry_immediate = None
            cancel = getattr(reader.ipc, "cancel_runtime_timer", None)
            if cancel is not None:
                cancel(OWNERSHIP_RETRY_TIMER)
        elif action.kind == ActionKind.RESTORE_VISIBILITY:
            restore = True if self._restore_visibility is None else self._restore_visibility
            reader.ipc.command("set_property", "sub-visibility", restore)

    def _arm_retry(self, reader: Reader, action: OwnershipAction) -> None:
        """Arm the ownership retry as a named deadline, fenced by its effect id."""
        effect_id = action.effect_id
        self._retry_effect_id = effect_id
        if effect_id is None:  # the FSM always ids a scheduled retry; nothing to fence without one
            return

        def due(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._execute(reader, self._retry_actions(effect_id))

        schedule = getattr(reader.ipc, "schedule_runtime_timer", None)
        if schedule is not None and schedule(
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

    def _ensure_selection(self, reader: Reader) -> None:
        selection = repr(
            (
                reader._prop("sid"),
                reader.native_geometry.source_path if reader.native_geometry else None,
            )
        )
        if selection == self._selection:
            return
        self._selection = selection
        context = OwnershipContext(
            self._state.context.connection_epoch,
            self._state.context.ownership_epoch + 1,
            OwnershipMode.NATIVE_VISIBLE,
            selection,
        )
        self._state, actions = reduce_ownership(
            self._state,
            OwnershipEvent(EventKind.SELECTION_CHANGED, context=context),
        )
        self._execute(reader, actions)

    def activate(self, reader: Reader) -> bool:
        self._ensure_selection(reader)
        if (
            not self._state.native_pixels_established
            and self._state.active_assertion_id is None
            and self._state.retry_effect_id is None
            and not self._state.retry_exhausted
        ):
            self._state, actions = reduce_ownership(
                self._state, OwnershipEvent(EventKind.ENSURE_MODE)
            )
            self._execute(reader, actions)
        return self._state.owner == PixelOwner.NATIVE

    def reassert(self, reader: Reader) -> bool:
        self._ensure_selection(reader)
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.VERIFY_NATIVE)
        )
        self._execute(reader, actions)
        return self._state.owner == PixelOwner.NATIVE

    def connection_replaced(self, reader: Reader) -> None:
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
        self._execute(reader, actions)

    def use_native(self, reader: Reader) -> bool:
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.GEOMETRY_READY)
        )
        self._execute(reader, actions)
        return self.activate(reader)

    def degrade_geometry(self, reader: Reader) -> None:
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.GEOMETRY_DEGRADED)
        )
        self._execute(reader, actions)

    def cue_changed(self, reader: Reader, *, nonempty: bool) -> None:
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.CUE_CHANGED, nonempty=nonempty)
        )
        self._execute(reader, actions)
        if nonempty:
            self.activate(reader)

    def draw(self, reader: Reader) -> None:
        if self._state.owner == PixelOwner.LEGACY:
            self._fallback.draw(reader)
            return
        if not self.activate(reader):
            return
        if reader.hover < 0 or box_for_token(reader.boxes, reader.hover) is None:
            self._hide_focus(reader.ipc)
            return
        span = reader._hover_span or (reader.hover, reader.hover + 1)
        selected = [box for box in reader.boxes if span[0] <= box.index < span[1]]
        if not selected:
            self._hide_focus(reader.ipc)
            return
        left = min(box.x for box in selected)
        top = min(box.y for box in selected)
        right = max(box.x + box.w for box in selected)
        bottom = max(box.y + box.h for box in selected)
        pad = 3
        width = right - left + 2 * pad
        height = bottom - top + 2 * pad
        drawing = (
            rf"{{\an7\pos({left - pad},{top - pad})\bord2\shad0"
            rf"\1c&H5AD6FF&\1a&HDF&\3c&H5AD6FF&\3a&H23&\p1}}"
            f"m 0 0 l {width} 0 l {width} {height} l 0 {height}"
        )
        self._submit_focus(
            reader.ipc,
            SurfaceAction.PRESENT,
            (NATIVE_FOCUS_ID, "ass-events", drawing, reader.osd[0], reader.osd[1], 1),
        )

    def clear(self, reader: Reader) -> None:
        self._hide_focus(reader.ipc)
        self._fallback.clear(reader)

    def close(self) -> None:
        self._fallback.close()

    def deactivate(self, reader: Reader) -> None:
        self._state, actions = reduce_ownership(
            self._state, OwnershipEvent(EventKind.CLOSE_REQUESTED)
        )
        try:
            self._execute(reader, actions)
        except (OSError, ValueError):
            log.info("could not finish native subtitle ownership teardown")
        self._state, _ = reduce_ownership(self._state, OwnershipEvent(EventKind.CLOSE_FINISHED))

    def suspend_for_overlay(self, reader: Reader) -> None:
        self._hide_focus(reader.ipc)
        self._fallback.clear(reader)
        _send_visibility(reader.ipc, "subtitle:suspend-native-for-overlay", visible=True)

    def resume_after_overlay(self, reader: Reader) -> None:
        if self._state.owner == PixelOwner.LEGACY:
            self._state, actions = reduce_ownership(
                self._state, OwnershipEvent(EventKind.LEGACY_REHANDOFF)
            )
            self._execute(reader, actions)
            return
        self.reassert(reader)

    def _hide_focus(self, ipc) -> None:
        self._submit_focus(ipc, SurfaceAction.REMOVE, (NATIVE_FOCUS_ID, "none", ""))

    def _submit_focus(self, ipc, action: SurfaceAction, tail: tuple[object, ...]) -> None:
        """One fenced write to the focus slot. A stale acknowledgement is dropped by the runtime,
        so an overtaken highlight can never repaint over the current one."""
        transaction = self._focus.request(_FOCUS_SLOT, action)

        def finished(completion: EffectFinished) -> None:
            self._focus.finish(
                SurfaceTransactionOutcome(transaction, completion.outcome, completion.error)
            )

        if not ipc.submit_runtime_mpv(
            owner=Owner.SUBTITLE,
            identity=transaction,
            command=("osd-overlay", *tail),
            timeout_s=10.0,
            on_finished=finished,
        ):
            self._focus.finish(
                SurfaceTransactionOutcome(
                    transaction, EffectOutcome.FAILED, EffectError.DISCONNECTED
                )
            )
