"""What each color device and each renderer costs, measured apart instead of as one cue.

Three legs draw a colored cue and they were only ever timed together, as `cue_redraw`. That made two
questions unanswerable from any report: whether the raster device is expensive relative to the text
one, and whether the measuring renderer or the drawing renderer dominates. A session read
`subtitle_geometry_libass` at p50 31.7 ms against `cue_redraw` at 2.1 ms, which looks like a verdict
and is not one — the two do different jobs at different rates, and only one of them was instrumented.

So this prices them on the same synthetic cue, per token, so the numbers divide:

* **device 1** — the overprint: one `\\pos`-ed ASS event per drawn token (per glyph when spaced).
* **device 2** — the overpaint: the coverage masks composited into one RGBA raster.
* **device 3** — the decoration rules, measured as the delta device 1 grows by when the same cue
  carries JLPT underlines.
* **ladder** — the classification itself, which every one of the above pays before it draws.
* **libasslite** — our measuring renderer, split into libass's own render and OUR extraction, via
  the backend's telemetry port rather than a wrapper around it.
* **legacy** — the PIL renderer that draws and colors the cue by itself.

**mpv's OSD leg is absent on purpose.** Its cost is a `compute_bounds` round trip into another
process, so it cannot be measured deterministically or headlessly; `saitenka.subtitle.calibration_ms`
records it in a live session instead. A number here that pretended to cover it would be the wall-clock
proxy `BENCHMARKS.md` warns against.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FRAME = (1920, 1080)
#: A cue of realistic shape: several short tokens and a couple of multi-glyph ones, which is what
#: makes the per-glyph split path reachable rather than a special case.
CUE_TOKENS = ("猫", "を", "見る", "犬", "が", "走った", "から", "面白い")


@dataclass(frozen=True, slots=True)
class DeviceBenchmarkConfig:
    reps: int = 40
    tokens: int = 24
    #: Coverage side, in pixels, for one token's mask. Device 2's cost is area-driven, so this is
    #: the knob that moves it and it is stated rather than buried in a fixture.
    mask_side: int = 48


class _DiscardingSpan:
    """The backend sets span attributes as it works; this benchmark reads only its histograms."""

    def set(self, key: str, value: object) -> None:
        pass


@dataclass
class _Collector:
    """The backend's telemetry port, kept as a list so the render/extract split is exact.

    Timing the backend from outside gives one number that is 97% extraction, which is the very
    conflation this benchmark exists to undo.
    """

    samples: dict[str, list[float]] = field(default_factory=dict)

    @contextlib.contextmanager
    def span(self, name: str):
        del name
        yield _DiscardingSpan()

    def record(self, metric: str, milliseconds: float) -> None:
        self.samples.setdefault(metric, []).append(milliseconds)


def _percentile(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]


def _timed(call, reps: int) -> list[float]:
    call()  # once outside the samples: the first call pays imports and any lazy cache build
    samples = []
    for _ in range(reps):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def _entry(name: str, unit: str, value: float) -> dict[str, Any]:
    return {"name": name, "unit": unit, "value": round(value, 4)}


class _Style:
    def __init__(self, color, underline=None) -> None:
        self.color = color
        self.underline = underline


def _coverage(side: int) -> bytes:
    """A deterministic non-uniform alpha mask of exactly ``side * side`` bytes.

    Both properties are load-bearing. `TokenMask.usable` demands the length equal width×height, and a
    mask that fails it is silently dropped — the first draft sized 48×48 masks for 120×48 boxes, so
    device 2 composed NOTHING and its timing was of an empty call. Non-uniform because a flat buffer
    is the one shape a vectorised tint could special-case.
    """
    return bytes((index * 37 + (index // side) * 11) % 256 for index in range(side * side))


def _draw_request(tokens: int, *, underlines: bool, mask_side: int = 0):
    from saitenka_tokenize.japanese import Token

    from saitenka.app.subtitle_render import DrawRequest
    from saitenka.app.subtitles import WordBox

    surfaces = [CUE_TOKENS[index % len(CUE_TOKENS)] for index in range(tokens)]
    line = [Token(s, s, s, "名詞", index, index + 1) for index, s in enumerate(surfaces)]
    # A box the coverage fits exactly, so the raster leg is measured rather than skipped.
    width = height = mask_side or 120
    coverage = _coverage(mask_side) if mask_side else b""
    boxes = [
        WordBox(
            index,
            40 + (index % 12) * (width + 20),
            900 + (index // 12) * (height + 12),
            width,
            height,
            "Arial",
            48.0,
            coverage,
        )
        for index in range(tokens)
    ]
    styles = [
        _Style((255, 0, 0, 255), underline=(0, 128, 255, 255) if underlines else None)
        for _ in range(tokens)
    ]
    return DrawRequest(
        text="".join(surfaces),
        lines=[line],
        osd=FRAME,
        sub_size=44,
        bg_opacity=150,
        bottom_margin=40,
        secondary_role=False,
        upgrade_pending=False,
        annotation_degraded=False,
        annotation_visible=True,
        hover=-1,
        hover_span=None,
        styles=styles,
        boxes=boxes,
    )


def benchmark_devices(config: DeviceBenchmarkConfig) -> dict[str, float]:
    """Devices 1, 2 and 3, plus the classification all three pay first."""
    from saitenka.app.subtitle_render import color_ladder, overpaint_image, overprint_payload

    plain = _draw_request(config.tokens, underlines=False)
    ruled = _draw_request(config.tokens, underlines=True)
    # Device 2 only takes a token device 1 refused, so its cue is faceless AND carries coverage.
    rastered = _replace_faces(
        _draw_request(config.tokens, underlines=False, mask_side=config.mask_side)
    )

    ladder = _timed(lambda: color_ladder(plain), config.reps)
    device1 = _timed(lambda: overprint_payload(plain), config.reps)
    device13 = _timed(lambda: overprint_payload(ruled), config.reps)
    device2 = _timed(lambda: overpaint_image(rastered), config.reps)

    image = overpaint_image(rastered)
    if image is None:
        # `compose` returns None when every mask is unusable, and an empty call still times fast —
        # so without this the benchmark reports device 2 as the cheapest leg precisely when it did
        # no work at all. This exact fixture bug shipped once already.
        raise AssertionError("device 2 composed nothing: the masks were rejected as unusable")
    if not overprint_payload(plain):
        raise AssertionError("device 1 drew nothing: the fixture's tokens are not drawable")
    return {
        "ladder_p50_ms": statistics.median(ladder),
        "device1_p50_ms": statistics.median(device1),
        "device1_p95_ms": _percentile(device1, 0.95),
        "device1_per_token_us": statistics.median(device1) * 1000.0 / config.tokens,
        "device3_delta_p50_ms": statistics.median(device13) - statistics.median(device1),
        "device2_p50_ms": statistics.median(device2),
        "device2_p95_ms": _percentile(device2, 0.95),
        "device2_per_token_us": statistics.median(device2) * 1000.0 / config.tokens,
        "device2_pixels": 0 if image is None else int(image.rgba.shape[0] * image.rgba.shape[1]),
    }


def _replace_faces(request):
    """Strip the measured face so every token falls to device 2, which is the only way to price the
    raster leg on its own — with a face present, device 1 takes the token and device 2 gets nothing.
    """
    import dataclasses

    from saitenka.app.subtitles import WordBox

    boxes = [WordBox(b.index, b.x, b.y, b.w, b.h, "", 0.0, b.coverage) for b in request.boxes]
    return dataclasses.replace(request, boxes=boxes)


def benchmark_legacy(config: DeviceBenchmarkConfig) -> dict[str, float]:
    """The legacy renderer, which draws the glyphs and colors them in one PIL pass."""
    from saitenka_tokenize.japanese import Token

    from saitenka.app.subtitles import render_subtitle

    surfaces = [CUE_TOKENS[index % len(CUE_TOKENS)] for index in range(config.tokens)]
    lines = [[Token(s, s, s, "名詞", index, index + 1) for index, s in enumerate(surfaces)]]
    styles = [_Style((255, 0, 0, 255)) for _ in range(config.tokens)]

    samples = _timed(lambda: render_subtitle(lines, FRAME[0], 44, styles=styles), config.reps)
    return {
        "legacy_p50_ms": statistics.median(samples),
        "legacy_p95_ms": _percentile(samples, 0.95),
        "legacy_per_token_us": statistics.median(samples) * 1000.0 / config.tokens,
    }


def _geometry_request(tokens: int):
    from saitenka_subtitles import SubtitleTrackId, TokenAnnotation
    from saitenka_subtitles.ass_geometry import prepare_ass_hit_map_frame
    from saitenka_subtitles.geometry import GeometryRequest

    surfaces = [CUE_TOKENS[index % len(CUE_TOKENS)] for index in range(tokens)]
    text = "".join(surfaces)
    spans, cursor = [], 0
    for index, surface in enumerate(surfaces):
        spans.append(TokenAnnotation(index, cursor, cursor + len(surface)))
        cursor += len(surface)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {FRAME[0]}\nPlayResY: {FRAME[1]}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        "Style: D,sans-serif,48,&H00FFFFFF,&H00000000,&H00000000,0,0,1,0,0,7,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    row = f"Dialogue: 0,0:00:00.50,0:00:08.00,D,,0,0,0,,{{\\pos(60,900)}}{text}"
    track = SubtitleTrackId("bench")
    prepared = prepare_ass_hit_map_frame(
        (header + row + "\n").encode(), track, active_rows=row, text=text, tokens=tuple(spans)
    )
    return GeometryRequest(
        1,
        track,
        prepared.frame_id,
        2_000,
        FRAME,
        FRAME,
        prepared.ass,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
    )


def benchmark_libasslite(config: DeviceBenchmarkConfig) -> dict[str, float] | None:
    """Our measuring renderer, split into libass's render and our extraction.

    `None` when libass is not installed — this is the one leg with an optional native dependency, and
    a benchmark that silently reported 0 for it would read as "free" rather than "not measured".
    """
    from saitenka_subtitles.libass_backend import LibassGeometryBackend
    from saitenka_subtitles.telemetry import (
        EXTRACT_COLLECT_MS,
        EXTRACT_COVERAGE_MS,
        EXTRACT_MS,
        EXTRACT_OWNERS_MS,
        EXTRACT_VALIDATE_MS,
        RENDER_MS,
    )

    collector = _Collector()
    backend = LibassGeometryBackend(telemetry=collector)
    try:
        request = _geometry_request(config.tokens)
        try:
            if not backend.render(request).tokens:
                return None
        except Exception:
            return None
        samples = _timed(lambda: backend.render(request), config.reps)
        # The coverage phase reads 0 unless the snapshot is asked to keep the masks, which happens
        # only once a face is demoted to the raster device. Measured on its own request so the
        # number is "what feeding device 2 costs upstream" rather than "not exercised".
        covered = _Collector()
        backend._telemetry = covered
        _timed(
            lambda: backend.render(dataclasses.replace(request, keep_coverage=True)), config.reps
        )
        collector.samples[EXTRACT_COVERAGE_MS] = covered.samples.get(EXTRACT_COVERAGE_MS, [])
    finally:
        backend.close()

    render_ms = collector.samples.get(RENDER_MS, [])
    extract_ms = collector.samples.get(EXTRACT_MS, [])
    if not render_ms or not extract_ms:
        return None
    total = statistics.median(samples)
    phases = {
        f"libasslite_{label}_p50_ms": statistics.median(collector.samples[metric])
        for label, metric in (
            ("owners", EXTRACT_OWNERS_MS),
            ("collect", EXTRACT_COLLECT_MS),
            ("validate", EXTRACT_VALIDATE_MS),
            ("coverage", EXTRACT_COVERAGE_MS),
        )
        if collector.samples.get(metric)
    }
    return {
        **phases,
        "libasslite_p50_ms": total,
        "libasslite_p95_ms": _percentile(samples, 0.95),
        "libasslite_render_p50_ms": statistics.median(render_ms),
        "libasslite_extract_p50_ms": statistics.median(extract_ms),
        # The share that is ours rather than libass's. A session read 97.4%, which is what says the
        # extraction is the thing to optimise and the renderer is not.
        "libasslite_extract_share_pct": 100.0
        * statistics.median(extract_ms)
        / max(statistics.median(extract_ms) + statistics.median(render_ms), 1e-9),
        "libasslite_per_token_us": total * 1000.0 / config.tokens,
    }


def run(config: DeviceBenchmarkConfig, output: Path) -> list[dict[str, Any]]:
    if min(config.reps, config.tokens, config.mask_side) < 1:
        raise ValueError("benchmark sizes and repetitions must be positive")
    devices = benchmark_devices(config)
    legacy = benchmark_legacy(config)
    geometry = benchmark_libasslite(config)

    result = [
        _entry("ladder: classify one cue", "ms", devices["ladder_p50_ms"]),
        _entry("device 1: overprint payload p50", "ms", devices["device1_p50_ms"]),
        _entry("device 1: overprint payload p95", "ms", devices["device1_p95_ms"]),
        _entry("device 1: per token", "us", devices["device1_per_token_us"]),
        _entry("device 2: overpaint compose p50", "ms", devices["device2_p50_ms"]),
        _entry("device 2: overpaint compose p95", "ms", devices["device2_p95_ms"]),
        _entry("device 2: per token", "us", devices["device2_per_token_us"]),
        _entry("device 3: decoration delta", "ms", devices["device3_delta_p50_ms"]),
        _entry("legacy renderer: full cue p50", "ms", legacy["legacy_p50_ms"]),
        _entry("legacy renderer: per token", "us", legacy["legacy_per_token_us"]),
    ]
    if geometry is not None:
        result += [
            _entry("libasslite: measure one cue p50", "ms", geometry["libasslite_p50_ms"]),
            _entry("libasslite: libass render p50", "ms", geometry["libasslite_render_p50_ms"]),
            _entry("libasslite: our extraction p50", "ms", geometry["libasslite_extract_p50_ms"]),
            _entry("libasslite: extraction share", "pct", geometry["libasslite_extract_share_pct"]),
            _entry("libasslite: per token", "us", geometry["libasslite_per_token_us"]),
        ]
        # The four phases inside that extraction, which was the number with no answer in it.
        result += [
            _entry(f"libasslite extract: {label}", "ms", geometry[key])
            for label, key in (
                ("owners map alloc", "libasslite_owners_p50_ms"),
                ("collect layers", "libasslite_collect_p50_ms"),
                ("validate tokens", "libasslite_validate_p50_ms"),
                ("coverage masks", "libasslite_coverage_p50_ms"),
            )
            if key in geometry
        ]
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("bench-devices.json"))
    parser.add_argument("--reps", type=int, default=40)
    parser.add_argument("--tokens", type=int, default=24)
    parser.add_argument("--mask-side", type=int, default=48)
    args = parser.parse_args(argv)
    result = run(
        DeviceBenchmarkConfig(reps=args.reps, tokens=args.tokens, mask_side=args.mask_side),
        args.output,
    )
    width = max(len(item["name"]) for item in result)
    for item in result:
        print(f"{item['name']:{width}}  {item['value']:10.3f} {item['unit']}")
    if not any(item["name"].startswith("libasslite") for item in result):
        print("\nlibasslite: NOT MEASURED (libass unavailable) — not zero, absent")
    print(
        "\nmpv's OSD leg is not measurable here; see saitenka.subtitle.calibration_ms in a session"
    )
    print(f"wrote → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
