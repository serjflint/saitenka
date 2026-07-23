"""Translation reveal: an English secondary subtitle track shown as its own overlay, either manually
toggled (``t``) or auto-revealed while a tooltip is up (``auto_translate`` opt-in).

Takes ``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from overlay.app.overlay_ids import OverlayId
from overlay.model import Span, Style
from overlay.render.flow import render_flow
from overlay.render.layout import Block, inline_width

if TYPE_CHECKING:
    from overlay.app.controller import Reader

EN_LANGS = {"en", "eng", "en-us", "en-gb", "eng-us", "english"}


def setup_secondary(reader: Reader) -> int | None:
    tracks = [t for t in (reader._get("track-list") or []) if t.get("type") == "sub"]
    primary = reader._get("sid")
    # prefer an English-tagged track; else any other sub track (generated demo subs carry no lang)
    pick = next((t for t in tracks if (t.get("lang") or "").lower() in EN_LANGS), None)
    if pick is None:
        pick = next((t for t in tracks if t.get("id") != primary), None)
    if pick is None:
        return None
    reader.ipc.command("set_property", "secondary-sid", pick["id"])
    reader.ipc.command("set_property", "secondary-sub-visibility", False)
    return pick["id"]


def translation_visible(reader: Reader) -> bool:
    """Should the EN translation be shown now? Manual toggle (`t`), OR auto-reveal while a tooltip is
    up (auto-translate opt-in)."""
    return reader._translate_on or (reader.auto_translate and reader.hover >= 0)


def sync_auto_translation(reader: Reader) -> None:
    """Reconcile the auto-translation overlay with the hover state (only when opted in)."""
    if not reader.auto_translate:
        return
    if translation_visible(reader):
        draw_translation(reader)
    elif not reader._translate_on:
        reader.ov.hide(OverlayId.TRANS)
        reader._trans_text = None


def toggle_translation(reader: Reader) -> None:
    reader._translate_on = not reader._translate_on
    if translation_visible(reader):
        draw_translation(reader)
    else:
        reader.ov.hide(OverlayId.TRANS)
        reader._trans_text = None


def secondary_text(reader: Reader) -> str:
    return (reader._prop("secondary-sub-text") or "").replace("\\N", " ").replace("\n", " ").strip()


def draw_translation(reader: Reader) -> None:
    text = secondary_text(reader)
    reader._trans_text = text
    if not text:
        reader.ov.hide(OverlayId.TRANS)
        return
    size = max(20, round(reader.osd[1] * 0.032))
    style = Style(size=size, color=(220, 224, 235, 255))
    pad = 14
    # trim the box to the text (wrap only if it exceeds 80% of the width), then centre it
    box_w = min(round(inline_width([Span(text, style)])) + 2 * pad, int(reader.osd[0] * 0.8))
    flow = render_flow(
        [Span(text, style)], Block(width=box_w, padding=pad, background=(0, 0, 0, 170))
    )
    x = (reader.osd[0] - flow.width) // 2
    # top of the screen (SubMiner-style) — separate from the JP subs at the bottom, and clear of the
    # tooltip that anchors above the hovered word.
    y = max(8, round(reader.osd[1] * 0.035))
    reader.ov.show(flow, x, y, oid=OverlayId.TRANS)
