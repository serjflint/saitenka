"""The subtitle raster, behind an injectable strategy so tests can run the reader loop headless.

``Reader.renderer`` holds a :class:`SubtitleRenderer` (the real blit); pass :class:`NullRenderer` to
suppress the raster and assert state only — the public seam that replaces monkeypatching the private
``_draw_subtitle`` (#50). The strategy takes the ``Reader`` as its host, matching the collaborator
pattern the other app modules use.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from overlay import otel_metrics
from overlay.app.languages import SECOND_LANG
from overlay.app.overlay_ids import OverlayId
from overlay.app.subtitles import render_plain_subtitle, render_subtitle

if TYPE_CHECKING:
    from overlay.app.controller import Reader

log = logging.getLogger("overlay.app.subtitle_render")

SUB_ID = OverlayId.SUB


class SubtitleRenderer:
    """Rasterize the current cue and blit it as the SUB overlay — the real draw path."""

    def draw(self, reader: Reader) -> None:
        with otel_metrics.instrumented(otel_metrics.subtitle_render_duration_ms, "subtitle_render"):
            # Draw plain for the known/translation track, OR a cue still awaiting a complete
            # tokenization (dictionaries loading): the cue shows at cue time and reader_deps
            # re-renders it annotated once deps land.
            if reader.subtitle_language == SECOND_LANG or reader._sub_pending is not None:
                sr = render_plain_subtitle(reader.sub_text, reader.osd[0], size=reader.sub_size)
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
            from overlay.app.loading import clear_startup_hint

            clear_startup_hint(reader.ipc)  # overlay is live now → drop mpv's startup breadcrumb
        reader.ov.show(sr.image, ox, oy, oid=SUB_ID)


class NullRenderer:
    """No-op draw strategy: run the reader's hover/nav/prefetch logic without rasterizing."""

    def draw(self, reader: Reader) -> None:
        pass
