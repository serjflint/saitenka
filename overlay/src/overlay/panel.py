"""``render_panel`` — compose a full Yomitan-style dictionary entry into one RGBA image.

Assembles the chrome primitives (chips, bordered labels, list markers, icons) around walked
structured-content, reproducing the real 読む popup: big ruby headword + speaker, grammar tags,
frequency pills, dictionary-name pills, and numbered definitions with ruby'd examples. This is the
image the controller composites over mpv video in a single surface.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from PIL import Image

from overlay.draw.chip import ChipStyle
from overlay.draw.icons import check, dot, plus, speaker
from overlay.model import RGBA, LinkBox, ScanBox, Span, Style
from overlay.render.document import GUTTER_PX, INDENT_PX, render_document
from overlay.render.flow import ChipBox, ImgBox, render_flow
from overlay.render.layout import Block as FlowBlock
from overlay.sc.walk import inline_flow, walk


@dataclass(frozen=True)
class Theme:
    bg: RGBA = (252, 252, 250, 255)
    text: RGBA = (33, 33, 33, 255)
    muted: RGBA = (110, 118, 110, 255)
    accent: RGBA = (60, 110, 210, 255)  # ▶ triangle / links
    purple: RGBA = (126, 96, 168, 255)  # dictionary-name pills
    tag: RGBA = (96, 125, 175, 255)  # defTag pills (★ / priority form)
    # Every size in this module is defined at the REFERENCE window (scale 1.0) and multiplied by this,
    # so the whole tooltip renders at window_height / REF_H — mpv's OSD model, giving the same amount of
    # content at any window size (just scaled). A plain ``Theme()`` (scale 1.0) is byte-identical to before.
    scale: float = 1.0
    # Reference-size structural paddings (at scale 1.0); the scaled values are the properties below.
    _MARGIN: ClassVar[int] = 16
    _GAP: ClassVar[int] = 7
    _BODY_INDENT: ClassVar[int] = 20

    def px(self, v: float) -> int:
        """Scale a reference-canvas pixel size to the current window (floor 1px so nothing vanishes)."""
        return max(1, round(v * self.scale))

    @property
    def margin(self) -> int:
        return self.px(self._MARGIN)

    @property
    def gap(self) -> int:
        return self.px(self._GAP)

    @property
    def body_indent(self) -> int:
        return self.px(self._BODY_INDENT)


@dataclass
class Freq:
    name: str
    value: str
    color: RGBA


# A structured-content node (Yomitan SC): plain text, a tag dict, or a list of nodes.
type SCNode = str | dict | list


@dataclass
class Definition:
    dict_name: str
    content: SCNode  # structured-content node
    tags: list[str] = field(default_factory=list)  # defTags: ★, priority form, …


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


def _hex(s: str) -> RGBA:
    from overlay.sc.walk import _parse_color

    return _parse_color(s, (90, 122, 160, 255))


def load_entry(path: str | Path) -> Entry:
    data = json.loads(Path(path).read_text())
    return Entry(
        headword=data["headword"],
        tags=[t["text"] for t in data.get("tags", [])],
        freqs=[Freq(f["name"], f["value"], _hex(f["color"])) for f in data.get("freqs", [])],
        reading_label=(
            tuple(data["reading_label"].values()) if data.get("reading_label") else None
        ),
        defs=[Definition(d["dict"], d["content"]) for d in data.get("defs", [])],
    )


_DEFAULT_THEME = Theme()  # frozen — safe module singleton (B008: no per-call Theme())


def _flow_row(flow, content_w: int, scale: float = 1.35) -> Image.Image:
    return render_flow(
        flow,
        FlowBlock(width=content_w, padding=0, line_height_scale=scale, background=(0, 0, 0, 0)),
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
    width: int, theme: Theme = _DEFAULT_THEME, top_reserve: int = 0, speak_button: bool = True
) -> tuple[int, int, int, int]:
    """Panel-space (x, y, w, h) of the header ⊕ add-to-Anki button. Sits just left of the 🔊 speaker
    when it's shown, else takes the speaker's rightmost slot (so hiding TTS doesn't leave a gap).
    ``top_reserve`` must match the panel's tab-strip reserve so the hit-box tracks the drawn icon."""
    spk, add, gap, top = (
        theme.px(_SPK_SIZE),
        theme.px(_ADD_SIZE),
        theme.px(_ICON_GAP),
        theme.px(_ICON_TOP),
    )
    content_w = width - 2 * theme.margin
    right = content_w - (spk + gap if speak_button else 0)
    x = theme.margin + right - add
    y = theme.margin + top_reserve + top + theme.px(2)
    return (x, y, add, add)


def header_speaker_rect(
    width: int, theme: Theme = _DEFAULT_THEME, top_reserve: int = 0
) -> tuple[int, int, int, int]:
    """Panel-space (x, y, w, h) of the header 🔊 speaker button — the only click target that plays audio.
    ``top_reserve`` must match the panel's tab-strip reserve so the hit-box tracks the drawn icon."""
    spk, top = theme.px(_SPK_SIZE), theme.px(_ICON_TOP)
    content_w = width - 2 * theme.margin
    x = theme.margin + content_w - spk
    y = theme.margin + top_reserve + top
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
    render: Callable[[], tuple[Image.Image, list[ScanBox], list[LinkBox]]]
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


def panel_rows(
    entry: Entry,
    width: int = 384,
    theme: Theme = _DEFAULT_THEME,
    add_button: bool = False,
    mined: bool = False,
    speak_button: bool = True,
) -> list[Row]:
    """Build the panel's rows as deferred thunks (same order/content as ``render_panel``).

    ``add_button`` draws the header add-to-Anki button (only when mining is available); ``mined`` makes
    it a ✓ instead of ⊕ for a word already in the deck. ``speak_button`` draws the 🔊 TTS button — set
    False to hide it when no Japanese TTS voice is installed (it would silently do nothing). Defaults
    keep ``render_panel`` and its golden unchanged."""
    m = theme.margin
    content_w = width - 2 * m
    rows: list[Row] = []

    # --- header: ▶ + big ruby headword, ⊕/✓ add + 🔊 speaker top-right ---
    def _header() -> tuple[Image.Image, list[ScanBox], list[LinkBox]]:
        hw = [
            Span("▶", Style(size=theme.px(28), color=theme.accent)),
            Span(" ", Style(size=theme.px(46))),
        ]
        hw += inline_flow(entry.headword, Style(size=theme.px(46), weight=700, color=theme.text))
        hdr = _flow_row(hw, content_w)
        right = content_w
        top = theme.px(_ICON_TOP)
        if speak_button:
            spk = speaker(theme.px(_SPK_SIZE))
            hdr.alpha_composite(spk, (right - spk.width, top))
            right -= theme.px(_SPK_SIZE) + theme.px(_ICON_GAP)
        if add_button:
            add = theme.px(_ADD_SIZE)
            btn = check(add) if mined else plus(add)
            hdr.alpha_composite(btn, (right - add, top + theme.px(2)))
        return hdr, [], []

    rows.append(Row(m, _header))

    # --- pitch-accent graphs: one compact graph per distinct accent, in the header area next to
    # the reading; the purple text pill in the freq row stays as the fallback ---
    if entry.pitches:

        def _pitch_row(pitches=tuple(entry.pitches)):
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
            return _flow_row(flow, content_w, scale=1.5), [], []

        rows.append(Row(m, _pitch_row))

    # --- inflection chain: dot marker + one chip per Yomitan transform name (● [-て][-いる][-た]) ---
    if entry.inflection_chain:

        def _chain(chain=tuple(entry.inflection_chain)):
            pz = theme.px(18)
            cflow: list = [
                ImgBox(width=pz, height=pz, sprite=dot(pz), baseline_drop=theme.px(3)),
                Span("  ", Style(size=theme.px(20))),
            ]
            for i, name in enumerate(chain):
                if i:
                    cflow.append(Span("›", Style(size=theme.px(18), color=theme.muted)))
                cflow.append(
                    ChipBox(name, ChipStyle(size=theme.px(18), weight=600, bg=INFLECTION_BG))
                )
            return _flow_row(cflow, content_w, scale=1.7), [], []

        rows.append(Row(m, _chain))

    # --- grammar tags: dot marker + muted text ---
    for tag in entry.tags:

        def _tag(tag=tag):
            pz = theme.px(18)
            tflow = [
                ImgBox(width=pz, height=pz, sprite=dot(pz), baseline_drop=theme.px(3)),
                Span("  " + tag, Style(size=theme.px(20), color=theme.muted)),
            ]
            return _flow_row(tflow, content_w), [], []

        rows.append(Row(m, _tag))

    # --- frequency pills: two-tone (colored name + light value), SubMiner-style ---
    if entry.freqs:

        def _freqs(freqs=tuple(entry.freqs)):
            fflow: list = []
            for f in freqs:
                fflow.append(
                    ChipBox(
                        f.name, ChipStyle(size=theme.px(20), weight=600, bg=f.color, value=f.value)
                    )
                )
                fflow.append(Span("  ", Style(size=theme.px(20))))
            return _flow_row(fflow, content_w, scale=1.7), [], []

        rows.append(Row(m, _freqs))

    # --- reading label (dict-name pill + reading, e.g. よむ[1]) ---
    if entry.reading_label:

        def _reading(rl=entry.reading_label):
            dn, txt = rl
            flow = [
                ChipBox(dn, ChipStyle(size=theme.px(19), bg=theme.purple)),
                Span("  " + txt, Style(size=theme.px(20), color=theme.text)),
            ]
            return _flow_row(flow, content_w, scale=1.7), [], []

        rows.append(Row(m, _reading))

    # --- numbered definitions --- (def-name chip row is cheap; the body row is the expensive one)
    body_style = Style(size=theme.px(23), color=theme.text)
    for i, d in enumerate(entry.defs, 1):

        def _def_head(i=i, d=d):
            dh: list = [Span(f"{i}. ", Style(size=theme.px(20), weight=700, color=theme.text))]
            for tag in d.tags:  # defTag pills: ★ / priority form
                dh.append(ChipBox(tag, ChipStyle(size=theme.px(18), weight=600, bg=theme.tag)))
                dh.append(Span(" ", Style(size=theme.px(19))))
            dh.append(ChipBox(d.dict_name, ChipStyle(size=theme.px(19), bg=theme.purple)))
            return _flow_row(dh, content_w, scale=1.7), [], []

        rows.append(Row(m, _def_head, section=d.dict_name))

        # ONE row per def body, fully deferred: the SC-walk itself is NOT cheap for pathological
        # entries (a 取る-class def walks in 200+ ms), so both the walk AND the rasterisation live
        # inside the thunk — building rows costs nothing, and the head only walks/rasters the defs
        # the viewport actually shows. ``render_capped`` bounds the raster mid-def (block budget +
        # mid-block line clip via render_document/render_flow max_height) so cold first paint is
        # O(viewport) even when the first visible def body is enormous. render_document stacks the
        # walked blocks with the same 3px inter-block gap, so the composed full panel is
        # byte-identical.
        body_w = content_w - theme.body_indent

        def _def_body(d, body_w):  # explicit params — no loop-variable closure (B023)
            def thunk():
                scan: list[ScanBox] = []  # per-char hitboxes → nested scanning
                links: list[LinkBox] = []  # per-link hitboxes → clickable cross-refs
                img = render_document(
                    walk(d.content, body_style),
                    width=body_w,
                    base=body_style,
                    padding=0,
                    gap=theme.px(3),
                    indent_px=theme.px(INDENT_PX),
                    gutter_px=theme.px(GUTTER_PX),
                    background=(0, 0, 0, 0),
                    scan_out=scan,
                    link_out=links,
                )
                return img, scan, links

            def capped(max_h: int):
                scan: list[ScanBox] = []
                links: list[LinkBox] = []
                clipped: list = []
                img = render_document(
                    walk(d.content, body_style),
                    width=body_w,
                    base=body_style,
                    padding=0,
                    gap=theme.px(3),
                    indent_px=theme.px(INDENT_PX),
                    gutter_px=theme.px(GUTTER_PX),
                    background=(0, 0, 0, 0),
                    scan_out=scan,
                    link_out=links,
                    max_height=max_h,
                    clipped_out=clipped,
                )
                return img, scan, links, not clipped

            return thunk, capped

        body_thunk, body_capped = _def_body(d, body_w)
        rows.append(Row(m + theme.body_indent, body_thunk, render_capped=body_capped))

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


# Sticky dict-tab strip geometry. The strip WRAPS onto multiple rows so every dictionary tab stays
# visible — a many-dict word (10+ monolingual dicts) overflowed a single row and hid all but ~4 tabs.
_TAB_PAD_Y, _TAB_GAP, _TAB_ROW_GAP, _TAB_BOTTOM = 9, 11, 7, 7


def _tab_chip_styles(theme: Theme):
    from overlay.draw.chip import ChipStyle

    sz, ph, pv, rad = theme.px(20), theme.px(11), theme.px(6), theme.px(9)
    active_cs = ChipStyle(size=sz, weight=600, bg=theme.purple, pad_h=ph, pad_v=pv, radius=rad)
    idle_cs = ChipStyle(
        size=sz,
        weight=500,
        fg=theme.muted,
        bg=(0, 0, 0, 0),
        border=(170, 170, 170, 255),
        pad_h=ph,
        pad_v=pv,
        radius=rad,
    )
    return active_cs, idle_cs


def _tab_label(name: str) -> str:
    return name if len(name) <= 10 else name[:9] + "…"


def _tab_layout(
    names: list[str], width: int, theme: Theme
) -> tuple[list[tuple[int, int]], int, int]:
    """Wrapped ``(x, y)`` per tab + total strip height + chip height. Chip widths are measured with the
    IDLE style so the layout is STABLE regardless of which tab is active: the panel reserves this
    height once at build time, and the active-highlight (drawn later) must not shift wrap points, or the
    reserve would desync from the rendered strip (covering content / leaving a gap)."""
    from overlay.draw.chip import render_chip

    _, idle_cs = _tab_chip_styles(theme)
    sprites = [render_chip(_tab_label(n), idle_cs) for n in names]
    chip_h = max((sp.image.height for sp in sprites), default=0)
    pad_y, gap, row_gap, bottom = (
        theme.px(_TAB_PAD_Y),
        theme.px(_TAB_GAP),
        theme.px(_TAB_ROW_GAP),
        theme.px(_TAB_BOTTOM),
    )
    pad_x = theme.margin
    x, y = pad_x, pad_y
    pos: list[tuple[int, int]] = []
    for sp in sprites:
        w = sp.image.width
        if x > pad_x and x + w > width - pad_x:  # doesn't fit on this row → wrap to the next
            x, y = pad_x, y + chip_h + row_gap
        pos.append((x, y))
        x += w + gap
    total_h = (y + chip_h + bottom) if names else (pad_y + bottom)
    return pos, total_h, chip_h


def tab_strip_height(names: list[str], width: int, theme: Theme = _DEFAULT_THEME) -> int:
    """Total height of the (possibly multi-row) sticky tab strip for these names at this width — the
    exact space the panel reserves above its header so the wrapped strip never covers content."""
    return _tab_layout(names, width, theme)[1]


def tab_row_height(theme: Theme = _DEFAULT_THEME) -> int:
    """Height of a SINGLE-row strip — the one-row baseline / minimum reserve. A kanji sample so JP
    font metrics (taller than Latin) are reflected."""
    return _tab_layout(["三"], 64, theme)[1]


def render_tab_row(
    names: list[str], active: int, width: int, theme: Theme = _DEFAULT_THEME
) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """The sticky dict-tab strip: one chip per dictionary, the active one highlighted, WRAPPING onto
    multiple rows so all tabs stay visible for many-dict words. Opaque background (theme.bg) so it
    occludes scrolled content when composited onto the viewport. Returns (image, per-chip rects)."""
    from overlay.draw.chip import render_chip

    active_cs, idle_cs = _tab_chip_styles(theme)
    pos, total_h, _chip_h = _tab_layout(names, width, theme)
    img = Image.new("RGBA", (width, max(total_h, 1)), theme.bg)
    img.alpha_composite(Image.new("RGBA", (width, 1), (90, 90, 90, 120)), (0, total_h - 1))  # sep
    rects: list[tuple[int, int, int, int]] = []
    for i, (name, (x, y)) in enumerate(zip(names, pos, strict=True)):
        sp = render_chip(_tab_label(name), active_cs if i == active else idle_cs)
        img.alpha_composite(sp.image, (x, y))
        rects.append((x, y, sp.image.width, sp.image.height))
    return img, rects


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

    def section_offsets(self) -> list[tuple[str, int]]:
        """(dict_name, y) for each rendered section-start row, in panel coords — the scroll targets
        for the tab row and LEFT/RIGHT keyboard nav. Grows as finish() streams."""
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
        done), then compose. Idempotent enough for concurrent callers — each renders what's left.

        If the next row supports bounded raster (a def-body block) and the remaining budget is
        smaller than the row, only the covering strip is rasterised now and the row stays pending
        — cold first paint is O(viewport) even when the first def body is one enormous block."""
        self._partial = None
        while self._pending and self._height() < min_height:
            row = self._pending[0]
            gap = row.gap if row.gap is not None else self.theme.gap
            if row.render_capped is not None:
                remaining = min_height - self._height()
                img, scan, links, complete = row.render_capped(remaining)
                if not complete:
                    self._partial = (row.x, img, scan, links, gap)  # head strip; row stays pending
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

    def finish(self) -> Image.Image:
        return self.render_to(1 << 30)


def render_panel(
    entry: Entry,
    width: int = 384,
    theme: Theme = _DEFAULT_THEME,
    max_height: int | None = None,
    scroll_y: int = 0,
    add_button: bool = False,
    mined: bool = False,
) -> Image.Image:
    rows = panel_rows(entry, width, theme, add_button, mined)
    rendered = [(r.x, r.render()[0]) for r in rows]
    gaps = [theme.gap if r.gap is None else r.gap for r in rows]
    canvas = compose_panel(rendered, width, theme, gaps)
    total_h = canvas.height

    if max_height is not None and total_h > max_height:
        # clip to a viewport (scroll offset now; scrollbar drawn by the controller viewport)
        top = max(0, min(scroll_y, total_h - max_height))
        canvas = canvas.crop((0, top, width, top + max_height))
    return canvas
