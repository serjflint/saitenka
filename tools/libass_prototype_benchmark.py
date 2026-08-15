"""Measure the research-only native-visible/libass shadow prototype."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import psutil
from libass_token_matrix import (
    ASS_HEADER,
    TokenKey,
    character_mask_signature,
    character_support,
    extract_token_geometry,
)

from saitenka.app.scoring import Scorer
from saitenka.app.tokenizer import UnidicTokenizer
from saitenka.app.wordlists import KnownWords
from saitenka.subtitles.ass import (
    allocate_token_colors,
    decode_ass_event,
    parse_ass_event_line,
    parse_ass_styles,
    rewrite_ass_event,
    serialize_ass_event_line,
)
from saitenka.subtitles.document import AnnotatedSubtitleEvent, SubtitleTrackId, TokenAnnotation

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from libass_token_matrix import RenderResult

    from saitenka.app.scoring import TokenStyle
    from saitenka.subtitles.document import DecodedSubtitleEvent


class Renderer(Protocol):
    def render(
        self, timestamp_ms: int, frame_size: tuple[int, int], storage_size: tuple[int, int]
    ) -> Any: ...

    def close(self) -> None: ...

    def library_version(self) -> int: ...

    def library_path(self) -> str: ...


class RendererFactory(Protocol):
    def __call__(self, ass: bytes, *, library_path: str | None = None) -> Renderer: ...


class LibassModule(Protocol):
    AssRenderer: RendererFactory


INTERACTIVE = "primary"
NONINTERACTIVE = {"source-marked-auxiliary", "ambiguous-disabled"}
FRAME_SIZE = (1280, 720)
RESERVED_RGB = (0xFFFFFF,)


@dataclass(frozen=True, slots=True)
class EventInput:
    interaction: str
    line: str


@dataclass(frozen=True, slots=True)
class ModelEvent:
    interaction: str
    decoded: DecodedSubtitleEvent


@dataclass(frozen=True, slots=True)
class PreparedPrototype:
    visible_ass: bytes
    styled_ass: bytes
    shadow_ass: bytes
    palette: tuple[tuple[int, TokenKey], ...]
    reserved_rgb: tuple[int, ...]
    timestamp_ms: int
    interactive_tokens: int
    excluded_events: int


@dataclass(frozen=True, slots=True)
class PipelineParts:
    model: tuple[ModelEvent, ...]
    annotated: tuple[AnnotatedSubtitleEvent, ...]
    styles: tuple[Mapping[int, int], ...]


def _canonical_contract(manifest: dict) -> bytes:
    unlocked = dict(manifest)
    denominator = dict(cast("dict", unlocked["denominator"]))
    denominator.pop("contract_sha256", None)
    unlocked["denominator"] = denominator
    return json.dumps(unlocked, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def contract_hash(manifest: dict) -> str:
    return hashlib.sha256(_canonical_contract(manifest)).hexdigest()


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("unsupported prototype benchmark manifest")
    denominator = manifest.get("denominator")
    budgets = manifest.get("budgets")
    cases = manifest.get("cases")
    if (
        not isinstance(denominator, dict)
        or not isinstance(budgets, dict)
        or not isinstance(cases, list)
    ):
        raise TypeError("manifest denominator, budgets, and cases must be structured values")
    if denominator.get("case_count") != len(cases):
        raise ValueError("prototype case count changed")
    if denominator.get("contract_sha256") != contract_hash(manifest):
        raise ValueError("prototype execution contract changed")
    expected_budgets = {
        "warm_samples": 1000,
        "cold_process_starts": 30,
        "cold_geometry_p95_us": 100000,
        "static_render_p99_us": 50000,
        "cadence_fps": 24,
        "cadence_duration_ms": 2000,
        "cadence_min_unique_frames": 24,
        "cadence_max_skipped_frames": 0,
    }
    if budgets != expected_budgets:
        raise ValueError("prototype budgets changed")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or len(ids) != len(set(ids)):
        raise ValueError("prototype cases need unique identities")
    roles = {
        event.get("interaction")
        for case in cases
        for event in cast("list[dict]", case.get("events", []))
    }
    if roles - ({INTERACTIVE} | NONINTERACTIVE):
        raise ValueError("prototype contains an unknown interaction role")
    if not roles >= NONINTERACTIVE:
        raise ValueError("prototype must cover auxiliary and ambiguous fail-closed roles")
    return manifest


def _ass_document(lines: Sequence[str]) -> bytes:
    return (ASS_HEADER + "\n".join(lines) + "\n").encode()


def _event_inputs(case: dict) -> tuple[EventInput, ...]:
    events = case.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("prototype case must contain events")
    return tuple(EventInput(str(item["interaction"]), str(item["line"])) for item in events)


def build_model(events: Sequence[EventInput]) -> tuple[ModelEvent, ...]:
    track_id = SubtitleTrackId("prototype")
    return tuple(
        ModelEvent(
            event.interaction,
            decode_ass_event(parse_ass_event_line(event.line, track_id, source_order)),
        )
        for source_order, event in enumerate(events)
    )


def _rgba_to_bgr(style: TokenStyle) -> int:
    red, green, blue, _alpha = style.color
    value = blue << 16 | green << 8 | red
    return value or 1


def _bgr_to_rgb(value: int) -> int:
    return (value & 0xFF) << 16 | (value & 0xFF00) | (value >> 16 & 0xFF)


def tokenize_and_score(
    model: Sequence[ModelEvent], tokenizer: UnidicTokenizer, scorer: Scorer
) -> tuple[tuple[AnnotatedSubtitleEvent, ...], tuple[Mapping[int, int], ...]]:
    annotated: list[AnnotatedSubtitleEvent] = []
    colors: list[Mapping[int, int]] = []
    for event in model:
        if event.interaction != INTERACTIVE:
            annotated.append(AnnotatedSubtitleEvent(event.decoded, ()))
            colors.append({})
            continue
        tokens = tokenizer.tokenize(event.decoded.text, strip_furigana=False)
        scores = scorer.score_line(tokens)
        annotations = tuple(
            TokenAnnotation(index, token.start, token.end)
            for index, token in enumerate(tokens)
            if not tokenizer.is_skippable(token)
        )
        annotated.append(AnnotatedSubtitleEvent(event.decoded, annotations))
        colors.append(
            {token.token_index: _rgba_to_bgr(scores[token.token_index]) for token in annotations}
        )
    return tuple(annotated), tuple(colors)


def serialize_prototype(parts: PipelineParts, timestamp_ms: int) -> PreparedPrototype:
    catalog = parse_ass_styles(ASS_HEADER.encode())
    allocations = allocate_token_colors(parts.annotated, reserved_colors=RESERVED_RGB)
    id_by_event: dict[object, dict[int, int]] = {}
    palette: list[tuple[int, TokenKey]] = []
    for color in allocations:
        per_event = id_by_event.setdefault(color.event_id, {})
        per_event[color.token_index] = color.bgr
        palette.append(
            (_bgr_to_rgb(color.bgr), TokenKey(str(color.event_id.source_order), color.token_index))
        )

    visible_lines: list[str] = []
    styled_lines: list[str] = []
    shadow_lines: list[str] = []
    excluded = 0
    for model_event, annotated, visible_colors in zip(
        parts.model, parts.annotated, parts.styles, strict=True
    ):
        source_line = serialize_ass_event_line(model_event.decoded.source)
        visible_lines.append(source_line)
        if model_event.interaction != INTERACTIVE:
            excluded += 1
            styled_lines.append(source_line)
            continue
        styled = rewrite_ass_event(annotated, visible_colors, catalog).event
        shadow = rewrite_ass_event(
            annotated,
            id_by_event[annotated.decoded.source.identity],
            catalog,
            require_unique=True,
        ).event
        styled_lines.append(serialize_ass_event_line(styled))
        shadow_lines.append(serialize_ass_event_line(shadow))
    return PreparedPrototype(
        _ass_document(visible_lines),
        _ass_document(styled_lines),
        _ass_document(shadow_lines),
        tuple(palette),
        RESERVED_RGB,
        timestamp_ms,
        len(palette),
        excluded,
    )


def prepare_case(
    case: dict, tokenizer: UnidicTokenizer | None = None, scorer: Scorer | None = None
) -> PreparedPrototype:
    tokenizer = tokenizer or UnidicTokenizer()
    scorer = scorer or Scorer(
        known=KnownWords.from_set([]), enable_freq=False, enable_jlpt=False, enable_known=False
    )
    model = build_model(_event_inputs(case))
    annotated, styles = tokenize_and_score(model, tokenizer, scorer)
    return serialize_prototype(PipelineParts(model, annotated, styles), int(case["timestamp_ms"]))


def percentile(samples: Sequence[int], percentile_value: int) -> int:
    if not samples or not 0 < percentile_value <= 100:
        raise ValueError("percentile needs samples and a value in (0, 100]")
    ordered = sorted(samples)
    rank = (len(ordered) * percentile_value + 99) // 100
    return ordered[rank - 1]


def evaluate_budgets(report: dict, budgets: dict) -> tuple[str, ...]:
    failures = []
    warm = report["warm_samples"]
    cold = report["cold_geometry_samples"]
    if len(warm) != budgets["warm_samples"]:
        failures.append("warm sample count")
    if len(cold) != budgets["cold_process_starts"]:
        failures.append("cold process sample count")
    render_samples = [int(sample["render_us"]) for sample in warm]
    if render_samples and percentile(render_samples, 99) > budgets["static_render_p99_us"]:
        failures.append("static render p99")
    cold_latencies = [int(sample["latency_us"]) for sample in cold]
    if cold_latencies and percentile(cold_latencies, 95) > budgets["cold_geometry_p95_us"]:
        failures.append("cold geometry p95")
    cadence = report["animated_cadence"]
    expected_frames = budgets["cadence_fps"] * budgets["cadence_duration_ms"] // 1000
    if cadence["frame_count"] != expected_frames:
        failures.append("animated frame count")
    if cadence["active_frame_count"] != cadence["frame_count"]:
        failures.append("animated active frames")
    if cadence["unique_geometry_frames"] < budgets["cadence_min_unique_frames"]:
        failures.append("animated geometry changes")
    if cadence["skipped_budget_count"] > budgets["cadence_max_skipped_frames"]:
        failures.append("animated cadence skips")
    return tuple(failures)


def _timed(clock: Callable[[], int], operation: Callable[[], object]) -> tuple[object, int]:
    started = clock()
    value = operation()
    return value, (clock() - started) // 1000


def _render_activated(
    renderer: Renderer, timestamp_ms: int, clock: Callable[[], int]
) -> tuple[object, int]:
    if timestamp_ms <= 0:
        raise ValueError("prototype cue timestamp must follow the inactive frame")
    renderer.render(0, FRAME_SIZE, FRAME_SIZE)
    return _timed(clock, lambda: renderer.render(timestamp_ms, FRAME_SIZE, FRAME_SIZE))


def _one_pipeline_sample(
    case: dict,
    tokenizer: UnidicTokenizer,
    scorer: Scorer,
    renderer: Renderer,
    clock: Callable[[], int],
) -> tuple[dict, PreparedPrototype]:
    events = _event_inputs(case)
    model_value, model_us = _timed(clock, lambda: build_model(events))
    model = cast("tuple[ModelEvent, ...]", model_value)
    token_value, token_us = _timed(clock, lambda: tokenize_and_score(model, tokenizer, scorer))
    annotated, styles = cast(
        "tuple[tuple[AnnotatedSubtitleEvent, ...], tuple[Mapping[int, int], ...]]", token_value
    )
    prototype_value, serialize_us = _timed(
        clock,
        lambda: serialize_prototype(
            PipelineParts(model, annotated, styles), int(case["timestamp_ms"])
        ),
    )
    prototype = cast("PreparedPrototype", prototype_value)
    result_value, render_us = _render_activated(renderer, prototype.timestamp_ms, clock)
    result = cast("RenderResult", result_value)
    geometry_value, extract_us = _timed(
        clock,
        lambda: extract_token_geometry(
            result, prototype.palette, reserved_colors=prototype.reserved_rgb
        ),
    )
    geometry = cast("tuple", geometry_value)
    if len(geometry) != prototype.interactive_tokens:
        raise RuntimeError("shadow geometry lost an interactive token")
    return (
        {
            "model_us": model_us,
            "tokenize_score_us": token_us,
            "serialize_us": serialize_us,
            "render_us": render_us,
            "extract_us": extract_us,
        },
        prototype,
    )


def _cold_worker(manifest_path: Path, library_path: str | None) -> dict[str, int | str]:
    manifest = load_manifest(manifest_path)
    case = cast("list[dict]", manifest["cases"])[0]
    libass = cast("LibassModule", importlib.import_module("libasslite"))
    tokenizer = UnidicTokenizer()
    scorer = Scorer(
        known=KnownWords.from_set([]), enable_freq=False, enable_jlpt=False, enable_known=False
    )
    started = time.perf_counter_ns()
    prototype = prepare_case(case, tokenizer, scorer)
    renderer = libass.AssRenderer(prototype.shadow_ass, library_path=library_path)
    try:
        result = renderer.render(prototype.timestamp_ms, FRAME_SIZE, FRAME_SIZE)
        geometry = extract_token_geometry(
            result, prototype.palette, reserved_colors=prototype.reserved_rgb
        )
        if len(geometry) != prototype.interactive_tokens:
            raise RuntimeError("cold render lost an interactive token")
        elapsed_us = (time.perf_counter_ns() - started) // 1000
        version = renderer.library_version()
        loaded_path = renderer.library_path()
    finally:
        renderer.close()
    return {
        "latency_us": elapsed_us,
        "library_version": f"0x{version:08x}",
        "library_path": loaded_path,
    }


def _cold_samples(
    manifest_path: Path,
    count: int,
    library_path: str,
    library_version: str,
):
    command = [sys.executable, __file__, "--manifest", str(manifest_path), "--cold-worker"]
    env = dict(os.environ)
    env["LIBASSLITE_LIBRARY"] = library_path
    for _ in range(count):
        completed = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
        sample = json.loads(completed.stdout)
        if sample["library_path"] != library_path or sample["library_version"] != library_version:
            raise RuntimeError("cold worker loaded a different libass binary")
        yield sample


def _cadence_report(
    renderer_type: RendererFactory,
    animated: dict,
    fps: int,
    duration_ms: int,
    library_path: str | None,
) -> dict:
    document = _ass_document([str(animated["line"])])
    renderer = renderer_type(document, library_path=library_path)
    frame_budget_us = 1_000_000 / fps
    samples = []
    signatures = []
    active_frames = 0
    changed_frames = 0
    try:
        frame_count = duration_ms * fps // 1000
        start_ms = int(animated["timestamp_ms"])
        for frame in range(frame_count):
            timestamp_ms = start_ms + frame * 1000 // fps
            started = time.perf_counter_ns()
            result = renderer.render(timestamp_ms, FRAME_SIZE, FRAME_SIZE)
            samples.append((time.perf_counter_ns() - started) // 1000)
            signatures.append(character_mask_signature(result))
            active_frames += bool(character_support(result))
            changed_frames += bool(result.detect_change)
    finally:
        renderer.close()
    return {
        "fps": fps,
        "frame_budget_us": frame_budget_us,
        "frame_count": len(samples),
        "samples_us": samples,
        "skipped_budget_count": sum(sample > frame_budget_us for sample in samples),
        "active_frame_count": active_frames,
        "unique_geometry_frames": len(set(signatures)),
        "detect_change_nonzero_count": changed_frames,
        "interactive_during_playback": False,
        "pause_requires_stable_timestamp_recompute": True,
    }


def run_benchmark(
    manifest_path: Path, progress: Callable[[dict[str, Any]], None] | None = None
) -> dict:
    manifest = load_manifest(manifest_path)
    budgets = cast("dict", manifest["budgets"])
    cases = cast("list[dict]", manifest["cases"])
    libass = cast("LibassModule", importlib.import_module("libasslite"))
    library_path = os.environ.get("LIBASSLITE_LIBRARY")
    tokenizer = UnidicTokenizer()
    scorer = Scorer(
        known=KnownWords.from_set([]), enable_freq=False, enable_jlpt=False, enable_known=False
    )
    prepared = [prepare_case(case, tokenizer, scorer) for case in cases]
    process = psutil.Process()
    rss_before = process.memory_info().rss
    renderers = [
        libass.AssRenderer(item.shadow_ass, library_path=library_path)
        if item.interactive_tokens
        else None
        for item in prepared
    ]
    probe = next(renderer for renderer in renderers if renderer is not None)
    warm_samples = []
    try:
        for index in range(budgets["warm_samples"]):
            case_index = index % len(cases)
            if not prepared[case_index].interactive_tokens:
                case_index = 0
            renderer = renderers[case_index]
            assert renderer is not None
            sample, _ = _one_pipeline_sample(
                cases[case_index], tokenizer, scorer, renderer, time.perf_counter_ns
            )
            warm_samples.append(sample)
        rss_retained = process.memory_info().rss
        version = probe.library_version()
        loaded_path = probe.library_path()
    finally:
        for renderer in renderers:
            if renderer is not None:
                renderer.close()
    gc.collect()
    rss_after_static_close = process.memory_info().rss
    library_version = f"0x{version:08x}"
    report: dict[str, Any] = {
        "schema": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "libass_version": library_version,
        "library_path": loaded_path,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "contract_sha256": contract_hash(manifest),
        "cache_state": {
            "cold_definition": "timed from available source through fresh tokenizer use, renderer/track construction, render, and extraction in a fresh process; Python startup/import and OS font caches excluded",
            "warm_definition": "retained per-case renderers and process; each timed cue follows an inactive frame",
            "rss_before_bytes": rss_before,
            "rss_retained_bytes": rss_retained,
            "rss_after_static_close_gc_bytes": rss_after_static_close,
        },
        "case_contracts": [
            {
                "id": case["id"],
                "visible_sha256": hashlib.sha256(item.visible_ass).hexdigest(),
                "shadow_sha256": hashlib.sha256(item.shadow_ass).hexdigest(),
                "interactive_tokens": item.interactive_tokens,
                "excluded_events": item.excluded_events,
            }
            for case, item in zip(cases, prepared, strict=True)
        ],
        "warm_samples": warm_samples,
        "cold_geometry_samples": [],
        "animated_cadence": None,
        "integration_budgets_frozen_not_measured": {
            "interaction_thread_p99_us": 8000,
            "interaction_thread_regression_p99_us": 2000,
            "original_track_first_paint_delay_us": 0,
            "static_lookahead_ready_percent": 99,
            "retained_rss_growth_mib": 256,
            "live_switch_blank_or_duplicate_frames": 0,
        },
        "status": "running",
    }
    if progress is not None:
        progress(report)
    cold_samples = cast("list[dict[str, int | str]]", report["cold_geometry_samples"])
    for sample in _cold_samples(
        manifest_path,
        budgets["cold_process_starts"],
        loaded_path,
        library_version,
    ):
        cold_samples.append(sample)
        if progress is not None:
            progress(report)
    try:
        report["animated_cadence"] = _cadence_report(
            libass.AssRenderer,
            cast("dict", manifest["animated"]),
            budgets["cadence_fps"],
            budgets["cadence_duration_ms"],
            loaded_path,
        )
    finally:
        gc.collect()
        report["cache_state"]["rss_after_all_close_gc_bytes"] = process.memory_info().rss
        if progress is not None:
            progress(report)
    failures = evaluate_budgets(report, budgets)
    report["budget_failures"] = list(failures)
    report["prototype_budgets_passed"] = not failures
    report["status"] = "passed" if not failures else "failed"
    if progress is not None:
        progress(report)
    return report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _run_and_persist(
    path: Path,
    operation: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]],
) -> dict[str, Any]:
    latest: dict[str, Any] = {}

    def persist(report: dict[str, Any]) -> None:
        latest.clear()
        latest.update(report)
        _write_report(path, report)

    try:
        return operation(persist)
    except BaseException as error:
        latest["status"] = "failed"
        latest["error"] = f"{type(error).__name__}: {error}"
        _write_report(path, latest)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cold-worker", action="store_true")
    args = parser.parse_args()
    if args.cold_worker:
        print(json.dumps(_cold_worker(args.manifest, os.environ.get("LIBASSLITE_LIBRARY"))))
        return
    if args.output is None:
        parser.error("--output is required")
    report = _run_and_persist(args.output, lambda progress: run_benchmark(args.manifest, progress))
    if not report["prototype_budgets_passed"]:
        raise SystemExit("prototype performance budget failed")


if __name__ == "__main__":
    main()
