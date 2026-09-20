"""Whole-event coloring from positioned libass layers or qualified ASS OSD text."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
import pysubs2

from saitenka_subtitles import font_names
from saitenka_subtitles.geometry import MAX_BITMAP_BYTES
from saitenka_subtitles.overpaint import MAX_COMPOSITE_PIXELS, Overpaint

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka_subtitles.ass_geometry import PreparedAssFrame
    from saitenka_subtitles.libass_backend import ImageLayer


@dataclass(frozen=True, slots=True)
class FillLayer:
    token: int
    x: int
    y: int
    width: int
    height: int
    bitmap: bytes


@dataclass(frozen=True, slots=True)
class OsdEvent:
    text: str
    prefix: str
    spans: tuple[tuple[int, int, int], ...]
    resets: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class WholeCue:
    layers: tuple[FillLayer, ...] = ()
    events: tuple[OsdEvent, ...] = ()
    resolution: tuple[int, int] = (0, 0)
    osd_reason: str = "unqualified"
    blockers: tuple[str, ...] = ()
    evidence: tuple[tuple[str, object], ...] = ()
    mapping: tuple[float, float, float, float] = (1, 1, 0, 0)
    qualify: bool = False

    @property
    def byte_size(self) -> int:
        return sum(len(layer.bitmap) for layer in self.layers)


def retain_layers(layers: Sequence[ImageLayer], palette: dict[int, int]) -> tuple[FillLayer, ...]:
    """Retain libass fill layers directly; no native reference render or pixel attribution."""
    result = []
    size = 0
    for layer in layers:
        token = palette.get(layer.color >> 8)
        if layer.image_type != 0 or token is None or layer.color & 255:
            continue
        if layer.width <= 0 or layer.height <= 0:
            continue
        size += len(layer.bitmap)
        if len(layer.bitmap) != layer.width * layer.height or size > MAX_BITMAP_BYTES:
            raise ValueError("invalid whole-cue bitmap size")
        result.append(
            FillLayer(token, layer.dst_x, layer.dst_y, layer.width, layer.height, layer.bitmap)
        )
    return tuple(result)


def _overlaps(lower: ImageLayer, upper: ImageLayer) -> bool:
    left, top = max(lower.dst_x, upper.dst_x), max(lower.dst_y, upper.dst_y)
    right = min(lower.dst_x + lower.width, upper.dst_x + upper.width)
    bottom = min(lower.dst_y + lower.height, upper.dst_y + upper.height)
    if left >= right or top >= bottom:
        return False
    a = np.frombuffer(lower.bitmap, np.uint8).reshape(lower.height, lower.width)
    b = np.frombuffer(upper.bitmap, np.uint8).reshape(upper.height, upper.width)
    return bool(
        np.any(
            (
                a[
                    top - lower.dst_y : bottom - lower.dst_y,
                    left - lower.dst_x : right - lower.dst_x,
                ]
                != 0
            )
            & (
                b[
                    top - upper.dst_y : bottom - upper.dst_y,
                    left - upper.dst_x : right - upper.dst_x,
                ]
                != 0
            )
        )
    )


def has_occlusion(layers: Sequence[ImageLayer], palette: set[int]) -> bool:
    """A topmost overlay cannot preserve authored paint covering a selected fill."""
    if len(layers) > 256:
        return True
    fills: list[ImageLayer] = []
    for layer in layers:
        if not layer.width or not layer.height or layer.color & 255 == 255:
            continue
        if any(lower.color != layer.color and _overlaps(lower, layer) for lower in fills):
            return True
        if layer.image_type == 0 and layer.color >> 8 in palette:
            fills.append(layer)
    return False


def layer_bounds(layers: Sequence[FillLayer]) -> tuple[int, int, int, int]:
    if not layers:
        return (0, 0, 0, 0)
    return (
        min(layer.x for layer in layers),
        min(layer.y for layer in layers),
        max(layer.x + layer.width for layer in layers),
        max(layer.y + layer.height for layer in layers),
    )


def render_constraints(
    cue: WholeCue,
    *,
    pixel_aspect: float,
    default_state: bool,
    blended: bool,
    osd_justify: bool = False,
) -> WholeCue:
    blockers = list(cue.blockers)
    if pixel_aspect != 1:
        blockers.append("pixel-aspect")
    if not default_state:
        blockers.append("renderer-state")
    if blended:
        blockers.append("blended")
    if osd_justify:
        blockers.append("osd-justify")
    return replace(cue, osd_reason=blockers[0], blockers=tuple(blockers)) if blockers else cue


def compose(cue: WholeCue, colors: tuple[tuple[int, int], ...]) -> Overpaint | None:
    """Tint complete-cue layers and source-over them in libass order."""
    palette = dict(colors)
    layers = [layer for layer in cue.layers if layer.token in palette]
    if not layers:
        return None
    x, y = min(layer.x for layer in layers), min(layer.y for layer in layers)
    width = max(layer.x + layer.width for layer in layers) - x
    height = max(layer.y + layer.height for layer in layers) - y
    if width * height > MAX_COMPOSITE_PIXELS:
        return None
    premultiplied = np.zeros((height, width, 4), dtype=np.float32)
    for layer in layers:
        rgb = palette[layer.token]
        if not 0 <= rgb <= 0xFFFFFF:
            raise ValueError("invalid whole-cue RGB")
        alpha = np.frombuffer(layer.bitmap, np.uint8).reshape(layer.height, layer.width, 1) / 255.0
        color = np.array([rgb >> 16, (rgb >> 8) & 255, rgb & 255, 255])
        window = premultiplied[
            layer.y - y : layer.y - y + layer.height, layer.x - x : layer.x - x + layer.width
        ]
        window[:] = color * alpha + window * (1 - alpha)
    output_alpha = premultiplied[..., 3:4]
    premultiplied[..., :3] = np.divide(
        premultiplied[..., :3] * 255,
        output_alpha,
        out=np.zeros_like(premultiplied[..., :3]),
        where=output_alpha != 0,
    )
    rgba = np.rint(premultiplied).clip(0, 255).astype(np.uint8)
    rgba.setflags(write=False)
    return Overpaint(x, y, rgba)


def raster_fits(cue: WholeCue) -> bool:
    if not cue.layers:
        return False
    width = max(layer.x + layer.width for layer in cue.layers) - min(
        layer.x for layer in cue.layers
    )
    height = max(layer.y + layer.height for layer in cue.layers) - min(
        layer.y for layer in cue.layers
    )
    return width * height <= MAX_COMPOSITE_PIXELS


def _style_tags(style: pysubs2.SSAStyle) -> str:
    return (
        f"\\fn{style.fontname}\\fs{style.fontsize:g}"
        f"\\fscx{style.scalex:g}\\fscy{style.scaley:g}\\fsp{style.spacing:g}"
        f"\\b{int(style.bold)}\\i{int(style.italic)}\\s{int(style.strikeout)}"
        f"\\frz{style.angle:g}\\an{int(style.alignment)}\\fe{style.encoding}"
        f"\\bord{style.outline:g}\\shad{style.shadow:g}\\blur0\\q2"
    )


@lru_cache(maxsize=2)
def _document(source: bytes) -> pysubs2.SSAFile:
    return pysubs2.SSAFile.from_string(source.decode("utf-8-sig"), format_="ass")


def _blocked_fonts(
    doc: pysubs2.SSAFile, prepared: PreparedAssFrame, blocked: frozenset[str]
) -> bool:
    families: set[str] = set()
    for event in prepared.events:
        raw = event.decoded.source.raw_text
        styles = {event.decoded.source.style}
        styles.update(
            name or event.decoded.source.style for name in re.findall(r"\\r([^\\}]*)", raw)
        )
        families.update(doc.styles[name].fontname for name in styles if name in doc.styles)
        families.update(re.findall(r"\\fn([^\\}]*)", raw))
    return any(font_names.key(family) in blocked for family in families)


def osd_template(
    source: bytes,
    prepared: PreparedAssFrame,
    *,
    fonts_blocked: bool,
    frame: tuple[int, int] | None = None,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    blocked_families: frozenset[str] = frozenset(),
) -> WholeCue:
    """Lower static events; the worker qualifies final whole-cue glyph geometry."""
    doc = _document(source)
    blockers = (
        ["font-access"] if fonts_blocked or _blocked_fonts(doc, prepared, blocked_families) else []
    )
    if frame is None and doc.info.get("Kerning", "no").casefold() != "yes":
        blockers.append("kerning")
    if frame is None and doc.info.get("WrapStyle", "0") != "2":
        blockers.append("wrapping")
    resolution = (int(doc.info.get("PlayResX", 0)), int(doc.info.get("PlayResY", 0)))
    if min(resolution) <= 0:
        return WholeCue(osd_reason="script-resolution", blockers=(*blockers, "script-resolution"))
    if any(
        int(doc.info.get(key, resolution[i])) != resolution[i]
        for i, key in enumerate(("LayoutResX", "LayoutResY"))
    ):
        blockers.append("layout-resolution")
    mapping = (1.0, 1.0, 0.0, 0.0)
    if frame is not None:
        top, bottom, left, right = margins
        mapping = (
            (frame[0] - left - right) / resolution[0],
            (frame[1] - top - bottom) / resolution[1],
            float(left),
            float(top),
        )
    events = []
    for event in prepared.events:
        raw = event.decoded.source.raw_text
        spans = event.decoded.raw_spans
        if spans is None or re.search(
            r"\\(?:p\d|fr[xyz]?[-\d]|fax|fay|org\(|move\(|t\(|[iu]?clip\()", raw
        ):
            blockers.append("event-layout")
            continue
        style = doc.styles.get(event.decoded.source.style)
        if style is None or style.angle or style.underline or style.strikeout:
            blockers.append("style-layout")
            continue
        prefix = _style_tags(style)
        if not re.search(r"\\pos\(", raw):
            if len(prepared.events) > 1:
                blockers.append("event-order")
            original = next((e for e in doc.events if e.text == raw), None)
            ml = (original.marginl if original else 0) or style.marginl
            mr = (original.marginr if original else 0) or style.marginr
            mv = (original.marginv if original else 0) or style.marginv
            alignment = int(style.alignment)
            x = (ml, resolution[0] / 2, resolution[0] - mr)[(alignment - 1) % 3]
            y = (resolution[1] - mv, resolution[1] / 2, mv)[(alignment - 1) // 3]
            prefix += f"\\pos({x:g},{y:g})"
        resets = []
        for match in re.finditer(r"\\r([^\\}]*)", raw):
            name = match[1] or event.decoded.source.style
            if name not in doc.styles:
                blockers.append("style-layout")
            else:
                resets.append((match[1], _style_tags(doc.styles[name])))
        owners = {
            i: token.token_index
            for token in event.tokens
            for i in range(token.text_start, token.text_end)
        }
        events.append(
            OsdEvent(
                raw,
                prefix,
                tuple((span.start, span.end, owners.get(i, -1)) for i, span in enumerate(spans)),
                tuple(resets),
            )
        )
    blockers = list(dict.fromkeys(blockers))
    return WholeCue(
        events=tuple(events),
        resolution=frame or resolution,
        mapping=mapping,
        osd_reason=blockers[0] if blockers else "eligible",
        blockers=tuple(blockers),
        qualify=frame is not None,
    )


def _mapped_tags(text: str, mapping: tuple[float, float, float, float]) -> str:
    sx, sy, dx, dy = mapping

    def block(match: re.Match) -> str:
        tags = re.sub(
            r"\\pos\(\s*([-+\d.]+)\s*,\s*([-+\d.]+)\s*\)",
            lambda m: f"\\pos({float(m[1]) * sx + dx:.12g},{float(m[2]) * sy + dy:.12g})",
            match[0],
        )
        return re.sub(
            r"\\(fscx|fs|fsp|bord|shad)([-+]?\d+(?:\.\d+)?)",
            lambda m: (
                f"\\{m[1]}{float(m[2]) * (sx / sy if m[1] == 'fscx' else sx if m[1] == 'fsp' else sy):.17g}"
            ),
            tags,
        )

    return re.sub(r"\{[^}]*\}", block, text)


def osd_document(cue: WholeCue, colors: tuple[tuple[int, int], ...]) -> bytes:
    doc = pysubs2.SSAFile()
    doc.info.update(
        PlayResX=str(cue.resolution[0]),
        PlayResY=str(cue.resolution[1]),
        Kerning="yes",
        WrapStyle="1",
        **{"YCbCr Matrix": "None"},
    )
    doc.events = [
        pysubs2.SSAEvent(start=0, end=1000, text=line)
        for line in osd_payload(cue, colors).split("\n")
    ]
    return doc.to_string("ass").encode()


def _expand_resets(raw: str, resets: tuple[tuple[str, str], ...]) -> str:
    styles = dict(resets)
    return re.sub(r"\\r([^\\}]*)", lambda m: r"\r" + styles.get(m[1], ""), raw)


def osd_payload(cue: WholeCue, colors: tuple[tuple[int, int], ...]) -> str:
    palette = dict(colors)
    output = []
    for event in cue.events:
        insertions = []
        previous_end, previous = -1, ""
        for start, end, token in event.spans:
            color = palette.get(token)
            paint = (
                r"\1a&HFF&"
                if color is None
                else (rf"\1a&H00&\1c&H{color & 255:02X}{(color >> 8) & 255:02X}{color >> 16:02X}&")
            )
            paint += r"\2a&HFF&\3a&HFF&\4a&HFF&\u0"
            if start != previous_end or paint != previous:
                insertions.append((start, "{" + paint + "}"))
            previous_end, previous = end, paint
        raw = event.text
        for start, tags in reversed(insertions):
            raw = raw[:start] + tags + raw[start:]
        raw = _expand_resets(raw, event.resets)
        raw = re.sub(r"\\q[0-3]", lambda _m: r"\q2", raw)
        output.append(_mapped_tags("{" + event.prefix + "}" + raw, cue.mapping))
    return "\n".join(output) if colors else ""
