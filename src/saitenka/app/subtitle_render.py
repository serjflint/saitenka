"""The subtitle raster, behind an injectable strategy so tests can run the reader loop headless.

``Reader.renderer`` holds a :class:`SubtitleRenderer` (the real blit); pass :class:`NullRenderer` to
suppress the raster and assert state only — the public seam that replaces monkeypatching the private
``_draw_subtitle`` (#50). The strategy takes the ``Reader`` as its host, matching the collaborator
pattern the other app modules use.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.languages import SECOND_LANG
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitles import render_plain_subtitle, render_subtitle

if TYPE_CHECKING:
    from saitenka.app.controller import Reader

log = logging.getLogger("saitenka.app.subtitle_render")

SUB_ID = OverlayId.SUB
NATIVE_FOCUS_ID = 1_001


class SubtitleRenderer:
    """Rasterize the current cue and blit it as the SUB overlay — the real draw path."""

    def draw(self, reader: Reader) -> None:
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
        reader.ov.show(sr.image, ox, oy, oid=SUB_ID)

    def clear(self, reader: Reader) -> None:
        reader.ov.hide(SUB_ID)


class NullRenderer:
    """No-op draw strategy: run the reader's hover/nav/prefetch logic without rasterizing."""

    def draw(self, reader: Reader) -> None:
        pass

    def clear(self, reader: Reader) -> None:
        pass


class NativeVisibleRenderer:
    """Use mpv pixels only while matching interaction geometry is available."""

    def __init__(self, fallback: SubtitleRenderer | None = None) -> None:
        self._fallback = fallback or SubtitleRenderer()
        self._native_ready = False
        self._visibility: bool | None = None
        self._activation_failure_reported = False

    def activate(self, reader: Reader) -> bool:
        visible = self._native_ready
        try:
            reply = reader.ipc.command("set_property", "sub-visibility", visible)
        except Exception as error:  # noqa: BLE001  # optional renderer must restore legacy drawing
            reply = {"error": f"{type(error).__name__}: {error}"}
        accepted = not isinstance(reply, dict) or reply.get("error") in {None, "success"}
        if accepted:
            self._visibility = visible
            self._activation_failure_reported = False
        else:
            self._visibility = None
            if visible:
                self._native_ready = False
            if not self._activation_failure_reported:
                self._activation_failure_reported = True
                failure = reply.get("error") if isinstance(reply, dict) else "unknown"
                log.warning("mpv rejected subtitle visibility change: %s", failure)
        return accepted

    def use_fallback(self, reader: Reader) -> None:
        self._native_ready = False
        self._hide_focus(reader)
        if self._visibility is not False:
            self.activate(reader)

    def use_native(self, reader: Reader) -> bool:
        self._fallback.clear(reader)
        self._native_ready = True
        if self.activate(reader):
            return True
        self._native_ready = False
        return False

    def draw(self, reader: Reader) -> None:
        if not self._native_ready:
            if self._visibility is not False:
                self.activate(reader)
            self._fallback.draw(reader)
            return
        if self._visibility is not True and not self.activate(reader):
            self._native_ready = False
            self._fallback.draw(reader)
            self._hide_focus(reader)
            return
        if reader.hover < 0 or reader.hover >= len(reader.boxes):
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

    @staticmethod
    def _hide_focus(reader: Reader) -> None:
        reader.ipc.command("osd-overlay", NATIVE_FOCUS_ID, "none", "")
