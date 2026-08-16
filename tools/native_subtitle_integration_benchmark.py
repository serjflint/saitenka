"""Post-integration evidence for the opt-in native-visible geometry path."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import platform
import statistics
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import psutil

from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
from saitenka.app.controller import Reader
from saitenka.panel import Definition, Entry
from saitenka.subtitles import (
    Cue,
    CueIndex,
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    prepare_ass_hit_map_frame,
)
from saitenka.subtitles.geometry import MAX_BITMAP_BYTES
from saitenka.subtitles.libass_backend import LibassGeometryBackend, extract_token_geometry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from saitenka.mpvio.ipc import MpvIPC

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
LOCKED_MANIFEST_SHA256 = "56cf4eacb9d34de8442c6bbf15b3999b05d4356246d7b2ad37e0710f1ad46ae5"


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
    cues = tuple((1_000 + i * 2_000, 2_500 + i * 2_000, f"語 {i:03d}") for i in range(count))
    events = "".join(
        f"Dialogue: 0,{_time(start)},{_time(end)},Default,,0,0,0,,{text}\n"
        for start, end, text in cues
    )
    return (STYLE + events).encode(), cues


def _simultaneous_source(count: int) -> tuple[bytes, str, str, tuple[TokenAnnotation, ...]]:
    rows = []
    for index in range(count):
        x = 30 + index % 8 * 150
        y = 30 + index // 8 * 75
        rows.append(
            f"Dialogue: {index},0:00:01.00,0:00:03.00,Default,,0,0,0,,"
            rf"{{\an7\pos({x},{y})}}猫"
        )
    active_rows = "\n".join(rows)
    text = "\n".join("猫" for _row in rows)
    annotations = tuple(TokenAnnotation(index, index * 2, index * 2 + 1) for index in range(count))
    return (STYLE + active_rows + "\n").encode(), active_rows, text, annotations


def _frame_workloads(counts: list[int], library_path: Path | None) -> list[dict]:
    libasslite = importlib.import_module("libasslite")
    reports = []
    for count in counts:
        source, active_rows, text, annotations = _simultaneous_source(count)
        track_id = SubtitleTrackId(f"simultaneous:{count}")
        started = time.perf_counter_ns()
        prepared = prepare_ass_hit_map_frame(
            source,
            track_id,
            active_rows=active_rows,
            text=text,
            tokens=annotations,
        )
        prepare_ms = (time.perf_counter_ns() - started) / 1_000_000
        request = GeometryRequest(
            0,
            track_id,
            prepared.frame_id,
            1_500,
            (1280, 720),
            (1280, 720),
            prepared.ass,
            palette=prepared.palette,
            reserved_rgb=prepared.reserved_rgb,
        )
        renderer = libasslite.AssRenderer(prepared.ass, library_path=library_path)
        try:
            started = time.perf_counter_ns()
            rendered = renderer.render(
                request.timestamp_ms,
                request.frame_size,
                request.storage_size,
                max_bitmap_bytes=min(2 * 1280 * 720, MAX_BITMAP_BYTES),
            )
            render_ms = (time.perf_counter_ns() - started) / 1_000_000
            started = time.perf_counter_ns()
            geometry = extract_token_geometry(rendered, request)
            extract_ms = (time.perf_counter_ns() - started) / 1_000_000
        finally:
            renderer.close()
        reports.append(
            {
                "active_events": count,
                "eligible_tokens": len(prepared.palette),
                "found_tokens": len(geometry),
                "prepare_ms": prepare_ms,
                "render_ms": render_ms,
                "extract_ms": extract_ms,
            }
        )
    return reports


def _percentile(samples: list[float], quantile: float) -> float:
    assert samples
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))]


def _performance_passes(report: dict, manifest: dict) -> bool:
    budgets = manifest["budgets"]
    return bool(
        report["interaction_cpu_p99_ms"] <= budgets["interaction_cpu_p99_ms"]
        and report["interaction_p99_ms"] <= budgets["interaction_wall_p99_ms"]
        and report["interaction_cpu_delta_p99_ms"] <= budgets["interaction_cpu_delta_p99_ms"]
        and report["interaction_wall_delta_p99_ms"] <= budgets["interaction_wall_delta_p99_ms"]
        and report["ready_before_presentation_ratio"] >= budgets["ready_before_presentation_ratio"]
        and report["retained_rss_growth_mib"] <= budgets["retained_rss_growth_mib"]
        and report["cadence_misses"] == 0
    )


def _functional_passes(report: dict, manifest: dict) -> bool:
    workloads = report.get("simultaneous_frame_workloads", [])
    expected_counts = manifest["simultaneous_event_counts"]
    return bool(
        report.get("schema") == 1
        and report["event_count"] == manifest["event_count"]
        and report["interaction_clock"] == manifest["interaction_clock"]
        and report["result_cache_entries"] <= manifest["cache_max"]
        and report["prefetch_cache_entries"] <= manifest["cache_max"]
        and report["presented"] == manifest["event_count"]
        and report["completed"] == manifest["event_count"]
        and report["geometry_apply_count"] == report["ready_before_presented"]
        and report["hit_test_count"] == report["presented"]
        and report["focus_draw_count"] == report["geometry_apply_count"]
        and report["tooltip_open_count"] == report["presented"]
        and report["tooltip_scroll_count"] == report["presented"]
        and report["failures"] == 0
        and report["last_error"] is None
        and report["superseded"] == 0
        and report["prefetch_dropped"] == 0
        and report["source_clear_current"] is False
        and report["source_clear_hit_count"] == 0
        and report["profile_switch_cache_entries"] == 0
        and report["close_completed"] is True
        and [item["active_events"] for item in workloads] == expected_counts
        and all(
            item["eligible_tokens"] == item["found_tokens"] == item["active_events"]
            for item in workloads
        )
    )


def evaluate(report: dict, manifest: dict) -> bool:
    return _performance_passes(report, manifest) and _functional_passes(report, manifest)


def summarize_trial_records(records: list[dict], manifest: dict) -> dict:
    expected = manifest["trials"]
    if len(records) > expected or expected < 3 or expected % 2 == 0:
        raise ValueError("native subtitle integration trials must match an odd locked denominator")
    snapshots = copy.deepcopy(records)
    reports = [record["report"] for record in snapshots if record["status"] == "completed"]
    performance_passes = sum(_performance_passes(report, manifest) for report in reports)
    functional_passed = len(reports) == expected and all(
        _functional_passes(report, manifest) for report in reports
    )
    required = expected // 2 + 1
    return {
        "schema": 2,
        "artifact_kind": "native-subtitle-integration-trials",
        "trial_count": expected,
        "completed_trials": len(reports),
        "required_performance_passes": required,
        "performance_passes": performance_passes,
        "all_functional_invariants_passed": functional_passed,
        "trials": snapshots,
        "integration_budgets_passed": performance_passes >= required and functional_passed,
    }


def summarize_trials(reports: list[dict], manifest: dict) -> dict:
    expected = manifest["trials"]
    if len(reports) != expected:
        raise ValueError("native subtitle integration trials must match an odd locked denominator")
    records = [
        {"index": index, "status": "completed", "report": report}
        for index, report in enumerate(reports, start=1)
    ]
    return summarize_trial_records(records, manifest)


def _write_artifact(output: Path, report: dict) -> None:
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)


def execute_trials(manifest: dict, library_path: Path | None, output: Path) -> dict:
    records: list[dict] = []
    for index in range(1, manifest["trials"] + 1):
        records.append({"index": index, "status": "running"})
        _write_artifact(output, summarize_trial_records(records, manifest))
        try:
            records[-1] = {
                "index": index,
                "status": "completed",
                "report": run(manifest, library_path=library_path),
            }
        except BaseException as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit))
            records[-1] = {
                "index": index,
                "status": "interrupted" if interrupted else "error",
                "error_type": type(error).__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
            }
            _write_artifact(output, summarize_trial_records(records, manifest))
            if interrupted:
                raise
            continue
        _write_artifact(output, summarize_trial_records(records, manifest))
    return summarize_trial_records(records, manifest)


class _IPC:
    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self.props = {
            "sid": 1,
            "sub-text/ass-full": "",
            "sub-start": 1.0,
            "sub-end": 2.5,
            "time-pos": 1.1,
            "pause": True,
            "osd-dimensions": {"w": 1280, "h": 720},
            "video-out-params": {"dw": 1280, "dh": 720, "w": 1280, "h": 720, "par": 1.0},
            "options/sub-ass-override": "no",
            "options/sub-ass-scale-with-window": False,
            "options/sub-scale": 1.0,
            "options/sub-pos": 100.0,
            "options/sub-use-margins": True,
            "options/sub-ass-force-margins": False,
            "options/sub-ass-video-aspect-override": 0.0,
            "options/sub-ass-use-video-data": "all",
            "options/sub-ass-vsfilter-aspect-compat": None,
            "options/sub-ass-style-overrides": [],
            "options/sub-font-provider": "auto",
            "options/embeddedfonts": False,
            "options/sub-fonts-dir": "",
        }

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"error": "success", "data": self.props.get(args[1])}
        return {"error": "success", "data": None}

    def close(self) -> None:
        pass


def _reader(ipc: _IPC, *, backend: LibassGeometryBackend | None = None) -> Reader:
    geometry = SubtitleGeometryOptions(native_visible=backend is not None, cache_max=3, lookahead=2)
    return Reader(
        cast("MpvIPC", ipc),
        options=ReaderOptions(subtitle_geometry=geometry, prefetch=False),
        renderer=None,
        geometry_backend=backend,
    )


@contextmanager
def _managed_readers(
    baseline_ipc: _IPC, native_ipc: _IPC, backend: LibassGeometryBackend
) -> Iterator[tuple[Reader, Reader]]:
    baseline: Reader | None = None
    native: Reader | None = None
    try:
        baseline = _reader(baseline_ipc)
        native = _reader(native_ipc, backend=backend)
        yield baseline, native
    finally:
        try:
            if baseline is not None:
                baseline.close()
        finally:
            try:
                if native is not None:
                    native.close()
            finally:
                backend.close()


class _TallDictionary:
    def entry_for(self, token, inflected=None, *, extra_terms=()):  # noqa: ARG002
        return Entry(
            headword=[token.surface],
            reading=token.reading or token.surface,
            defs=[Definition("辞書", [("定義\n" * 20).rstrip()])],
        )

    def has_term(self, *_forms):
        return False


def _present(reader: Reader, text: str, *, native: bool) -> bool:
    reader.set_subtitle(text)
    if native:
        assert reader.native_geometry is not None
        return reader.native_geometry.apply(reader)
    return bool(reader.boxes)


def _open_tooltip(reader: Reader, ipc: _IPC, *, native: bool) -> tuple[bool, bool, bool]:
    if not reader.boxes:
        return False, False, False
    box = reader.boxes[0]
    ox, oy = reader.sub_origin
    hit = reader._hit(ox + box.x + box.w / 2, oy + box.y + box.h / 2) == box.index
    before = len(ipc.commands)
    reader.hover = box.index
    reader._draw_subtitle()
    commands = ipc.commands[before:]
    focus = (
        any(command[:3] == ("osd-overlay", 1_001, "ass-events") for command in commands)
        if native
        else True
    )
    reader._show_tooltip(box.index)
    opened = reader._tip_state is not None
    return hit, focus, opened


def _scroll_and_close_tooltip(reader: Reader) -> bool:
    opened = reader._tip_state is not None
    reader._scroll_tip(1)
    scrolled = (
        opened
        and reader._scrolled_this_tick
        and reader._tip_state is not None
        and reader._tip_scroll > 0
    )
    reader._teardown_tip()
    reader.hover = -1
    return scrolled


def run(manifest: dict, *, library_path: Path | None = None) -> dict:
    count = int(manifest["event_count"])
    source, cues = _source(count)
    backend = LibassGeometryBackend(
        library_path=library_path,
        renderer_cache_max=int(manifest["cache_max"]),
    )
    process = psutil.Process()
    rss_before = process.memory_info().rss
    simultaneous_frame_workloads = _frame_workloads(
        [int(value) for value in manifest["simultaneous_event_counts"]], library_path
    )
    baseline_ipc = _IPC()
    native_ipc = _IPC()
    with (
        tempfile.TemporaryDirectory(prefix="saitenka-stage-f-") as raw_workspace,
        _managed_readers(baseline_ipc, native_ipc, backend) as readers,
    ):
        baseline, native = readers
        source_path = Path(raw_workspace) / "integration.ass"
        source_path.write_bytes(source)
        baseline.dict_set = _TallDictionary()
        native.dict_set = _TallDictionary()
        assert native.native_geometry is not None
        native.native_geometry.set_source(source_path, reader=native)
        index = CueIndex([Cue(start / 1_000, end / 1_000, text) for start, end, text in cues])
        native._sub_index = index
        latencies: list[float] = []
        baseline_latencies: list[float] = []
        cpu_latencies: list[float] = []
        baseline_cpu_latencies: list[float] = []
        cpu_deltas: list[float] = []
        wall_deltas: list[float] = []
        cadence_misses = 0
        geometry_apply_count = 0
        hit_test_count = 0
        focus_draw_count = 0
        tooltip_open_count = 0
        tooltip_scroll_count = 0
        interval_ns = int(manifest["presentation_interval_ms"] * 1_000_000)
        deadline = time.perf_counter_ns()
        for cue_index, (start, end, text) in enumerate(cues):
            deadline += interval_ns if cue_index else 0
            remaining = deadline - time.perf_counter_ns()
            if remaining > 0:
                time.sleep(remaining / 1_000_000_000)
            elif -remaining > interval_ns:
                cadence_misses += 1
            for ipc in (baseline_ipc, native_ipc):
                ipc.props.update(
                    {
                        "sub-text/ass-full": (
                            f"Dialogue: 0,{_time(start)},{_time(end)},"
                            f"Default,,0000,0000,0000,,{text}"
                        ),
                        "sub-start": start / 1_000,
                        "sub-end": end / 1_000,
                        "time-pos": (start + 1) / 1_000,
                    }
                )
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            _present(baseline, text, native=False)
            baseline_wall = (time.perf_counter_ns() - started) / 1_000_000
            baseline_cpu = (time.thread_time_ns() - cpu_started) / 1_000_000
            baseline_latencies.append(baseline_wall)
            baseline_cpu_latencies.append(baseline_cpu)
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            applied = _present(native, text, native=True)
            geometry_apply_count += int(applied)
            native_wall = (time.perf_counter_ns() - started) / 1_000_000
            latencies.append(native_wall)
            wall_deltas.append(max(0.0, native_wall - baseline_wall))
            native_cpu = (time.thread_time_ns() - cpu_started) / 1_000_000
            cpu_latencies.append(native_cpu)
            cpu_deltas.append(max(0.0, native_cpu - baseline_cpu))
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            _open_tooltip(baseline, baseline_ipc, native=False)
            baseline_wall = (time.perf_counter_ns() - started) / 1_000_000
            baseline_cpu = (time.thread_time_ns() - cpu_started) / 1_000_000
            baseline_latencies.append(baseline_wall)
            baseline_cpu_latencies.append(baseline_cpu)
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            hit, focus, opened = _open_tooltip(native, native_ipc, native=True)
            hit_test_count += int(hit)
            focus_draw_count += int(focus)
            tooltip_open_count += int(opened)
            native_wall = (time.perf_counter_ns() - started) / 1_000_000
            latencies.append(native_wall)
            wall_deltas.append(max(0.0, native_wall - baseline_wall))
            native_cpu = (time.thread_time_ns() - cpu_started) / 1_000_000
            cpu_latencies.append(native_cpu)
            cpu_deltas.append(max(0.0, native_cpu - baseline_cpu))
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            _scroll_and_close_tooltip(baseline)
            baseline_wall = (time.perf_counter_ns() - started) / 1_000_000
            baseline_cpu = (time.thread_time_ns() - cpu_started) / 1_000_000
            baseline_latencies.append(baseline_wall)
            baseline_cpu_latencies.append(baseline_cpu)
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            scrolled = _scroll_and_close_tooltip(native)
            tooltip_scroll_count += int(scrolled)
            native_wall = (time.perf_counter_ns() - started) / 1_000_000
            latencies.append(native_wall)
            wall_deltas.append(max(0.0, native_wall - baseline_wall))
            native_cpu = (time.thread_time_ns() - cpu_started) / 1_000_000
            cpu_latencies.append(native_cpu)
            cpu_deltas.append(max(0.0, native_cpu - baseline_cpu))
        assert native.native_geometry.worker.wait_idle(timeout=30)
        stats = native.native_geometry.worker.stats
        last_error = native.subtitle_pipeline.last_error
        rss_retained = process.memory_info().rss
        native.use_tokenizer(native.tokenizer)
        profile_switch_cache_entries = sum(
            (
                native.native_geometry.worker.stats.result_cache_entries,
                native.native_geometry.worker.stats.prefetch_cache_entries,
            )
        )
        rss_after_profile_switch = process.memory_info().rss
        native.native_geometry.set_source(None, reader=native)
        source_clear_current = native.subtitle_pipeline.current is not None
        source_clear_hit_count = len(native.boxes)
    close_completed = backend.closed
    baseline_p99 = _percentile(baseline_latencies, 0.99)
    interaction_p99 = _percentile(latencies, 0.99)
    baseline_cpu_p99 = _percentile(baseline_cpu_latencies, 0.99)
    interaction_cpu_p99 = _percentile(cpu_latencies, 0.99)
    report = {
        "schema": 1,
        "platform": platform.platform(),
        "python": sys.version,
        "event_count": count,
        "simultaneous_frame_workloads": simultaneous_frame_workloads,
        "interaction_samples_ms": latencies,
        "interaction_cpu_samples_ms": cpu_latencies,
        "interaction_cpu_delta_samples_ms": cpu_deltas,
        "interaction_wall_delta_samples_ms": wall_deltas,
        "interaction_clock": "thread_time",
        "interaction_p50_ms": statistics.median(latencies),
        "interaction_p99_ms": interaction_p99,
        "interaction_baseline_p99_ms": baseline_p99,
        "interaction_cpu_p99_ms": interaction_cpu_p99,
        "interaction_baseline_cpu_p99_ms": baseline_cpu_p99,
        "interaction_cpu_delta_p99_ms": _percentile(cpu_deltas, 0.99),
        "interaction_wall_delta_p99_ms": _percentile(wall_deltas, 0.99),
        "ready_before_presentation_ratio": (
            stats.ready_before_presented / stats.presented if stats.presented else 0.0
        ),
        "ready_before_presented": stats.ready_before_presented,
        "geometry_apply_count": geometry_apply_count,
        "hit_test_count": hit_test_count,
        "focus_draw_count": focus_draw_count,
        "tooltip_open_count": tooltip_open_count,
        "tooltip_scroll_count": tooltip_scroll_count,
        "submitted": stats.submitted,
        "completed": stats.completed,
        "prefetched": stats.prefetched,
        "superseded": stats.superseded,
        "failures": stats.failures,
        "last_error": last_error,
        "prefetch_dropped": stats.prefetch_dropped,
        "presented": stats.presented,
        "cadence_misses": cadence_misses,
        "result_cache_entries": stats.result_cache_entries,
        "prefetch_cache_entries": stats.prefetch_cache_entries,
        "profile_switch_cache_entries": profile_switch_cache_entries,
        "source_clear_current": source_clear_current,
        "source_clear_hit_count": source_clear_hit_count,
        "close_completed": close_completed,
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
    report["rss_after_close_bytes"] = process.memory_info().rss
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library-path", type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    report = execute_trials(manifest, args.library_path, args.output)
    print(json.dumps(report, indent=2))
    return 0 if report["integration_budgets_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
