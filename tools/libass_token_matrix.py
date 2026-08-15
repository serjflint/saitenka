"""Locked token-ID feasibility oracle for the experimental libass subtitle mode."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


class ImageLayer(Protocol):
    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    @property
    def bitmap(self) -> bytes: ...

    @property
    def color(self) -> int: ...

    @property
    def dst_x(self) -> int: ...

    @property
    def dst_y(self) -> int: ...

    @property
    def image_type(self) -> int: ...


class RenderResult(Protocol):
    @property
    def layers(self) -> Sequence[ImageLayer]: ...


class Renderer(Protocol):
    def render(
        self, timestamp_ms: int, frame_size: tuple[int, int], storage_size: tuple[int, int]
    ) -> RenderResult: ...

    def library_version(self) -> int: ...

    def library_path(self) -> str: ...

    def close(self) -> None: ...


class RendererFactory(Protocol):
    def __call__(self, ass_data: bytes, *, library_path: str | None = None) -> Renderer: ...


class LibassModule(Protocol):
    AssRenderer: RendererFactory


MAX_SUPPORT_DISTANCE = 1


@dataclass(frozen=True, order=True)
class TokenKey:
    event_id: str
    token_index: int


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class TokenGeometry:
    key: TokenKey
    bounds: Rect
    segments: tuple[Rect, ...]


def line_count(geometry: Iterable[TokenGeometry]) -> int:
    """Count non-overlapping vertical bands without assuming identical glyph tops."""
    intervals = sorted((item.bounds.y, item.bounds.y + item.bounds.height) for item in geometry)
    if not intervals:
        return 0
    count = 1
    _, current_bottom = intervals[0]
    for top, bottom in intervals[1:]:
        if top >= current_bottom:
            count += 1
            current_bottom = bottom
        else:
            current_bottom = max(current_bottom, bottom)
    return count


def _rgb(layer: ImageLayer) -> int:
    return layer.color >> 8


def _pixel_bounds(points: set[tuple[int, int]]) -> Rect:
    left = min(x for x, _ in points)
    top = min(y for _, y in points)
    right = max(x for x, _ in points) + 1
    bottom = max(y for _, y in points) + 1
    return Rect(left, top, right - left, bottom - top)


def extract_token_geometry(
    result: RenderResult,
    palette: Iterable[tuple[int, TokenKey]],
    *,
    reserved_colors: Iterable[int] = (),
) -> tuple[TokenGeometry, ...]:
    """Group character-image segments by an event-aware RGB palette."""
    by_color: dict[int, TokenKey] = {}
    seen_keys: set[TokenKey] = set()
    reserved = set(reserved_colors)
    for color, key in palette:
        if not 0 < color <= 0xFFFFFF:
            raise ValueError(f"invalid token color: {color:#08x}")
        if color in reserved:
            raise ValueError(f"token color is reserved: {color:#08x}")
        if color in by_color:
            raise ValueError(f"duplicate token color: {color:#08x}")
        if key in seen_keys:
            raise ValueError(f"token has multiple colors: {key}")
        by_color[color] = key
        seen_keys.add(key)

    segments: dict[TokenKey, list[Rect]] = defaultdict(list)
    masks: dict[TokenKey, set[tuple[int, int]]] = defaultdict(set)
    for layer in result.layers:
        if layer.image_type != 0 or layer.width <= 0 or layer.height <= 0:
            continue
        color = _rgb(layer)
        if color in reserved:
            continue
        try:
            key = by_color[color]
        except KeyError as error:
            raise ValueError(f"unknown character color: {color:#08x}") from error
        segments[key].append(Rect(layer.dst_x, layer.dst_y, layer.width, layer.height))
        masks[key].update(
            (layer.dst_x + index % layer.width, layer.dst_y + index // layer.width)
            for index, coverage in enumerate(layer.bitmap)
            if coverage
        )

    missing = seen_keys - {key for key, points in masks.items() if points}
    if missing:
        raise ValueError(f"missing token colors: {sorted(missing)}")
    geometries = tuple(
        TokenGeometry(key, _pixel_bounds(masks[key]), tuple(rects))
        for key, rects in sorted(segments.items())
    )
    for index, left in enumerate(geometries):
        for right in geometries[index + 1 :]:
            if masks[left.key] & masks[right.key]:
                raise ValueError(f"ambiguous token bounds: {left.key} overlaps {right.key}")
    return geometries


def geometry_signature(result: RenderResult) -> tuple[tuple[object, ...], ...]:
    """Color-independent public-layer signature; run boundaries remain observable."""
    return tuple(
        (
            layer.image_type,
            layer.width,
            layer.height,
            layer.dst_x,
            layer.dst_y,
            layer.bitmap,
        )
        for layer in result.layers
    )


def character_support(result: RenderResult) -> frozenset[tuple[int, int]]:
    return frozenset(
        (layer.dst_x + index % layer.width, layer.dst_y + index // layer.width)
        for layer in result.layers
        if layer.image_type == 0
        for index, coverage in enumerate(layer.bitmap)
        if coverage
    )


def support_bounds(points: frozenset[tuple[int, int]]) -> tuple[int, int, int, int]:
    if not points:
        return (0, 0, 0, 0)
    return (
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points) + 1,
        max(y for _, y in points) + 1,
    )


def support_iou(left: frozenset[tuple[int, int]], right: frozenset[tuple[int, int]]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def supports_within(
    left: frozenset[tuple[int, int]], right: frozenset[tuple[int, int]], distance: int
) -> bool:
    def covered(source: frozenset[tuple[int, int]], target: frozenset[tuple[int, int]]) -> bool:
        return all(
            any(
                (x + dx, y + dy) in target
                for dx in range(-distance, distance + 1)
                for dy in range(-distance, distance + 1)
            )
            for x, y in source
        )

    return covered(left, right) and covered(right, left)


def character_mask_signature(result: RenderResult) -> tuple[tuple[int, int, int, int], str]:
    """Final character coverage independent of list segmentation and color."""
    pixels: dict[tuple[int, int], int] = {}
    for layer in result.layers:
        if layer.image_type != 0:
            continue
        opacity = 255 - (layer.color & 0xFF)
        for row in range(layer.height):
            for column in range(layer.width):
                coverage = layer.bitmap[row * layer.width + column] * opacity // 255
                if coverage:
                    point = (layer.dst_x + column, layer.dst_y + row)
                    pixels[point] = max(pixels.get(point, 0), coverage)
    if not pixels:
        return ((0, 0, 0, 0), hashlib.sha256().hexdigest())
    left = min(x for x, _ in pixels)
    top = min(y for _, y in pixels)
    right = max(x for x, _ in pixels) + 1
    bottom = max(y for _, y in pixels) + 1
    packed = bytes(pixels.get((x, y), 0) for y in range(top, bottom) for x in range(left, right))
    return ((left, top, right, bottom), hashlib.sha256(packed).hexdigest())


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,24,24,30,1
Style: Sign,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,1,0,8,20,20,20,1
Style: Box,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,3,3,0,2,24,24,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_bytes(events: Sequence[str]) -> bytes:
    return (ASS_HEADER + "\n".join(f"Dialogue: {event}" for event in events) + "\n").encode()


def fixture_hash(events: Sequence[str]) -> str:
    return hashlib.sha256(ass_bytes(events)).hexdigest()


def key_set_hash(case_ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(case_ids)).encode()).hexdigest()


def classification_hash(cases: Iterable[dict]) -> str:
    rows = sorted(f"{case['id']}:{case['expectation']}" for case in cases)
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def contract_hash(case: dict) -> str:
    payload = json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def contract_set_hash(cases: Iterable[dict]) -> str:
    rows = sorted(f"{case['id']}:{contract_hash(case)}" for case in cases)
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _font_inventory() -> tuple[str, tuple[str, ...]]:
    roots = []
    if os.name == "nt":
        roots.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            roots.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
    elif platform.system() == "Darwin":
        roots.extend(
            (
                Path("/System/Library/Fonts"),
                Path("/Library/Fonts"),
                Path.home() / "Library" / "Fonts",
            )
        )
    else:
        roots.extend(
            (
                Path("/usr/share/fonts"),
                Path("/usr/local/share/fonts"),
                Path.home() / ".local" / "share" / "fonts",
                Path.home() / ".fonts",
            )
        )
    entries = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            stat = path.stat()
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(f"{path}:{stat.st_size}:{content_hash}")
    digest = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    return digest, tuple(entries)


def _load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be an object")
    if manifest.get("schema") != 1:
        raise ValueError("unsupported manifest schema")
    cases = manifest.get("cases")
    denominator = manifest.get("denominator")
    if not isinstance(cases, list) or not isinstance(denominator, dict):
        raise TypeError("manifest must contain cases and denominator")
    if len(cases) != denominator.get("count"):
        raise ValueError("manifest case count changed")
    if key_set_hash(str(case["id"]) for case in cases) != denominator.get("key_set_sha256"):
        raise ValueError("manifest case identities changed")
    if classification_hash(cases) != denominator.get("classification_sha256"):
        raise ValueError("manifest case classifications changed")
    if contract_set_hash(cases) != denominator.get("contract_set_sha256"):
        raise ValueError("manifest execution contracts changed")
    if hashlib.sha256(ASS_HEADER.encode()).hexdigest() != denominator.get("ass_header_sha256"):
        raise ValueError("ASS header changed")
    if denominator.get("maximum_chebyshev_distance_px") != MAX_SUPPORT_DISTANCE:
        raise ValueError("support distance threshold changed")
    for case in cases:
        visible_events = case["visible_events"]
        id_events = case["id_events"]
        if not isinstance(visible_events, list) or not isinstance(id_events, list):
            raise TypeError("manifest events must be lists")
    return manifest


def _case_report(renderer_type: RendererFactory, case: dict, library_path: str | None) -> dict:
    visible_events = case["visible_events"]
    id_events = case["id_events"]
    assert isinstance(visible_events, list) and isinstance(id_events, list)
    visible = renderer_type(ass_bytes(visible_events), library_path=library_path)
    hit_map = renderer_type(ass_bytes(id_events), library_path=library_path)
    try:
        timestamp = int(case["timestamp_ms"])
        visible_result = visible.render(timestamp, (1280, 720), (1280, 720))
        id_result = hit_map.render(timestamp, (1280, 720), (1280, 720))
        palette = [
            (int(item["rgb"], 16), TokenKey(str(item["event_id"]), int(item["token_index"])))
            for item in case["palette"]
        ]
        reserved = [int(value, 16) for value in case.get("reserved_rgb", [])]
        error = None
        boxes = []
        geometry = ()
        try:
            geometry = extract_token_geometry(id_result, palette, reserved_colors=reserved)
            boxes = [asdict(item) for item in geometry]
        except ValueError as exc:
            error = str(exc)
        geometry_equal = geometry_signature(visible_result) == geometry_signature(id_result)
        mask_equal = character_mask_signature(visible_result) == character_mask_signature(id_result)
        visible_support = character_support(visible_result)
        id_support = character_support(id_result)
        bounds_equal = support_bounds(visible_support) == support_bounds(id_support)
        overlap = support_iou(visible_support, id_support)
        within_one_pixel = supports_within(visible_support, id_support, MAX_SUPPORT_DISTANCE)
        rows = line_count(geometry)
        max_segments = max((len(item.segments) for item in geometry), default=0)
        passed = (
            bounds_equal
            and within_one_pixel
            and error is None
            and len(boxes) == len(palette)
            and rows >= int(case.get("min_rows", 1))
            and max_segments >= int(case.get("min_max_segments", 1))
        )
        return {
            "id": case["id"],
            "expectation": case["expectation"],
            "visible_sha256": fixture_hash(visible_events),
            "id_sha256": fixture_hash(id_events),
            "geometry_equal": geometry_equal,
            "mask_equal": mask_equal,
            "support_bounds_equal": bounds_equal,
            "support_iou": overlap,
            "support_within_one_pixel": within_one_pixel,
            "boxes": boxes,
            "line_count": rows,
            "max_segments_per_token": max_segments,
            "extraction_error": error,
            "decision": "supported" if passed else "fallback",
        }
    finally:
        hit_map.close()
        visible.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = _load_manifest(args.manifest)
    libasslite = cast("LibassModule", importlib.import_module("libasslite"))
    renderer_type = libasslite.AssRenderer

    library_path = os.environ.get("LIBASSLITE_LIBRARY")
    probe = renderer_type(ass_bytes([]), library_path=library_path)
    try:
        version = probe.library_version()
        loaded_path = probe.library_path()
    finally:
        probe.close()
    font_hash, font_inventory = _font_inventory()
    cases = manifest["cases"]
    assert isinstance(cases, list)
    case_reports = [_case_report(renderer_type, case, library_path) for case in cases]
    required_core_passed = all(
        case["decision"] == "supported"
        for case in case_reports
        if case["expectation"] == "required-core"
    )
    report = {
        "schema": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "libass_version": f"0x{version:08x}",
        "library_path": loaded_path,
        "font_provider": None,
        "font_provider_evidence": os.environ.get(
            "LIBASSLITE_FONT_PROVIDER_EVIDENCE",
            "not exposed by public ABI; inspect libass stderr",
        ),
        "font_inventory_sha256": font_hash,
        "font_inventory_count": len(font_inventory),
        "font_inventory": font_inventory,
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "support_thresholds": {
            "maximum_chebyshev_distance_px": MAX_SUPPORT_DISTANCE,
            "require_equal_outer_bounds": True,
        },
        "required_core_passed": required_core_passed,
        "cases": case_reports,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not required_core_passed:
        raise SystemExit("required-core token-ID matrix failed")


if __name__ == "__main__":
    main()
