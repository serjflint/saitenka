"""``render_panel`` — compose a full Yomitan-style dictionary entry into one RGBA image.

Assembles the chrome primitives (chips, bordered labels, list markers, icons) around walked
structured-content, reproducing the real 読む popup: big ruby headword + speaker, grammar tags,
frequency pills, dictionary-name pills, and numbered definitions with ruby'd examples. This is the
image the controller composites over mpv video in a single surface.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from overlay.body_block import (
    BodyRenderArgs,
    LaidOutBody,
    SCNode,
    layout_body_block,
    raster_body_window,
    render_body_block,
)
from overlay.draw.icon_source import Icon, render_icon
from overlay.model import _DEFAULT_THEME, RGBA, LinkBox, ScanBox, Span, Style, Theme
from overlay.parallel import shared_executor
from overlay.render.chip import ChipStyle
from overlay.render.document import GUTTER_PX, INDENT_PX
from overlay.render.flow import ChipBox, ImgBox, render_flow
from overlay.render.layout import Block as FlowBlock
from overlay.sc.walk import inline_flow

if TYPE_CHECKING:
    from collections.abc import Callable

# Theme + _DEFAULT_THEME moved to overlay.model (value types, no render deps) to break the
# render↔panel cycle; re-exported here so ``from overlay.panel import Theme`` keeps working.
# BodyRenderArgs/SCNode/render_body_block live in the TOP-LEVEL overlay.body_block for the same
# reason (render/banded.py needs render_body_block; sc.walk already imports from render.flow, so a
# copy inside the render/ package would cycle); re-exported here so ``from overlay.panel import
# BodyRenderArgs`` keeps working.


@dataclass
class Freq:
    name: str
    value: str
    color: RGBA


@dataclass
class Definition:
    dict_name: str
    content: SCNode  # structured-content node
    tags: list[str] = field(default_factory=list)  # defTags: ★, priority form, …


@dataclass
class EntryGroup:
    """One Yomitan-style stacked entry: a distinct (term, reading) with its own ruby'd headword and
    per-dictionary definitions, drawn as its own block with its own ⊕ mine button. ``card_index``
    indexes ``DictionarySet.cards_for(token)`` so the button mines exactly this entry."""

    headword: object  # structured-content node (ruby'd)
    reading: str
    defs: list[Definition] = field(default_factory=list)
    card_index: int = 0


@dataclass
class Entry:
    headword: object  # structured-content node (ruby'd)
    tags: list[str] = field(default_factory=list)
    freqs: list[Freq] = field(default_factory=list)
    reading_label: tuple[str, str] | None = None  # (dict_name, text)
    defs: list[Definition] = field(default_factory=list)
    inflection_chain: list[str] = field(default_factory=list)  # 🧩 -て « -いる « -た
    reading: str = ""  # dictionary-form kana reading (for TTS: 習う → ならう, not ならわ)
    # Distinct pitch accents as (reading, positions) — drawn as compact graphs in a header-area row;
    # the purple text pill in the freq row stays as the compact fallback.
    pitches: list[tuple[str, tuple[int, ...]]] = field(default_factory=list)
    # Yomitan-style stacked entries: when a headword has ≥2 distinct readings (退く = のく / しりぞく),
    # one EntryGroup per reading, each rendered as its own block with its own ⊕. Empty for the common
    # single-entry case — the fused header path above is unchanged (goldens preserved).
    groups: list[EntryGroup] = field(default_factory=list)


def _hex(s: str) -> RGBA:
    from overlay.sc.walk import _parse_color

    return _parse_color(s, (90, 122, 160, 255))


def _load_defs(items: list) -> list[Definition]:
    return [Definition(d["dict"], d["content"], tags=d.get("tags", [])) for d in items]


def load_entry(path: str | Path) -> Entry:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Entry(
        headword=data["headword"],
        tags=[t["text"] for t in data.get("tags", [])],
        freqs=[Freq(f["name"], f["value"], _hex(f["color"])) for f in data.get("freqs", [])],
        reading_label=(
            tuple(data["reading_label"].values()) if data.get("reading_label") else None
        ),
        defs=_load_defs(data.get("defs", [])),
        reading=data.get("reading", ""),
        # Yomitan-style stacked entries (退く = のく / しりぞく): one block per reading, each with its
        # own ruby'd headword + ⊕. Absent in single-entry fixtures → the fused header path.
        groups=[
            EntryGroup(
                headword=g["headword"],
                reading=g.get("reading", ""),
                defs=_load_defs(g.get("defs", [])),
                card_index=g.get("card_index", i),
            )
            for i, g in enumerate(data.get("groups", []))
        ],
    )


def _flow_row(
    flow, content_w: int, scale: float = 1.35, *, render_scale: float = 1.0
) -> Image.Image:
    # ``scale`` is the LINE-HEIGHT scale (a layout knob); ``render_scale`` > 1 is the display factor
    # that rasters the row NATIVELY (crisp non-body rows for the scale-boundary arch).
    return render_flow(
        flow,
        FlowBlock(width=content_w, padding=0, line_height_scale=scale, background=(0, 0, 0, 0)),
        scale=render_scale,
    )


# Inflection-chain chips: same green as the dot marker, so the marker and its chips read as one unit.
INFLECTION_BG: RGBA = (91, 191, 106, 255)

# Header top-right icon strip: [ ⊕ add ][gap][ 🔊 speaker ]. Kept as constants so the drawing and the
# click hit-test (controller._hit_header_add) agree on one geometry.
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
        hw = [
            Span("▶", Style(size=theme.px(28), color=theme.accent)),
            Span(" ", Style(size=theme.px(46))),
        ]
        hw += inline_flow(entry.headword, Style(size=theme.px(46), weight=700, color=theme.text))
        hdr = _flow_row(hw, content_w, render_scale=scale)  # native big ruby word at scale
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
        return hdr, [], []

    rows.append(Row(m, _header))

    # --- pitch-accent graphs: one compact graph per distinct accent, in the header area next to
    # the reading; the purple text pill in the freq row stays as the fallback ---
    if entry.pitches:

        def _pitch_row(pitches=tuple(entry.pitches), *, scale: float = 1.0):
            from overlay.draw.pitch import render_pitch_graph

            flow: list = []
            for reading, positions in pitches:
                for pos in positions:
                    g = render_pitch_graph(reading, pos, scale=theme.scale)
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
            fflow: list = []
            for f in freqs:
                # freq pills are secondary signal → render a notch smaller than the def pills (px19)
                # and body (px23), so more fit on the row and they don't compete with the readings.
                fflow.append(
                    ChipBox(
                        f.name, ChipStyle(size=theme.px(16), weight=600, bg=f.color, value=f.value)
                    )
                )
                fflow.append(Span("  ", Style(size=theme.px(16))))
            return _flow_row(fflow, content_w, 1.7, render_scale=scale), [], []

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


def compose_panel(
    rendered: list[tuple[int, Image.Image]],
    width: int,
    theme: Theme = _DEFAULT_THEME,
    gaps: list[int] | None = None,
    top_reserve: int = 0,
) -> Image.Image:
    """Stack already-rendered ``(x, image)`` rows into one canvas (the geometry ``render_panel`` uses).

    ``gaps[i]`` is the gap placed *after* row ``i`` (defaults to a uniform ``theme.gap``); only the
    ``n-1`` inter-row gaps add to the height. ``top_reserve`` leaves that many blank pixels above the
    first row — used to clear the sticky dict-tab strip so it never overlaps the header/reading."""
    m = theme.margin
    n = len(rendered)
    if gaps is None:
        gaps = [theme.gap] * n
    inter = sum(gaps[i] for i in range(n - 1)) if n > 1 else 0
    total_h = 2 * m + top_reserve + sum(im.height for _, im in rendered) + inter
    canvas = Image.new("RGBA", (width, max(total_h, 1)), theme.bg)
    y = m + top_reserve
    for i, (x, im) in enumerate(rendered):
        canvas.alpha_composite(im, (x, y))
        y += im.height + (gaps[i] if i < n - 1 else 0)
    return canvas


class LazyPanel:
    """Row-by-row, viewport-first panel. ``render_to(h)`` renders just enough rows to cover ``h`` px
    and composes them; ``finish()`` renders the rest. A cold 6-dict tooltip paints its visible top
    immediately and streams the below-the-fold bodies in afterwards, instead of blocking ~860 ms."""

    def __init__(
        self, rows: list[Row], width: int, theme: Theme = _DEFAULT_THEME, top_reserve: int = 0
    ):
        self.top_reserve = top_reserve  # blank px above row 0 to clear the sticky tab strip
        self._pending = list(rows)  # unrendered thunks (popped front-to-back)
        self._rendered: list[tuple[int, Image.Image, list[ScanBox], list[LinkBox], int]] = []
        # Bounded strip of the FIRST pending row, shown in the head compose only. The row itself
        # stays pending — finish() re-renders it fully, so the completed panel is unchanged.
        self._partial: tuple[int, Image.Image, list[ScanBox], list[LinkBox], int] | None = None
        self.width = width
        self.theme = theme
        self._row_sections: list[str | None] = []  # parallel to _rendered (dict-tab sections)
        self.scan_boxes: list[ScanBox] = []  # panel-space hitboxes for the rendered rows
        self.link_boxes: list[LinkBox] = []  # panel-space clickable link regions
        self._offsets_frozen: list[tuple[str, int]] | None = None  # cached at release_rows()
        # render_to() is called from both the main-thread hover path and a prefetch worker on the
        # same panel key (popups.py's "single-writer per key" assumption isn't airtight — a re-hover
        # can race a still-running finish()); guards _pending/_rendered/_partial against concurrent pop.
        self._lock = threading.Lock()

    @property
    def complete(self) -> bool:
        return not self._pending

    def _height(self) -> int:
        n = len(self._rendered)
        if n == 0:
            return 0
        m = self.theme.margin
        heights = sum(r[1].height for r in self._rendered)
        inter = sum(self._rendered[i][4] for i in range(n - 1)) if n > 1 else 0
        return 2 * m + self.top_reserve + heights + inter

    def _compose(self) -> Image.Image:
        m = self.theme.margin
        show = self._rendered + ([self._partial] if self._partial is not None else [])
        canvas = compose_panel(
            [(x, im) for x, im, _, _, _ in show],
            self.width,
            self.theme,
            gaps=[g for *_, g in show],
            top_reserve=self.top_reserve,
        )
        scan: list[ScanBox] = []
        links: list[LinkBox] = []
        y = m + self.top_reserve
        n = len(show)
        for i, (x, im, local, llocal, g) in enumerate(show):
            # row-local → panel coords
            scan.extend(ScanBox(sb.text, sb.x + x, sb.y + y, sb.w, sb.h) for sb in local)
            links.extend(LinkBox(lb.query, lb.x + x, lb.y + y, lb.w, lb.h) for lb in llocal)
            y += im.height + (g if i < n - 1 else 0)
        self.scan_boxes = scan
        self.link_boxes = links
        return canvas

    def release_rows(self) -> None:
        """Drop the per-row rendered sub-images once the panel is complete and its BGRA has been
        captured elsewhere — they are the single largest retained buffer (a full second copy of the
        panel) and are never needed again: scrolling slices the BGRA, hit-testing uses ``scan_boxes`` /
        ``link_boxes`` (already composed onto ``self``), and the only other reader — ``section_offsets``
        — is frozen here first. Idempotent."""
        if self._offsets_frozen is None:
            self._offsets_frozen = self.section_offsets()
        self._rendered = []
        self._row_sections = []
        self._partial = None

    def section_offsets(self) -> list[tuple[str, int]]:
        """(dict_name, y) for each rendered section-start row, in panel coords — the scroll targets
        for the tab row and LEFT/RIGHT keyboard nav. Grows as finish() streams, then frozen by
        release_rows() so it survives dropping the row images."""
        if self._offsets_frozen is not None:
            return self._offsets_frozen
        m = self.theme.margin
        y = m + self.top_reserve
        out: list[tuple[str, int]] = []
        n = len(self._rendered)
        for i, ((_x, im, _s, _l, g), sec) in enumerate(
            zip(self._rendered, self._row_sections, strict=True)
        ):
            if sec:
                out.append((sec, y))
            y += im.height + (g if i < n - 1 else 0)
        return out

    def render_to(self, min_height: int) -> Image.Image:
        """Render rows until the composed panel is at least ``min_height`` px tall (or all rows are
        done), then compose. Safe for concurrent callers (lock-serialized) — each renders what's left.

        If the next row supports bounded raster (a def-body block) and the remaining budget is
        smaller than the row, only the covering strip is rasterised now and the row stays pending
        — cold first paint is O(viewport) even when the first def body is one enormous block."""
        with self._lock:
            return self._render_to_locked(min_height)

    def _render_to_locked(self, min_height: int) -> Image.Image:
        self._partial = None
        while self._pending and self._height() < min_height:
            row = self._pending[0]
            gap = row.gap if row.gap is not None else self.theme.gap
            if row.render_capped is not None:
                remaining = min_height - self._height()
                img, scan, links, complete = row.render_capped(remaining)
                if not complete:
                    self._partial = (row.x, img, scan, links, gap)  # head strip; stays pending
                    break
                self._pending.pop(0)
                self._rendered.append((row.x, img, scan, links, gap))
                self._row_sections.append(row.section)
                continue
            self._pending.pop(0)
            img, scan, links = row.render()
            self._rendered.append((row.x, img, scan, links, gap))
            self._row_sections.append(row.section)
        return self._compose()

    def finish(self, workers: int = 4) -> Image.Image:
        """Render every remaining row and compose the complete panel. Gated on the count of
        *poolable* rows (``body_args`` set — the FreeType-bound def bodies, the only expensive
        work here), not raw row count: a typical 1-2-dict panel's header/chip rows are microseconds
        each and not worth dispatch overhead. ``>= 2`` poolable rows fans out to the process-wide
        :func:`~overlay.parallel.shared_executor` (see :meth:`_finish_parallel`) instead of
        rendering serially on the calling thread — the same pool
        :meth:`~overlay.render.banded.WindowedPanel.render_ahead` already uses for scroll-ahead
        blocks. This is what ``app/prefetch.py``'s engaged (``full=True``) worker jobs land in: a
        several-second multi-dict ``finish()`` no longer burns entirely on one prefetch thread."""
        with self._lock:
            pending = list(self._pending)
            poolable = sum(1 for row in pending if row.body_args is not None)
            if poolable <= 1:
                return self._render_to_locked(1 << 30)
            return self._finish_parallel(pending, workers)

    def _finish_parallel(self, pending: list[Row], workers: int) -> Image.Image:
        """Render ``pending`` (>1 poolable rows, all currently pending — called with ``self._lock``
        held, so no other renderer can race ``_pending``/``_rendered``) concurrently: threads on a
        free-threaded build (row thunks are closures, fine for threads, zero copy), a process pool on
        a GIL build for the picklable ``body_args`` rows only — cheap header/freq/reading rows aren't
        FreeType-bound and aren't picklable, so they still render inline. Mirrors
        ``WindowedPanel._render_ahead_parallel``'s dispatch."""
        self._partial = None
        ex = shared_executor(workers)
        results = (
            self._finish_threaded(ex, pending)
            if isinstance(ex, ThreadPoolExecutor)
            else self._finish_pooled(ex, pending)
        )
        for row, result in zip(pending, results, strict=True):
            assert result is not None
            img, scan, links = result
            gap = row.gap if row.gap is not None else self.theme.gap
            self._rendered.append((row.x, img, scan, links, gap))
            self._row_sections.append(row.section)
        self._pending = []
        return self._compose()

    @staticmethod
    def _finish_threaded(
        ex: ThreadPoolExecutor, pending: list[Row]
    ) -> list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None]:
        results: list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None] = [None] * len(
            pending
        )
        thread_futures: dict[Future[tuple[Image.Image, list[ScanBox], list[LinkBox]]], int] = {
            ex.submit(row.render): i for i, row in enumerate(pending)
        }
        for fut in as_completed(thread_futures):
            results[thread_futures[fut]] = fut.result()
        return results

    @staticmethod
    def _finish_pooled(
        ex: Executor, pending: list[Row]
    ) -> list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None]:
        results: list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None] = [None] * len(
            pending
        )
        pool_futures: dict[Future[tuple[Image.Image, list[ScanBox], list[LinkBox], bool]], int] = {}
        for i, row in enumerate(pending):
            if row.body_args is not None:
                pool_futures[ex.submit(render_body_block, row.body_args)] = i
            else:
                results[i] = row.render()
        for pfut in as_completed(pool_futures):
            img, scan, links, _complete = pfut.result()
            results[pool_futures[pfut]] = (img, scan, links)
        return results


def render_panel(
    entry: Entry,
    width: int = 384,
    theme: Theme = _DEFAULT_THEME,
    max_height: int | None = None,
    scroll_y: int = 0,
    *,
    add_button: bool = False,
    mined: bool = False,
    group_mined: tuple[bool, ...] = (),
) -> Image.Image:
    rows = panel_rows(
        entry, width, theme, add_button=add_button, mined=mined, group_mined=group_mined
    )
    rendered = [(r.x, r.render()[0]) for r in rows]
    gaps = [theme.gap if r.gap is None else r.gap for r in rows]
    canvas = compose_panel(rendered, width, theme, gaps)
    total_h = canvas.height

    if max_height is not None and total_h > max_height:
        # clip to a viewport (scroll offset now; scrollbar drawn by the controller viewport)
        top = max(0, min(scroll_y, total_h - max_height))
        canvas = canvas.crop((0, top, width, top + max_height))
    return canvas
