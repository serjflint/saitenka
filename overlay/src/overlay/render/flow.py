"""Ruby in flow — inline ruby inside wrapped rich text.

Generalises the wrap to a heterogeneous inline stream: plain text tokens *and* :class:`RubyBox`
elements. A ruby box is atomic (a line may break before or after it, never inside), it contributes
its advance width to wrapping, and — because a line's ascent is the max over its items — it reserves
the furigana clearance for its whole line automatically. Baseline stays consistent across lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from overlay import fonts
from overlay.model import LinkBox, ScanBox, Span, Style
from overlay.render.layout import (
    NO_END,
    NO_START,
    Block,
    Token,
    _font,
    _tokenize_span,
    draw_token,
)
from overlay.render.ruby import RubyBox, _base_size, layout_ruby

if TYPE_CHECKING:
    from overlay.draw.chip import ChipStyle, Sprite


@dataclass
class ImgBox:
    """Inline box: either an opaque placeholder (structured-content img / pitch graph, deferred) or
    a pre-drawn sprite (a small icon). Sits with its bottom slightly below the baseline."""

    width: int
    height: int
    label: str = ""
    fill: tuple[int, int, int, int] = (0, 0, 0, 0)
    border: tuple[int, int, int, int] = (170, 170, 170, 255)
    sprite: Image.Image | None = None
    baseline_drop: int = 0  # px the box extends below the baseline

    @property
    def advance(self) -> float:
        return self.width

    @property
    def ascent(self) -> int:
        return self.height - self.baseline_drop

    @property
    def descent(self) -> int:
        return self.baseline_drop

    def draw(self, img: Image.Image, draw: ImageDraw.ImageDraw, x: float, baseline: float) -> None:
        top = baseline + self.baseline_drop - self.height
        if self.sprite is not None:
            img.alpha_composite(self.sprite, (round(x), round(top)))
            return
        draw.rounded_rectangle(
            [x, top, x + self.width - 1, top + self.height - 1],
            radius=3,
            fill=self.fill,
            outline=self.border,
            width=1,
        )
        if self.label:
            f = _font(fonts.FONT_FILES[1], Style(size=max(9, self.height // 3)))
            draw.text(
                (x + self.width / 2, top + self.height / 2),
                self.label,
                font=f,
                fill=self.border,
                anchor="mm",
            )


@dataclass
class ChipBox:
    """A pre-rendered chip/pill/bordered-label sprite placed inline (text baseline on the line)."""

    text: str
    chip_style: ChipStyle  # runtime import stays lazy to avoid the cycle
    _sprite: Sprite | None = None

    @property
    def sprite(self):
        if self._sprite is None:
            from overlay.draw.chip import render_chip

            self._sprite = render_chip(self.text, self.chip_style)
        return self._sprite

    @property
    def advance(self) -> float:
        return self.sprite.width

    @property
    def ascent(self) -> int:
        return self.sprite.baseline

    @property
    def descent(self) -> int:
        return self.sprite.height - self.sprite.baseline

    def draw(self, img: Image.Image, draw: ImageDraw.ImageDraw, x: float, baseline: float) -> None:
        img.alpha_composite(self.sprite.image, (round(x), round(baseline - self.sprite.baseline)))


# A flow segment is styled text, a ruby box, an opaque image box, or a chip.
Inline = Span | RubyBox | ImgBox | ChipBox


def ruby(base_text: str, reading: str, style: Style | None = None) -> RubyBox:
    return layout_ruby([Span(base_text, style or Style())], reading)


@dataclass
class Item:
    kind: str  # 'text' | 'space' | 'ruby' | 'img' | 'chip' | 'break'
    width: float
    tok: Token | None = None
    box: RubyBox | None = None
    img: ImgBox | None = None
    chip: ChipBox | None = None

    def no_start(self) -> bool:
        # kind == "text" guarantees tok is set (build_items invariant)
        return (
            self.kind == "text"
            and self.tok is not None
            and self.tok.kind == "cjk"
            and self.tok.text in NO_START
        )

    def no_end(self) -> bool:
        return (
            self.kind == "text"
            and self.tok is not None
            and self.tok.kind == "cjk"
            and self.tok.text in NO_END
        )

    def metrics(self) -> tuple[int, int, int]:
        """(ascent, descent, nominal_size) for line-height computation."""
        if self.kind == "ruby" and self.box is not None:
            return self.box.ascent, self.box.descent, _base_size(self.box.base)
        if self.kind == "img" and self.img is not None:
            return self.img.ascent, self.img.descent, self.img.height
        if self.kind == "chip" and self.chip is not None:
            return self.chip.ascent, self.chip.descent, self.chip.ascent
        if self.tok is not None:
            a, d = _font(self.tok.file, self.tok.style).getmetrics()
            return a, d, self.tok.style.size
        return 0, 0, 0


def build_items(flow: list[Inline]) -> list[Item]:
    items: list[Item] = []
    for seg in flow:
        if isinstance(seg, RubyBox):
            items.append(Item("ruby", seg.advance, box=seg))
            continue
        if isinstance(seg, ImgBox):
            items.append(Item("img", seg.advance, img=seg))
            continue
        if isinstance(seg, ChipBox):
            items.append(Item("chip", seg.advance, chip=seg))
            continue
        for tok in _tokenize_span(seg.text, seg.style, getattr(seg, "href", None)):
            if tok.text == "\n":
                items.append(Item("break", 0.0))
            elif tok.kind == "space":
                items.append(Item("space", tok.width, tok=tok))
            else:
                items.append(Item("text", tok.width, tok=tok))
    return items


def wrap_items(items: list[Item], max_width: float) -> list[list[Item]]:
    lines: list[list[Item]] = []
    line: list[Item] = []
    x = 0.0
    for it in items:
        if it.kind == "break":
            lines.append(line)
            line, x = [], 0.0
            continue
        if it.kind == "space" and not line:
            continue
        if line and x + it.width > max_width and not it.no_start():
            carry: list[Item] = []
            while line and line[-1].no_end():
                carry.insert(0, line.pop())
            lines.append(line)
            line = [*carry, it]
            x = sum(i.width for i in line)
            continue
        line.append(it)
        x += it.width
    if line:
        lines.append(line)
    return lines


def _item_line_box(line: list[Item], scale: float) -> tuple[int, int, int]:
    """(box_height, baseline_from_top, ascent) — leading scaled to the line's nominal text size."""
    ascent = descent = size = 0
    for it in line:
        a, d, s = it.metrics()
        ascent, descent, size = max(ascent, a), max(descent, d), max(size, s)
    if ascent == 0:
        a, d = _font(fonts.FONT_FILES[0], Style()).getmetrics()
        ascent, descent, size = a, d, Style().size
    lead = round(size * (scale - 1.0))
    box = ascent + descent + lead
    return box, lead // 2 + ascent, ascent


