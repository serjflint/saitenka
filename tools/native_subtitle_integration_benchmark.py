"""Post-integration evidence for the opt-in native-visible geometry path."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import operator
import os
import platform
import statistics
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

import psutil

from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
from saitenka.app.embedded_subs import resolve_track_fonts
from saitenka.app.session.factory import SessionInfrastructure, create_session_controller
from saitenka.panel import Definition, Entry
from saitenka.runtime.jobs import NoSessionRuntime
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
    from collections.abc import Callable, Iterator

    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.session.controller import SessionController
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
LOCKED_MANIFEST_SHA256 = "830656a8ecee78c72b06fb2de03d7424abae9582c37fd71e7026870efa78d338"


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


class _Budget(NamedTuple):
    """One budget clause: where its value lives in a report, which way the comparison runs, and how
    several trials of it combine.

    The comparator travels with the clause because the senses are mixed — `ready_before_presentation_ratio`
    is a floor while the latency percentiles are ceilings, and an aggregator that assumed one
    direction would make the other unfailable.
    """

    metric: str
    compare: Callable[[float, float], bool]
    across_trials: str  # "median" | "every"


#: A p99 taken over one trial's ~300 samples is the third-worst sample, so it moves with a
#: scheduler hiccup rather than with our code. The latency clauses therefore judge the MEDIAN of
#: the per-trial p99s — the same thresholds, estimated from three measurements instead of one.
#: Replayed over 52 archived macOS runs: 4 failures become 3, with none newly failing.
#: `retained_rss_growth_mib` is a leak meter, not a noise-symmetric percentile, so one bad trial is
#: a leak and it stays conjunctive.
BUDGET_CLAUSES = {
    "interaction_cpu_p99_ms": _Budget("interaction_cpu_p99_ms", operator.le, "median"),
    "interaction_wall_p99_ms": _Budget("interaction_p99_ms", operator.le, "median"),
    "interaction_cpu_delta_p99_ms": _Budget("interaction_cpu_delta_p99_ms", operator.le, "median"),
    "interaction_wall_delta_p99_ms": _Budget(
        "interaction_wall_delta_p99_ms", operator.le, "median"
    ),
    "ready_before_presentation_ratio": _Budget(
        "ready_before_presentation_ratio", operator.ge, "median"
    ),
    "retained_rss_growth_mib": _Budget("retained_rss_growth_mib", operator.le, "every"),
}


def trial_is_valid(report: dict) -> bool:
    """A trial that missed its presentation cadence measured a stalled runner, not our code.

    Its latency numbers describe the stall, so it is discarded rather than counted as a failure —
    otherwise the noisiest thing the harness can observe becomes the clause most likely to fail.
    """
    return report["cadence_misses"] == 0


def _performance_checks(report: dict, manifest: dict) -> dict[str, bool]:
    """Every clause against ONE trial. Still the per-trial view the summary prints and the
    clean-trial clause needs; the run-level verdict is :func:`performance_verdict`."""
    budgets = manifest["budgets"]
    checks = {
        name: clause.compare(report[clause.metric], budgets[name])
        for name, clause in BUDGET_CLAUSES.items()
    }
    checks["cadence_misses"] = trial_is_valid(report)
    return checks


def _performance_passes(report: dict, manifest: dict) -> bool:
    return all(_performance_checks(report, manifest).values())


#: How far past its budget one trial may sit while the others vote it down. The worst single-trial
#: breach across the 52 archived runs this rule accepts is 1.59x, so 2.0 clears real runner noise
#: with room to spare while still refusing an outlier no scheduler hiccup explains. Without it,
#: "the median absorbs one bad trial" has no upper bound and a 4x breach reads the same as a 1.1x.
OUTLIER_TOLERANCE = 2.0


def _within_tolerance(value: float, budget: float, clause: _Budget) -> bool:
    limit = (
        budget * OUTLIER_TOLERANCE if clause.compare is operator.le else budget / OUTLIER_TOLERANCE
    )
    return clause.compare(value, limit)


def _representative(values: list[float], clause: _Budget) -> float:
    """The middle measurement — never an interpolation between two.

    `statistics.median` averages the two middle values at even counts, and discarding a
    cadence-stalled trial makes an even count routine: 32.0 and 0.5 would average to 16.25 and pass
    a 16.67 budget that one of the two trials missed by 92%. Taking the worse side keeps this an
    order statistic, and at odd counts all three medians agree.
    """
    return (
        statistics.median_high(values)
        if clause.compare is operator.le
        else statistics.median_low(values)
    )


def performance_verdict(reports: list[dict], manifest: dict) -> dict:
    """The run-level performance verdict over every completed trial.

    Judging each clause on the median is a **relaxation** of the per-trial quorum it replaces — a
    run the quorum passes always passes this, never the reverse — so it carries two floors the
    median alone does not:

    * at least one valid trial passes every clause, or three trials each breaching a different
      clause would aggregate to green with nothing clean anywhere;
    * no single trial breaches a clause by more than :data:`OUTLIER_TOLERANCE`, which bounds what
      "one bad trial" is allowed to mean.
    """
    budgets = manifest["budgets"]
    required_valid = manifest["trials"] // 2 + 1
    valid = [report for report in reports if trial_is_valid(report)]
    clauses: dict[str, bool] = {}
    for name, clause in BUDGET_CLAUSES.items():
        values = [report[clause.metric] for report in valid]
        if not values:
            clauses[name] = False
            continue
        within = all(_within_tolerance(value, budgets[name], clause) for value in values)
        if clause.across_trials == "median":
            clauses[name] = within and clause.compare(
                _representative(values, clause), budgets[name]
            )
        else:
            clauses[name] = all(clause.compare(value, budgets[name]) for value in values)
    clean_trials = sum(all(_performance_checks(report, manifest).values()) for report in valid)
    return {
        "valid_trials": len(valid),
        "required_valid_trials": required_valid,
        "clean_trials": clean_trials,
        "clauses": clauses,
        "passed": len(valid) >= required_valid and clean_trials >= 1 and all(clauses.values()),
    }


def _functional_checks(report: dict, manifest: dict) -> dict[str, bool]:
    """Every functional invariant, named, so a failure says which clause rather than only that one
    failed."""
    # The schema guard short-circuits, as it did when this was one `and` chain: every clause below
    # subscripts keys a foreign schema need not carry, and a `KeyError` here would kill the run
    # instead of recording the functional failure this check exists to record.
    if report.get("schema") != 1:
        return {"schema": False}
    workloads = report.get("simultaneous_frame_workloads", [])
    return {
        "schema": True,
        "event_count": report["event_count"] == manifest["event_count"],
        "interaction_clock": report["interaction_clock"] == manifest["interaction_clock"],
        "result_cache_bounded": report["result_cache_entries"] <= manifest["cache_max"],
        "prefetch_cache_bounded": report["prefetch_cache_entries"] <= manifest["cache_max"],
        "presented_every_cue": report["presented"] == manifest["event_count"],
        "completed_every_cue": report["completed"] == manifest["event_count"],
        "applied_what_was_ready": (
            report["geometry_apply_count"] == report["ready_before_presented"]
        ),
        "hit_tested_every_presentation": report["hit_test_count"] == report["presented"],
        # Against presentations, not applications: the two are sampled on opposite sides of the
        # geometry lane's terminal, so the one cold cue that presents before its geometry lands
        # separates them by exactly one with nothing wrong. Every presented cue drawing focus is
        # both the stricter claim and the one that survives an asynchronous pipeline.
        "focus_drew_every_presentation": report["focus_draw_count"] == report["presented"],
        "tooltip_opened_every_presentation": report["tooltip_open_count"] == report["presented"],
        "tooltip_scrolled_every_presentation": (
            report["tooltip_scroll_count"] == report["presented"]
        ),
        "no_failures": report["failures"] == 0,
        "no_last_error": report["last_error"] is None,
        "nothing_superseded": report["superseded"] == 0,
        "no_prefetch_dropped": report["prefetch_dropped"] == 0,
        "source_cleared": report["source_clear_current"] is False,
        "no_source_clear_hits": report["source_clear_hit_count"] == 0,
        "profile_switch_evicted_caches": report["profile_switch_cache_entries"] == 0,
        "closed_cleanly": report["close_completed"] is True,
        "workload_denominator": (
            [item["active_events"] for item in workloads] == manifest["simultaneous_event_counts"]
        ),
        "workload_tokens_all_found": all(
            item["eligible_tokens"] == item["found_tokens"] == item["active_events"]
            for item in workloads
        ),
    }


def _functional_passes(report: dict, manifest: dict) -> bool:
    return all(_functional_checks(report, manifest).values())


def evaluate(report: dict, manifest: dict) -> bool:
    return _performance_passes(report, manifest) and _functional_passes(report, manifest)


def summarize_trial_records(records: list[dict], manifest: dict) -> dict:
    expected = manifest["trials"]
    if len(records) > expected or expected < 3 or expected % 2 == 0:
        raise ValueError("native subtitle integration trials must match an odd locked denominator")
    snapshots = copy.deepcopy(records)
    reports = [record["report"] for record in snapshots if record["status"] == "completed"]
    functional_passed = len(reports) == expected and all(
        _functional_passes(report, manifest) for report in reports
    )
    performance = performance_verdict(reports, manifest)
    return {
        "schema": 3,
        "artifact_kind": "native-subtitle-integration-trials",
        "trial_count": expected,
        "completed_trials": len(reports),
        "performance": performance,
        "all_functional_invariants_passed": functional_passed,
        "trials": snapshots,
        "integration_budgets_passed": (
            len(reports) == expected and performance["passed"] and functional_passed
        ),
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


class _IPC(NoSessionRuntime):
    """Headless stand-in: no gateway behind it, so it refuses lanes rather than lacking the port."""

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
            "options/sub-ass-style-overrides": [],
            "options/sub-scale-with-window": True,
            "options/sub-scale-by-window": True,
            "options/blend-subtitles": False,
            "options/sub-filter-sdh": False,
            # `converted.STYLE_OPTIONS`, as mpv 0.41 reports them with no config: a value
            # missing here reads `None`, and the style falls back to a default the trial is
            # not measuring.
            "options/sub-font-size": 38.0,
            "options/sub-color": "#FFFFFFFF",
            "options/sub-outline-color": "#FF000000",
            "options/sub-back-color": "#AF000000",
            "options/sub-border-style": "outline-and-shadow",
            "options/sub-outline-size": 1.65,
            "options/sub-shadow-offset": 0.0,
            "options/sub-spacing": 0.0,
            "options/sub-margin-x": 19,
            "options/sub-margin-y": 34,
            "options/sub-align-x": "center",
            "options/sub-align-y": "bottom",
            "options/sub-blur": 0.0,
            "options/sub-bold": False,
            "options/sub-italic": False,
            "options/sub-justify": "auto",
            "options/video-crop": "",
            "options/video-rotate": 0,
            "options/sub-font-provider": "auto",
            "options/embeddedfonts": False,
            "options/sub-fonts-dir": "",
            "options/sub-font": "sans-serif",
            "options/osd-fonts-dir": "",
            "options/osd-font-provider": "auto",
        }

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"error": "success", "data": self.props.get(args[1])}
        if args[:1] == ("set_property",):
            # A write mpv would honour has to be readable afterwards. Ownership of the subtitle
            # pixels is decided by asserting `sub-visibility` and reading it back, so a fake that
            # drops writes leaves that assertion permanently unresolved — and every geometry apply
            # then declines, which reads as the native path producing nothing.
            self.props[args[1]] = args[2]
            return {"error": "success", "data": None}
        return {"error": "success", "data": None}

    def query(self, name: str) -> object | None:
        """Routed through `command`, not read off `props`: one read path, so the trial's command log
        stays the record of everything the renderer actually asked mpv."""
        return self.command("get_property", name).get("data")

    def expand_path(self, path: str) -> str:
        """mpv expands its own `~~/` forms; nothing here uses one, so the identity is the honest
        stand-in — and having the member at all is what lets the production font resolver run."""
        return path

    def command_async(self, *args, **_kwargs):
        """Uncorrelated egress, completed inline — again through `command`, so a keybind the tooltip
        registers shows up in the same log as everything else."""
        from concurrent.futures import Future

        from saitenka.mpvio.ipc import IPCRequest

        future: Future[dict] = Future()
        future.set_result(self.command(*args))
        return IPCRequest(0, 0, future)

    def submit_runtime_mpv(self, *, identity, command, on_finished, **_kwargs) -> bool:
        """Correlated egress, completed inline. Delivery goes through `command` so this fake's own
        state simulation sees the write — a fake that skips it reports a stale readback."""
        from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

        reply = self.command(*command)
        on_finished(
            EffectFinished(
                EffectId(0),
                Owner.SUBTITLE,
                identity,
                EffectOutcome.SUCCEEDED,
                result=reply.get("data"),
            )
        )
        return True

    def close(self) -> None:
        pass


def _reader(ipc: _IPC, *, backend: LibassGeometryBackend | None = None) -> SessionController:
    geometry = SubtitleGeometryOptions(native_visible=backend is not None, cache_max=3, lookahead=2)
    return create_session_controller(
        cast("MpvIPC", ipc),
        infrastructure=SessionInfrastructure(
            renderer=None,
            geometry=backend,
        ),
        options=ReaderOptions(subtitle_geometry=geometry, prefetch=False),
    )


@contextmanager
def _managed_readers(
    baseline_ipc: _IPC, native_ipc: _IPC, backend: LibassGeometryBackend
) -> Iterator[tuple[SessionController, SessionController]]:
    baseline: SessionController | None = None
    native: SessionController | None = None
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


def _present(reader: SessionController, text: str, *, native: bool) -> bool:
    """Whether the cue is presented with geometry behind it.

    Observed, not taken from `apply`'s return: publication moved into the geometry lane's terminal,
    so the snapshot this call would install is usually already installed and `apply` reports False
    for it. Reading the return would count zero on a pipeline that is working perfectly.
    """
    reader.set_subtitle(text)
    if native:
        assert reader.subtitle_presentation.native is not None
        reader.subtitle_presentation.native.apply(reader._geometry_observation())
        return (
            bool(reader.subtitle_presentation.cue.current.boxes)
            and reader.subtitle_presentation.native.fallback_reason is None
        )
    return bool(reader.subtitle_presentation.cue.current.boxes)


def _open_tooltip(reader: SessionController, ipc: _IPC, *, native: bool) -> tuple[bool, bool, bool]:
    if not reader.subtitle_presentation.cue.current.boxes:
        return False, False, False
    box = reader.subtitle_presentation.cue.current.boxes[0]
    ox, oy = reader.subtitle_presentation.cue.current.origin
    hit = reader.tooltip_controller.hit(ox + box.x + box.w / 2, oy + box.y + box.h / 2) == box.index
    before = len(ipc.commands)
    reader.tooltip_controller.select(box.index)
    reader.subtitle_presentation.draw()
    commands = ipc.commands[before:]
    focus = (
        any(command[:3] == ("osd-overlay", 1_001, "ass-events") for command in commands)
        if native
        else True
    )
    reader.tooltip_controller.show_tooltip(box.index)
    opened = reader.tooltip_controller.surface_state().view.state is not None
    return hit, focus, opened


def _scroll_and_close_tooltip(reader: SessionController) -> bool:
    opened = reader.tooltip_controller.surface_state().view.state is not None
    reader.tooltip_controller.scroll_tip(1)
    scrolled = (
        opened
        and reader._scrolled_this_tick
        and reader.tooltip_controller.surface_state().view.state is not None
        and reader.tooltip_controller.surface_state().view.scroll > 0
    )
    reader.tooltip_controller.teardown()
    reader.tooltip_controller.select(-1)
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
        baseline.profile_session.profile.replace_dictionary_set(
            cast("DictionarySet", _TallDictionary())
        )
        native.profile_session.profile.replace_dictionary_set(
            cast("DictionarySet", _TallDictionary())
        )
        assert native.subtitle_presentation.native is not None
        native.subtitle_presentation.native.set_source(source_path, live=True)
        # A track load is where mpv's font set is read, and a frame measured against an unresolved
        # environment is refused rather than laid out in substitute faces. Through the production
        # resolver, not a hand-built environment: a harness that skipped it would benchmark the
        # refusal path and report the interaction as free.
        resolve_track_fonts(native.ipc, native.ipc.query, native.subtitle_presentation.native)
        index = CueIndex([Cue(start / 1_000, end / 1_000, text) for start, end, text in cues])
        native.track_commands.navigation.current.sub_index = index
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
            # Outside every timed region on purpose: the presentation numbers above are the perf
            # claim and must keep their cold cue, but whether a tooltip opens is a functional
            # invariant, and leaving it to race the geometry lane makes it a property of the
            # runner's speed. The wait costs the measurement nothing and the oracle everything.
            assert native.subtitle_presentation.native is not None
            assert native.subtitle_presentation.native.worker.wait_idle(timeout=30)
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
        assert native.subtitle_presentation.native.worker.wait_idle(timeout=30)
        stats = native.subtitle_presentation.native.worker.stats
        last_error = native.subtitle_presentation.pipeline.last_error
        rss_retained = process.memory_info().rss
        native.profile_session.profile.use_tokenizer(native.profile_session.profile.tokenizer)
        profile_switch_cache_entries = sum(
            (
                native.subtitle_presentation.native.worker.stats.result_cache_entries,
                native.subtitle_presentation.native.worker.stats.prefetch_cache_entries,
            )
        )
        rss_after_profile_switch = process.memory_info().rss
        native.subtitle_presentation.native.set_source(None, live=True)
        source_clear_current = native.subtitle_presentation.pipeline.current is not None
        source_clear_hit_count = len(native.subtitle_presentation.cue.current.boxes)
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


@contextmanager
def _native_log_to(path: Path):
    """Point fd 2 at `path` for the duration, so libass's chatter lands in a file.

    libass logs its version, shaper, font provider and every `fontselect` at default verbosity —
    once per renderer, and this builds one per cue. libasslite installs no message callback, so
    there is no level to turn down from Python and the descriptor is the only knob. It has to be
    fd 2, not `sys.stderr`: the writer is C, and rebinding the Python object would not move it.
    """
    sys.stderr.flush()
    with path.open("wb") as sink:  # opened before the dup, so a failed open cannot leak it
        saved = os.dup(2)
        try:
            os.dup2(sink.fileno(), 2)
            yield
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            os.close(saved)


def _summarize(report: dict, manifest: dict, output: Path) -> str:
    """A console view sized for a human, with the full record left in the artifact.

    Every clause that can fail the run is named here, because the numbers alone do not say which
    budget bit — and re-deriving that from the artifact by hand is the cost this exists to remove.
    """
    verdict = "PASS" if report["integration_budgets_passed"] else "FAIL"
    performance = report["performance"]
    lines = [
        (
            f"{verdict}"
            f"  trials {report['completed_trials']}/{report['trial_count']}"
            f"  valid {performance['valid_trials']}/{performance['required_valid_trials']}"
            f"  clean {performance['clean_trials']}"
            f"  functional {'ok' if report['all_functional_invariants_passed'] else 'FAILED'}"
        ),
    ]
    over = [name for name, ok in performance["clauses"].items() if not ok]
    if over:
        # The run-level clause, not a trial's: a median over budget is the regression signal, while
        # a single trial over it is the noise this rule exists to absorb.
        lines.append(f"  over budget across trials: {', '.join(over)}")
    if performance["valid_trials"] < performance["required_valid_trials"]:
        lines.append("  too few trials held their cadence to judge performance")
    elif not performance["clean_trials"]:
        lines.append("  no trial passed every budget")
    for trial in report["trials"]:
        index = trial["index"]
        if trial.get("status") != "completed":
            lines.append(f"  trial {index}: {trial['status']} — {trial.get('error', '')}")
            continue
        done = trial["report"]
        lines.append(
            f"  trial {index}: cpu p99 {done['interaction_cpu_p99_ms']:.2f}ms"
            f"  wall p99 {done['interaction_p99_ms']:.2f}ms"
            f"  ready {done['ready_before_presentation_ratio']:.3f}"
            f"  rss +{done['retained_rss_growth_mib']:.1f}MiB"
        )
        for kind, checks in (
            ("performance", _performance_checks(done, manifest)),
            ("functional", _functional_checks(done, manifest)),
        ):
            failed = [name for name, ok in checks.items() if not ok]
            if failed:
                lines.append(f"    {kind} failures: {', '.join(failed)}")
    lines.append(f"full report: {output}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library-path", type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    native_log = args.output.with_name(f"{args.output.stem}-libass.log")
    with _native_log_to(native_log):
        report = execute_trials(manifest, args.library_path, args.output)
    passed = report["integration_budgets_passed"]
    print(_summarize(report, manifest, args.output))
    captured = native_log.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"native log: {len(captured)} lines → {native_log}")
    if not passed:
        # A failure has to stay diagnosable from the console alone, so the tail comes back out.
        for line in captured[-40:]:
            print(f"  | {line}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
