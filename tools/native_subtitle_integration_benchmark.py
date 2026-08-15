"""Post-integration evidence for the opt-in native-visible geometry path."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import psutil

from saitenka.app.subtitle_pipeline import (
    SubtitleGeometryWorker,
    SubtitleModeCoordinator,
)
from saitenka.app.subtitle_render import NullRenderer
from saitenka.subtitles import (
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    prepare_ass_hit_map,
)
from saitenka.subtitles.libass_backend import LibassGeometryBackend

STYLE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
LOCKED_MANIFEST_SHA256 = "bfd821e3fc22fe976b6027ce0ff8dbd4e0198c484d0f9bede0d6d0e9b5f94429"


def load_manifest(path: Path) -> dict:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != LOCKED_MANIFEST_SHA256:
        raise ValueError("native subtitle integration manifest changed without re-locking")
    manifest = json.loads(data)
    if manifest.get("schema") != 1:
        raise ValueError("unsupported native subtitle integration manifest schema")
    return manifest


def _time(milliseconds: int) -> str:
    seconds, milliseconds = divmod(milliseconds, 1_000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}"


def _source(count: int) -> tuple[bytes, tuple[tuple[int, int, str], ...]]:
    cues = tuple((1_000 + i * 2_000, 2_500 + i * 2_000, f"語{i:03d}") for i in range(count))
    events = "".join(
        f"Dialogue: 0,{_time(start)},{_time(end)},Default,,0,0,0,,{text}\n"
        for start, end, text in cues
    )
    return (STYLE + events).encode(), cues


def _percentile(samples: list[float], quantile: float) -> float:
    assert samples
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))]


def evaluate(report: dict, manifest: dict) -> bool:
    budgets = manifest["budgets"]
    return bool(
        report["event_count"] == manifest["event_count"]
        and report["interaction_p99_ms"] <= budgets["interaction_p99_ms"]
        and report["interaction_delta_p99_ms"] <= budgets["interaction_delta_p99_ms"]
        and report["ready_before_presentation_ratio"] >= budgets["ready_before_presentation_ratio"]
        and report["retained_rss_growth_mib"] <= budgets["retained_rss_growth_mib"]
        and report["result_cache_entries"] <= manifest["cache_max"]
        and report["prefetch_cache_entries"] <= manifest["cache_max"]
    )


def run(manifest: dict, *, library_path: Path | None = None) -> dict:
    count = int(manifest["event_count"])
    source, cues = _source(count)
    track_id = SubtitleTrackId("integration-benchmark")
    backend = LibassGeometryBackend(
        library_path=library_path,
        renderer_cache_max=int(manifest["cache_max"]),
    )
    coordinator = SubtitleModeCoordinator(NullRenderer(), backend)
    worker = SubtitleGeometryWorker(coordinator, cache_max=int(manifest["cache_max"]))
    process = psutil.Process()
    rss_before = process.memory_info().rss

    def builder(index: int, generation: int):
        start, end, text = cues[index]

        def build() -> GeometryRequest:
            prepared = prepare_ass_hit_map(
                source,
                track_id,
                start_ms=start,
                end_ms=end,
                text=text,
                tokens=(TokenAnnotation(0, 0, len(text)),),
            )
            return GeometryRequest(
                generation,
                track_id,
                prepared.event.decoded.source.identity,
                start + 1,
                (1280, 720),
                (1280, 720),
                prepared.ass,
                palette=prepared.palette,
                reserved_rgb=prepared.reserved_rgb,
            )

        return build

    latencies = []
    baseline_latencies = []
    first_generation = coordinator.generation
    assert worker.submit_job(first_generation, builder(0, first_generation))
    worker.mark_not_ready()
    assert worker.wait_idle(timeout=30)
    for index in range(1, min(count, 1 + int(manifest["lookahead"]))):
        key = f"cue:{index}"
        assert worker.prefetch(key, coordinator.generation, builder(index, coordinator.generation))
    assert worker.wait_idle(timeout=30)
    for index in range(1, count):
        baseline_started = time.perf_counter_ns()
        baseline_latencies.append((time.perf_counter_ns() - baseline_started) / 1_000_000)
        generation = coordinator.invalidate()
        started = time.perf_counter_ns()
        request = worker.publish_prefetched(f"cue:{index}", generation)
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        assert request is not None
        assert worker.mark_presented(request)
        future = index + int(manifest["lookahead"])
        if future < count:
            assert worker.prefetch(f"cue:{future}", generation, builder(future, generation))
            assert worker.wait_idle(timeout=30)
    stats = worker.stats
    rss_retained = process.memory_info().rss
    worker.invalidate_cache()
    rss_after_profile_switch = process.memory_info().rss
    baseline_p99 = _percentile(baseline_latencies, 0.99)
    interaction_p99 = _percentile(latencies, 0.99)
    report = {
        "schema": 1,
        "platform": platform.platform(),
        "python": sys.version,
        "event_count": count,
        "interaction_samples_ms": latencies,
        "interaction_p50_ms": statistics.median(latencies),
        "interaction_p99_ms": interaction_p99,
        "interaction_baseline_p99_ms": baseline_p99,
        "interaction_delta_p99_ms": max(0.0, interaction_p99 - baseline_p99),
        "ready_before_presentation_ratio": (
            stats.ready_before_presented / stats.presented if stats.presented else 0.0
        ),
        "submitted": stats.submitted,
        "completed": stats.completed,
        "prefetched": stats.prefetched,
        "prefetch_dropped": stats.prefetch_dropped,
        "result_cache_entries": stats.result_cache_entries,
        "prefetch_cache_entries": stats.prefetch_cache_entries,
        "rss_before_bytes": rss_before,
        "rss_retained_bytes": rss_retained,
        "rss_after_profile_switch_bytes": rss_after_profile_switch,
        "retained_rss_growth_mib": max(
            0, rss_retained - rss_before, rss_after_profile_switch - rss_before
        )
        / 1024
        / 1024,
    }
    report["integration_budgets_passed"] = evaluate(report, manifest)
    worker.close()
    report["rss_after_close_bytes"] = process.memory_info().rss
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library-path", type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    report = run(manifest, library_path=args.library_path)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["integration_budgets_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