def first_baseline(flow: list[Inline], block: Block) -> int:
    """Y of the first line's baseline (from the flow image top) — for aligning list markers."""
    lines = wrap_items(build_items(flow), block.width)
    if not lines:
        return 0
    _, base_from_top, _ = _item_line_box(lines[0], block.line_height_scale)
    return base_from_top


def _flush_scan_run(
    run: list[tuple[str, float, float]], scan_out: list[ScanBox], y_top: float, h: float
) -> None:
    """Emit one :class:`ScanBox` per CJK char in a contiguous run, each carrying the tail from that
    char onward (so a hover longest-matches a word starting exactly under the cursor)."""
    n = len(run)
    for i in range(n):
        _, cx, cw = run[i]
        tail = "".join(run[j][0] for j in range(i, n))
        scan_out.append(ScanBox(tail, round(cx), round(y_top), round(cw), round(h)))


def render_flow(
    flow: list[Inline],
    block: Block,
    scan_out: list[ScanBox] | None = None,
    link_out: list[LinkBox] | None = None,
    max_height: int | None = None,
    clipped_out: list | None = None,
) -> Image.Image:
    """Wrap and render an inline stream (text + ruby) into a fixed-width panel image.

    When ``scan_out`` is given, append a per-CJK-character :class:`ScanBox` (flow-image coords) for
    nested scanning. When ``link_out`` is given, append one :class:`LinkBox` per internal ``<a>``
    link per visual line so a click can open the link's target term.

    ``max_height``: rasterise only the wrapped lines up to the first one that COVERS ``max_height``
    px (layout/wrapping of the whole flow still runs — it's cheap; drawing is the cost) — so a
    pathologically tall single block first-paints O(viewport), not O(block). When lines were
    dropped, ``True`` is appended to ``clipped_out``. ``max_height=None`` is the byte-identical
    full render."""
    items = build_items(flow)
    lines = wrap_items(items, block.width)
    boxes = [_item_line_box(line, block.line_height_scale) for line in lines]
    if max_height is not None and lines:
        acc = 2 * block.padding
        keep = 0
        for b in boxes:
            if acc >= max_height:
                break
            acc += b[0]
            keep += 1
        keep = max(1, keep)  # always draw at least one line
        if keep < len(lines):
            lines, boxes = lines[:keep], boxes[:keep]
            if clipped_out is not None:
                clipped_out.append(True)

    w = block.width + 2 * block.padding
    h = 2 * block.padding + sum(b[0] for b in boxes)
    img = Image.new("RGBA", (w, max(h, 1)), block.background)
    draw = ImageDraw.Draw(img)

    y = block.padding
    for line, (box, base_from_top, _a) in zip(lines, boxes, strict=True):
        baseline = y + base_from_top
        x = float(block.padding)
        run: list[tuple[str, float, float]] = []  # contiguous CJK chars: (char, x, width)
        link: tuple[str, float, float] | None = (
            None  # (query, x_start, x_end) of the current link run
        )
        for it in line:
            if it.kind == "ruby" and it.box is not None:  # kind implies the field (build_items)
                it.box.draw(img, draw, x, baseline)
            elif it.kind == "img" and it.img is not None:
                it.img.draw(img, draw, x, baseline)
            elif it.kind == "chip" and it.chip is not None:
                it.chip.draw(img, draw, x, baseline)
            elif it.kind == "text" and it.tok is not None:
                draw_token(img, draw, x, baseline, it.tok)
            if scan_out is not None:
                if it.kind == "text" and it.tok is not None and it.tok.kind == "cjk":
                    run.append((it.tok.text, x, it.width))
                elif run:
                    _flush_scan_run(run, scan_out, y, box)
                    run = []
            if link_out is not None:
                href = it.tok.href if (it.kind == "text" and it.tok is not None) else None
                if href:
                    link = (
                        (href, link[1], x + it.width)
                        if (link and link[0] == href)
                        else (href, x, x + it.width)
                    )
                elif link is not None:
                    q, xs, xe = link
                    link_out.append(LinkBox(q, round(xs), round(y), round(xe - xs), round(box)))
                    link = None
            x += it.width
        if scan_out is not None and run:
            _flush_scan_run(run, scan_out, y, box)
        if link_out is not None and link is not None:
            q, xs, xe = link
            link_out.append(LinkBox(q, round(xs), round(y), round(xe - xs), round(box)))
        y += box
    return img
