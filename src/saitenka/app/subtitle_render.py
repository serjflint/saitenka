"""The subtitle raster, behind an injectable strategy so tests can run the reader loop headless.

``Reader.renderer`` holds a :class:`SubtitleRenderer` (the real blit); pass :class:`NullRenderer` to
suppress the raster and assert state only — the public seam that replaces monkeypatching the private
``_draw_subtitle`` (#50). The strategy takes the ``Reader`` as its host, matching the collaborator
pattern the other app modules use.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from saitenka import otel_metrics
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
from saitenka.app.subtitles import box_for_token, render_plain_subtitle, render_subtitle

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.controller import Reader

log = logging.getLogger("saitenka.app.subtitle_render")

SUB_ID = OverlayId.SUB
NATIVE_FOCUS_ID = 1_001


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
        reader.ipc.command(
            "set_property",
            "sub-visibility",
            True,  # noqa: FBT003  # mpv IPC wire value
        )

    def resume_after_overlay(self, reader: Reader) -> None:
        reader.ipc.command(
            "set_property",
            "sub-visibility",
            False,  # noqa: FBT003  # mpv IPC wire value
        )

    def draw(self, reader: Reader) -> dict:
        with otel_metrics.instrumented(otel_metrics.subtitle_render_duration_ms, "subtitle_render"):
            # Draw plain for the known/translation track, OR a cue still awaiting a complete
            # tokenization (dictionaries loading): the cue shows at cue time and reader_deps
            # re-renders it annotated once deps land.
            background = (
                0,
                0,
                0,
                reader.sub_bg_opacity,
            )  # configurable box alpha (0 = fully transparent)
            if reader.subtitle_language == SECOND_LANG or reader._sub_pending is not None:
                sr = render_plain_subtitle(
                    reader.sub_text, reader.osd[0], size=reader.sub_size, background=background
                )
            else:
                annotated = reader.annotation_mode == "full" or reader._annotation_hover
                # A phrase span highlights [start, end) — start can precede the hovered token (a leading
                # お in お休み), so it drives the underline, not reader.hover.
                span = reader._hover_span if annotated else None
                sr = render_subtitle(
                    reader.lines,
                    reader.osd[0],
                    size=reader.sub_size,
                    hover=span[0]
                    if span
                    else (reader.hover if annotated and reader.hover >= 0 else None),
                    hover_end=span[1] if span else None,
                    styles=reader.styles if annotated else None,
                    background=background,
                )
        reader.boxes = sr.boxes
        ox = (reader.osd[0] - sr.image.width) // 2
        oy = reader.osd[1] - sr.image.height - reader.bottom_margin
        reader.sub_origin = (ox, oy)
        if not reader._first_sub_logged:
            reader._first_sub_logged = True
            log.info(
                "first subtitle drawn (%dx%d at %d,%d)", sr.image.width, sr.image.height, ox, oy
            )
            from saitenka.app.loading import clear_startup_hint

            clear_startup_hint(reader.ipc)  # overlay is live now → drop mpv's startup breadcrumb
        return reader.ov.show(sr.image, ox, oy, oid=SUB_ID)

    def clear(self, reader: Reader) -> None:
        reader.ov.hide(SUB_ID)


class NullRenderer:
    """No-op draw strategy: run the reader's hover/nav/prefetch logic without rasterizing."""

    def draw(self, reader: Reader) -> None:
        pass

    def clear(self, reader: Reader) -> None:
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
        self._retry_due: float | None = None
        self._retry_effect_id: int | None = None
        self._clock = clock

    @property
    def ownership_state(self) -> OwnershipState:
        return self._state

    @staticmethod
    def _reply_accepted(reply: object) -> bool:
        return not isinstance(reply, dict) or reply.get("error") in {None, "success"}

    def _read_visibility(self, reader: Reader) -> Visibility:
        try:
            reply = reader.ipc.command("get_property", "sub-visibility")
        except Exception:  # noqa: BLE001  # an unreadable boundary is unknown, never legacy proof
            return Visibility.UNKNOWN
        if not isinstance(reply, dict) or reply.get("error") not in {None, "success"}:
            return Visibility.UNKNOWN
        value = reply.get("data")
        if value is True:
            return Visibility.TRUE
        if value is False:
            return Visibility.FALSE
        return Visibility.UNKNOWN

    def _trace_ownership(
        self,
        event: str,
        *,
        owner_before: PixelOwner,
        accepted: bool | None = None,
        visibility: Visibility | None = None,
        effect_id: int | None = None,
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

    def _assert_native(self, reader: Reader, action: OwnershipAction) -> None:
        owner_before = self._state.owner
        exhausted_before = self._state.retry_exhausted
        self._capture_restore_visibility(reader)
        reply, accepted = self._set_native_visible(reader)
        visibility = self._read_visibility(reader)
        followups = self._apply_assertion_result(action, visibility)
        self._record_assertion_result(
            action,
            reply=reply,
            accepted=accepted,
            visibility=visibility,
            owner_before=owner_before,
            exhausted_before=exhausted_before,
        )
        self._execute(reader, followups)

    def _capture_restore_visibility(self, reader: Reader) -> None:
        if self._restore_visibility is not None:
            return
        initial = self._read_visibility(reader)
        if initial != Visibility.UNKNOWN:
            self._restore_visibility = initial == Visibility.TRUE

    def _set_native_visible(self, reader: Reader) -> tuple[object, bool]:
        try:
            reply = reader.ipc.command(
                "set_property",
                "sub-visibility",
                True,  # noqa: FBT003  # mpv IPC wire value
            )
        except Exception as error:  # noqa: BLE001  # readback below decides ownership
            reply = {"error": f"{type(error).__name__}: {error}"}
        return reply, self._reply_accepted(reply)

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
    ) -> None:
        self._trace_ownership(
            "native-visibility-assertion",
            owner_before=owner_before,
            accepted=accepted,
            visibility=visibility,
            effect_id=action.effect_id,
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
        owner_before = self._state.owner
        try:
            staged = self._reply_accepted(self._fallback.draw(reader))
            accepted = staged and (
                self._state.visibility == Visibility.FALSE or self._hide_mpv_subtitles(reader)
            )
        except Exception:  # noqa: BLE001  # rollback preserves the last confirmed surface
            accepted = False
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

    def _hide_mpv_subtitles(self, reader: Reader) -> bool:
        reply = reader.ipc.command(
            "set_property",
            "sub-visibility",
            False,  # noqa: FBT003  # mpv IPC wire value
        )
        return self._reply_accepted(reply)

    @staticmethod
    def _record_catastrophic_fallback() -> None:
        if otel_metrics.subtitle_pixel_catastrophic_fallbacks is not None:
            otel_metrics.subtitle_pixel_catastrophic_fallbacks.add(1)
        log.critical(
            "native subtitle pixels confirmed absent; committed catastrophic legacy recovery"
        )

    def _execute(self, reader: Reader, actions: tuple[OwnershipAction, ...]) -> None:
        for action in actions:
            if action.kind == ActionKind.ASSERT_NATIVE_VISIBILITY:
                self._assert_native(reader, action)
            elif action.kind == ActionKind.CLEAR_LEGACY:
                self._fallback.clear(reader)
            elif action.kind == ActionKind.CLEAR_INTERACTION:
                self._hide_focus(reader)
            elif action.kind in {ActionKind.STAGE_LEGACY, ActionKind.RESTAGE_LEGACY}:
                self._stage_legacy(reader, action)
            elif action.kind == ActionKind.SHOW_MPV:
                reader.ipc.command(
                    "set_property",
                    "sub-visibility",
                    True,  # noqa: FBT003  # mpv IPC wire value
                )
            elif action.kind == ActionKind.SCHEDULE_RETRY:
                self._retry_effect_id = action.effect_id
                self._retry_due = self._clock() + (action.delay_ms or 0) / 1_000
            elif action.kind == ActionKind.CANCEL_RETRY:
                self._retry_effect_id = None
                self._retry_due = None
            elif action.kind == ActionKind.RESTORE_VISIBILITY:
                restore = True if self._restore_visibility is None else self._restore_visibility
                reader.ipc.command("set_property", "sub-visibility", restore)

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

    def poll(self, reader: Reader) -> None:
        if self._retry_due is None or self._clock() < self._retry_due:
            return
        effect_id = self._retry_effect_id
        self._retry_due = None
        self._retry_effect_id = None
        self._state, actions = reduce_ownership(
            self._state,
            OwnershipEvent(
                EventKind.RETRY_DUE,
                context=self._state.context,
                effect_id=effect_id,
            ),
        )
        self._execute(reader, actions)

    def draw(self, reader: Reader) -> None:
        if self._state.owner == PixelOwner.LEGACY:
            self._fallback.draw(reader)
            return
        if not self.activate(reader):
            return
        if reader.hover < 0 or box_for_token(reader.boxes, reader.hover) is None:
            self._hide_focus(reader)
            return
        span = reader._hover_span or (reader.hover, reader.hover + 1)
        selected = [box for box in reader.boxes if span[0] <= box.index < span[1]]
        if not selected:
            self._hide_focus(reader)
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
        reader.ipc.command(
            "osd-overlay",
            NATIVE_FOCUS_ID,
            "ass-events",
            drawing,
            reader.osd[0],
            reader.osd[1],
            1,
        )

    def clear(self, reader: Reader) -> None:
        self._hide_focus(reader)
        self._fallback.clear(reader)

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
        self._hide_focus(reader)
        self._fallback.clear(reader)
        reader.ipc.command(
            "set_property",
            "sub-visibility",
            True,  # noqa: FBT003  # mpv IPC wire value
        )

    def resume_after_overlay(self, reader: Reader) -> None:
        if self._state.owner == PixelOwner.LEGACY:
            self._state, actions = reduce_ownership(
                self._state, OwnershipEvent(EventKind.LEGACY_REHANDOFF)
            )
            self._execute(reader, actions)
            return
        self.reassert(reader)

    @staticmethod
    def _hide_focus(reader: Reader) -> None:
        reader.ipc.command("osd-overlay", NATIVE_FOCUS_ID, "none", "")
