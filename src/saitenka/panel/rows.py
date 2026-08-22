"""Turn dictionary entries into deferred panel rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.draw.icon_source import Icon, render_icon
from saitenka.model import (
    _DEFAULT_THEME,
    RGBA,
    LinkBox,
    ScanBox,
    Span,
    Style,
    Theme,
    is_ideograph,
)
from saitenka.panel.body import (
    BodyRenderArgs,
    LaidOutBody,
    layout_body_block,
    raster_body_window,
    render_body_block,
)
from saitenka.render.chip import ChipStyle
from saitenka.render.document import GUTTER_PX, INDENT_PX
from saitenka.render.flow import ChipBox, ImgBox, render_chip_row, render_flow
from saitenka.render.layout import Block as FlowBlock
from saitenka.render.sc_adapter import inline_flow

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL import Image

    from saitenka.panel.model import Definition, Entry, EntryGroup


def _headword_kanji_links(scan: list[ScanBox]) -> list[LinkBox]:
    """One ``LinkBox('kanji:<ch>')`` per headword ideograph at its glyph rect, so clicking a kanji in
    the headword opens its kanji entry (Yomitan parity). Astral-safe (#99): 𠮟 U+20B9F counts."""
    return [
        LinkBox(f"kanji:{sb.text[0]}", sb.x, sb.y, sb.w, sb.h)
        for sb in scan
        if sb.text and is_ideograph(sb.text[0])
    ]


def _flow_row(
    flow,
    content_w: int,
    scale: float = 1.35,
    *,
    render_scale: float = 1.0,
    scan_out: list[ScanBox] | None = None,
) -> Image.Image:
    # ``scale`` is the LINE-HEIGHT scale (a layout knob); ``render_scale`` > 1 is the display factor
    # that rasters the row NATIVELY (crisp non-body rows for the scale-boundary arch). ``scan_out``
    # collects per-CJK-char hitboxes (1× coords, scale-independent) — the header uses them for kanji links.
    return render_flow(
        flow,
        FlowBlock(width=content_w, padding=0, line_height_scale=scale, background=(0, 0, 0, 0)),
        scan_out=scan_out,
        scale=render_scale,
    )


# Inflection-chain chips: same green as the dot marker, so the marker and its chips read as one unit.
INFLECTION_BG: RGBA = (91, 191, 106, 255)

# Header top-right icon strip: [ ⊕ add ][gap][ 🔊 speaker ]. Kept as constants so the drawing and the
# click hit-test (tooltip.hit_header_add) agree on one geometry.
_SPK_SIZE = 30
_ADD_SIZE = 26
_ICON_TOP = 8
_ICON_GAP = 10


def header_add_rect(
    width: int, theme: Theme = _DEFAULT_THEME, *, speak_button: bool = True
) -> tuple[int, int, int, int]:
    """Panel-space (x, y, w, h) of the header ⊕ add-to-Anki button. Sits just left of the 🔊 speaker
    when it's shown, else takes the speaker's rightmost slot (so hiding TTS doesn't leave a gap)."""
    spk, add, gap, top = (
        theme.px(_SPK_SIZE),
        theme.px(_ADD_SIZE),
        theme.px(_ICON_GAP),
        theme.px(_ICON_TOP),
    )
    content_w = width - 2 * theme.margin
    right = content_w - (spk + gap if speak_button else 0)
    x = theme.margin + right - add
    y = theme.margin + top + theme.px(2)
    return (x, y, add, add)


def header_speaker_rect(width: int, theme: Theme = _DEFAULT_THEME) -> tuple[int, int, int, int]:
    """Panel-space (x, y, w, h) of the header 🔊 speaker button — the only click target that plays audio."""
    spk, top = theme.px(_SPK_SIZE), theme.px(_ICON_TOP)
    content_w = width - 2 * theme.margin
    x = theme.margin + content_w - spk
    y = theme.margin + top
    return (x, y, spk, spk)


@dataclass
class Row:
    """One panel row: its x-offset and a deferred thunk that renders it on demand.

    The thunk returns ``(image, scan_boxes, link_boxes)`` — the row image plus per-character
    :class:`ScanBox`es (nested scanning) and per-link :class:`LinkBox`es (clickable cross-refs) in
    the *row image's* coordinate space (only def bodies carry any; every other row returns ``[]``).
    Cheap rows (header, tags, pills, def-name chips) are trivial; the expensive rows are the def
    *bodies* (SC-walk + document layout). Deferring the thunk is what lets a cold 6-dict tooltip
    paint its visible top first and finish the below-the-fold bodies in the background."""

    x: int
    # ``render(*, scale=1.0)`` — scale>1 rasters the row NATIVELY (crisp non-body; scale-boundary arch).
    render: Callable[..., tuple[Image.Image, list[ScanBox], list[LinkBox]]]
    gap: int | None = None  # trailing gap after this row (None = theme.gap); lets a split def body
    # keep its 3px inter-block spacing while other rows use the 7px row gap
    # The dictionary section this row STARTS (set on def-head rows) — the tab row and keyboard
    # section-nav derive their scroll targets from these.
    section: str | None = None
    # Optional bounded raster — ``render_capped(max_h)`` returns ``(image, scan, links, complete)``
    # where the image is only the strip that covers ``max_h`` px (complete=False when lines were
    # clipped). Only def-body rows carry it; a partial strip lets a pathologically tall single block
    # first-paint O(viewport) instead of O(block). The full ``render`` thunk stays the source of
    # truth for finish() so the composed panel is unchanged.
    render_capped: (
        Callable[[int], tuple[Image.Image, list[ScanBox], list[LinkBox], bool]] | None
    ) = None
    # Set only on def-body rows — lets a process-pool worker render this block from plain data
    # instead of the (unpicklable) closures above. See BodyRenderArgs/render_body_block.
    body_args: BodyRenderArgs | None = None
    # Windowed (banded) API — set only on def-body rows, both closing over ONE memoised
    # ``layout_body_block`` handle (walk + wrap once per row, then O(band) getmask2 rasters). ``measure``
    # returns the row's full pixel height without any raster (seeds the scroll offset table);
    # ``render_window(y0, y1)`` rasters just the band ``[y0, y1)`` (image + row-local scan/link boxes in
    # band space). Non-body rows are small (one band, never split) and keep only ``render``.
    measure: Callable[[], int] | None = None
    # ``render_window(y0, y1, *, scale=1.0)`` — scale>1 rasters the band natively (scale-boundary arch).
    render_window: Callable[..., tuple[Image.Image, list[ScanBox], list[LinkBox]]] | None = None
    # Whole-row scan/link hitboxes (row-local) from the layout, no raster — lets the banded engine
    # retain a MEASURED-but-not-yet-rastered row's geometry (a scroll-jump to the bottom keeps the top
    # hoverable). Set on def-body rows alongside ``measure``; both share the memoised layout handle.
    geometry: Callable[[], tuple[list[ScanBox], list[LinkBox]]] | None = None


def _emit_def_rows(
    rows: list[Row],
    defs: list[Definition],
    content_w: int,
    m: int,
    theme: Theme,
    *,
    sectioned: bool,
) -> None:
    """Numbered per-dictionary definitions: a cheap def-name chip head row + one deferred def-BODY row
    each. Shared by the fused single-header panel and each stacked group. ``sectioned`` keys the
    def-head on its dict name for tabs/keyboard-nav (fused layout); stacked groups carry the section on
    their group-head instead, so their def-heads pass ``sectioned=False``."""
    body_style = Style(size=theme.px(23), color=theme.text)
    for i, d in enumerate(defs, 1):

        def _def_head(i=i, d=d, *, scale: float = 1.0):
            dh: list = [Span(f"{i}. ", Style(size=theme.px(20), weight=700, color=theme.text))]
            for tag in d.tags:  # defTag pills: ★ / priority form
                dh.append(ChipBox(tag, ChipStyle(size=theme.px(18), weight=600, bg=theme.tag)))
                dh.append(Span(" ", Style(size=theme.px(19))))
            dh.append(ChipBox(d.dict_name, ChipStyle(size=theme.px(19), bg=theme.purple)))
            return _flow_row(dh, content_w, 1.7, render_scale=scale), [], []

        rows.append(Row(m, _def_head, section=d.dict_name if sectioned else None))

        # ONE row per def body, fully deferred: the SC-walk itself is NOT cheap for pathological
        # entries (a 取る-class def walks in 200+ ms), so both the walk AND the rasterisation live
        # inside the thunk — building rows costs nothing, and the head only walks/rasters the defs the
        # viewport actually shows. ``render_capped`` bounds the raster mid-def (block budget + mid-block
        # line clip via render_document/render_flow max_height) so cold first paint is O(viewport) even
        # when the first visible def body is enormous. render_document stacks the walked blocks with the
        # same 3px inter-block gap, so the composed full panel is byte-identical.
        body_args = BodyRenderArgs(
            content=d.content,
            body_style=body_style,
            body_w=content_w - theme.body_indent,
            gap_px=theme.px(3),
            indent_px=theme.px(INDENT_PX),
            gutter_px=theme.px(GUTTER_PX),
            media=d.media,
        )

        def _def_body(args: BodyRenderArgs):  # explicit param — no loop-variable closure (B023)
            # One memoised layout handle per row: the walk + wrap runs at most once (on the first
            # measure/window call), then every band raster reuses it — the O(band)-not-O(block) crux.
            # ``render`` stays the full-body source of truth (golden / finish / process pool).
            laid: list[LaidOutBody] = []

            def _laid() -> LaidOutBody:
                if not laid:
                    laid.append(layout_body_block(args))
                return laid[0]

            def thunk():
                img, scan, links, _complete = render_body_block(args)
                return img, scan, links

            def capped(max_h: int):
                return render_body_block(args, max_h)

            def measure() -> int:
                return _laid().full_height

            def window(y0: int, y1: int, *, scale: float = 1.0):
                return raster_body_window(_laid(), y0, y1, scale=scale)

            def geometry():
                return _laid().geometry()

            return thunk, capped, measure, window, geometry

        body_thunk, body_capped, body_measure, body_window, body_geometry = _def_body(body_args)
        rows.append(
            Row(
                m + theme.body_indent,
                body_thunk,
                render_capped=body_capped,
                body_args=body_args,
                measure=body_measure,
                render_window=body_window,
                geometry=body_geometry,
            )
        )


def _emit_group_rows(
    rows: list[Row],
    groups: list[EntryGroup],
    content_w: int,
    m: int,
    theme: Theme,
    *,
    add_button: bool,
    group_mined: tuple[bool, ...],
) -> None:
    """Yomitan-style stacked entries: one block per :class:`EntryGroup` — a ruby'd headword row with
    its own ⊕ (emitted as a ``LinkBox('mine:<card_index>')`` so it rides the existing link hit-test in
    both render paths), followed by that group's per-dict definitions. The group-head row carries the
    tab/keyboard-nav ``section`` (per reading); its def-heads don't."""
    for gi, g in enumerate(groups):
        mined = gi < len(group_mined) and group_mined[gi]

        def _group_head(g=g, mined=mined, *, scale: float = 1.0):
            flow: list = [
                Span("・ ", Style(size=theme.px(30), color=theme.accent)),
                *inline_flow(g.headword, Style(size=theme.px(30), weight=700, color=theme.text)),
            ]
            img = _flow_row(flow, content_w, render_scale=scale)
            links: list[LinkBox] = []
            if add_button:
                add = theme.px(_ADD_SIZE)
                btn = render_icon(Icon.MINED if mined else Icon.ADD, round(add * scale))
                bx, by = content_w - add, theme.px(2)  # LinkBox stays REFERENCE px (hit geometry)
                img.alpha_composite(btn, (round(bx * scale), round(by * scale)))
                links.append(LinkBox(f"mine:{g.card_index}", bx, by, add, add))
            return img, [], links

        rows.append(Row(m, _group_head, section=g.reading))
        _emit_def_rows(rows, g.defs, content_w, m, theme, sectioned=False)


def panel_rows(
    entry: Entry,
    width: int = 384,
    theme: Theme = _DEFAULT_THEME,
    *,
    add_button: bool = False,
    mined: bool = False,
    speak_button: bool = True,
    group_mined: tuple[bool, ...] = (),
) -> list[Row]:
    """Build the panel's rows as deferred thunks (same order/content as ``render_panel``).

    ``add_button`` draws the header add-to-Anki button (only when mining is available); ``mined`` makes
    it a ✓ instead of ⊕ for a word already in the deck. ``speak_button`` draws the 🔊 TTS button — set
    False to hide it when no Japanese TTS voice is installed (it would silently do nothing). When
    ``entry.groups`` is set (Yomitan-style stacked entries), the fused header ⊕ is suppressed and each
    group gets its own ⊕ (``group_mined[i]`` → ✓ for an already-mined group). Defaults keep
    ``render_panel`` and its golden unchanged."""
    m = theme.margin
    content_w = width - 2 * m
    header_add = add_button and not entry.groups  # groups carry their own per-entry ⊕
    rows: list[Row] = []

    # --- header: ▶ + big ruby headword, ⊕/✓ add + 🔊 speaker top-right ---
    def _header(*, scale: float = 1.0) -> tuple[Image.Image, list[ScanBox], list[LinkBox]]:
        # The numbered stroke-order glyph needs room for its stroke numbers to be legible, so draw it 2×
        # the normal headword size when that font is active (plain headwords stay at the reference size).
        # Branchless (bool→0/1) so it adds no cognitive complexity to the already-ceilinged panel_rows.
        hw_size = theme.px(46 * (1 + bool(entry.headword_font)))
        hw = [
            Span("▶", Style(size=theme.px(28), color=theme.accent)),
            Span(" ", Style(size=hw_size)),
        ]
        hw += inline_flow(
            entry.headword,
            Style(size=hw_size, weight=700, color=theme.text, font=entry.headword_font),
        )
        # Collect per-CJK-char hitboxes (1× coords) so each headword kanji becomes a click-to-open link
        # (Yomitan parity): clicking 勉 in 勉強 opens its kanji entry, which the header couldn't do before.
        scan: list[ScanBox] = []
        hdr = _flow_row(
            hw, content_w, render_scale=scale, scan_out=scan
        )  # native big ruby at scale
        kanji_links = _headword_kanji_links(scan)
        right = content_w
        top = theme.px(_ICON_TOP)
        if speak_button:  # icons render at native size, composited at scaled positions
            sz = theme.px(_SPK_SIZE)
            spk = render_icon(Icon.SPEAKER, round(sz * scale))
            hdr.alpha_composite(spk, (round((right - sz) * scale), round(top * scale)))
            right -= sz + theme.px(_ICON_GAP)
        if header_add:
            add = theme.px(_ADD_SIZE)
            btn = render_icon(Icon.MINED if mined else Icon.ADD, round(add * scale))
            hdr.alpha_composite(
                btn, (round((right - add) * scale), round((top + theme.px(2)) * scale))
            )
        return hdr, [], kanji_links

    rows.append(Row(m, _header))

    # --- pitch-accent graphs: one compact graph per distinct accent, in the header area next to
    # the reading; the purple text pill in the freq row stays as the fallback ---
    if entry.pitches:

        def _pitch_row(pitches=tuple(entry.pitches), *, scale: float = 1.0):
            from saitenka.draw.pitch import render_pitch_graph

            flow: list = []
            for reading, accents in pitches:
                for pa in accents:
                    g = render_pitch_graph(reading, pa, scale=theme.scale)
                    if flow:
                        flow.append(Span("  ", Style(size=theme.px(20))))
                    flow.append(
                        ImgBox(width=g.width, height=g.height, sprite=g, baseline_drop=theme.px(4))
                    )
            return _flow_row(flow, content_w, 1.5, render_scale=scale), [], []

        rows.append(Row(m, _pitch_row))

    # --- inflection chain: dot marker + one chip per Yomitan transform name (● [-て][-いる][-た]) ---
    if entry.inflection_chain:

        def _chain(chain=tuple(entry.inflection_chain), *, scale: float = 1.0):
            pz = theme.px(18)
            cflow: list = [
                ImgBox(
                    width=pz,
                    height=pz,
                    sprite=render_icon(Icon.MARKER, pz),
                    baseline_drop=theme.px(3),
                ),
                Span("  ", Style(size=theme.px(20))),
            ]
            for i, name in enumerate(chain):
                if i:
                    cflow.append(Span("›", Style(size=theme.px(18), color=theme.muted)))
                cflow.append(
                    ChipBox(name, ChipStyle(size=theme.px(18), weight=600, bg=INFLECTION_BG))
                )
            return _flow_row(cflow, content_w, 1.7, render_scale=scale), [], []

        rows.append(Row(m, _chain))

    # --- grammar tags: dot marker + muted text ---
    for tag in entry.tags:

        def _tag(tag=tag, *, scale: float = 1.0):
            pz = theme.px(18)
            tflow = [
                ImgBox(
                    width=pz,
                    height=pz,
                    sprite=render_icon(Icon.MARKER, pz),
                    baseline_drop=theme.px(3),
                ),
                Span("  " + tag, Style(size=theme.px(20), color=theme.muted)),
            ]
            return _flow_row(tflow, content_w, render_scale=scale), [], []

        rows.append(Row(m, _tag))

    # --- frequency pills: two-tone (colored name + light value), SubMiner-style ---
    if entry.freqs:

        def _freqs(freqs=tuple(entry.freqs), *, scale: float = 1.0):
            # freq pills are secondary signal → render a notch smaller than the def pills (px19) and
            # body (px23), so more fit on the row and they don't compete with the readings. Laid out
            # through the 2-D seam (solve_row): a uniform gap that wraps to width, replacing the old
            # "  "-space separators (whose gap drifted with the font metric + left a trailing line).
            chips = [
                ChipBox(f.name, ChipStyle(size=theme.px(16), weight=600, bg=f.color, value=f.value))
                for f in freqs
            ]
            return render_chip_row(chips, theme.px(8), content_w, scale=scale), [], []

        rows.append(Row(m, _freqs))

    # --- reading label (dict-name pill + reading, e.g. よむ[1]) ---
    if entry.reading_label:

        def _reading(rl=entry.reading_label, *, scale: float = 1.0):
            dn, txt = rl
            flow = [
                ChipBox(dn, ChipStyle(size=theme.px(19), bg=theme.purple)),
                Span("  " + txt, Style(size=theme.px(20), color=theme.text)),
            ]
            return _flow_row(flow, content_w, 1.7, render_scale=scale), [], []

        rows.append(Row(m, _reading))

    # --- numbered definitions --- (def-name chip row is cheap; the body row is the expensive one)
    if entry.groups:
        _emit_group_rows(
            rows, entry.groups, content_w, m, theme, add_button=add_button, group_mined=group_mined
        )
    else:
        _emit_def_rows(rows, entry.defs, content_w, m, theme, sectioned=True)

    return rows
