"""Installed, text-free diagnostics for native subtitle geometry and pixel ownership."""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

from saitenka.app import font_resolution
from saitenka.app.report_reader import read_member, trace_evidence

if TYPE_CHECKING:
    from pathlib import Path

_SPAN_NAMES = frozenset(
    {
        "subtitle_geometry_decision",
        "subtitle_geometry_source",
        "subtitle_geometry_acquisition",
        "subtitle_geometry_clock",
        "subtitle_geometry_cache",
        "subtitle_geometry_prepare",
        "subtitle_geometry_render",
        "subtitle_geometry_libass",
        "subtitle_geometry_fallback",
        "subtitle_pixel_ownership",
    }
)

_FIELDS = frozenset(
    {
        "configured_source",
        "selected_source",
        "scan_source",
        "paint_source",
        "paint_allowed",
        "paint_reason",
        "geometry_source",
        "cue_revision",
        "attempt",
        "request_generation",
        "request_cue_revision",
        "capability",
        "outcome",
        "reason",
        "error_code",
        "ass_full_capability",
        "active_events",
        "observed_rows",
        "matched_events",
        "eligible_tokens",
        "requested_tokens",
        "found_tokens",
        "skipped_whitespace",
        "skipped_tokenizer",
        "skipped_unpaintable",
        "frame_width",
        "frame_height",
        "storage_width",
        "storage_height",
        "pixel_aspect",
        "margins",
        "generation",
        "source_epoch",
        "source_class",
        "owner_transition",
        "provider",
        "libass_version",
        "layer_count",
        "prepare_ms",
        "render_ms",
        "extract_ms",
        "video_time_ms",
        "sub_delay_ms",
        "subtitle_time_ms",
        "timestamp_ms",
        "session",
        "cpu_ms",
        "cache_hits",
        "prefetch_dropped",
        "prefetch_cache_entries",
        "coverage_trimmed",
        "event",
        "mode",
        "owner_before",
        "owner_after",
        "visibility",
        "connection_epoch",
        "ownership_epoch",
        "selection_present",
        "retry_attempts",
        "retry_exhausted",
        "accepted",
        "effect_id",
    }
)


def _read_member(source: Path, name: str) -> str | None:
    return read_member(source, name)


def load_trace(source: Path) -> list[dict]:
    """Load Chrome trace events from a report zip/directory or bare trace JSON."""
    evidence = trace_evidence(source)
    if evidence["status"] == "invalid":
        raise ValueError(evidence["reason"])
    return evidence["events"]


def geometry_spans(events: list[dict]) -> list[dict]:
    spans = [
        event for event in events if event.get("ph") == "X" and event.get("name") in _SPAN_NAMES
    ]
    return sorted(spans, key=lambda span: span.get("ts", 0.0))


def geometry_records(events: list[dict]) -> list[dict]:
    return [
        {
            "name": span["name"],
            "ts": span.get("ts"),
            "args": {key: value for key, value in span.get("args", {}).items() if key in _FIELDS},
        }
        for span in geometry_spans(events)
    ]


def _ownership_diagnosis(args: dict) -> str:
    accepted = f" accepted={args['accepted']}" if "accepted" in args else ""
    return (
        f"{args.get('event', '?')}: {args.get('owner_before', '?')}"
        f" -> {args.get('owner_after', '?')} visibility={args.get('visibility', '?')}"
        f" retries={args.get('retry_attempts', 0)}{accepted}"
    )


def _clock_diagnosis(args: dict) -> str:
    if args.get("outcome") != "ready":
        return f"{args.get('outcome', '?')}: subtitle clock unavailable"
    return (
        f"video={args.get('video_time_ms', '?')}ms "
        f"delay={args.get('sub_delay_ms', '?')}ms "
        f"subtitle={args.get('subtitle_time_ms', '?')}ms"
    )


def _cache_diagnosis(args: dict) -> str:
    return (
        f"{args.get('outcome', '?')}: hits={args.get('cache_hits', '?')} "
        f"ready={args.get('prefetch_cache_entries', '?')} "
        f"dropped={args.get('prefetch_dropped', '?')}"
    )


def _decision_diagnosis(args: dict) -> str:
    skipped = sum(
        int(args.get(key, 0))
        for key in ("skipped_whitespace", "skipped_tokenizer", "skipped_unpaintable")
    )
    extra = f" error={args['error_code']}" if args.get("error_code") else ""
    transition = f" {args['owner_transition']}" if args.get("owner_transition") else ""
    return (
        f"{args.get('outcome', 'legacy')}: {args.get('reason', 'unknown')} "
        f"(events={args.get('active_events', 0)} eligible={args.get('eligible_tokens', 0)} "
        f"skipped={skipped}){extra}{transition}"
    )


def _prepare_diagnosis(args: dict) -> str:
    extra = f" error={args['error_code']}" if args.get("error_code") else ""
    return (
        f"{args.get('outcome', '?')}: observed={args.get('observed_rows', '?')} "
        f"matched={args.get('matched_events', '?')} eligible={args.get('eligible_tokens', '?')}"
        f"{extra}"
    )


def _libass_diagnosis(args: dict) -> str:
    return (
        f"provider={args.get('provider', '?')} libass={args.get('libass_version', '?')} "
        f"at={args.get('timestamp_ms', '?')}ms layers={args.get('layer_count', '?')} "
        f"tokens={args.get('found_tokens', '?')} render={args.get('render_ms', '?')}ms "
        f"extract={args.get('extract_ms', '?')}ms"
    )


def _render_diagnosis(args: dict) -> str:
    extra = f" error={args['error_code']}" if args.get("error_code") else ""
    return (
        f"{args.get('outcome', '?')}: events={args.get('active_events', '?')} "
        f"tokens={args.get('found_tokens', 0)}/{args.get('requested_tokens', '?')} "
        f"frame={args.get('frame_width', '?')}x{args.get('frame_height', '?')}{extra}"
    )


def _source_diagnosis(args: dict) -> str:
    return (
        f"configured={args.get('configured_source', '?')} selected={args.get('selected_source', '?')} "
        f"scan={args.get('scan_source', '?')} paint={args.get('paint_source', '?')} "
        f"paint_reason={args.get('paint_reason', '?')} "
        f"eligible={args.get('eligible_tokens', 0)} reason={args.get('reason', '?')} "
        f"cue={args.get('cue_revision', '?')} generation={args.get('generation', '?')}"
    )


def _acquisition_diagnosis(args: dict) -> str:
    return (
        f"source={args.get('geometry_source', '?')} reason={args.get('reason', '?')} "
        f"capability={args.get('capability', '?')} attempt={args.get('attempt', '?')} "
        f"epoch={args.get('source_epoch', '?')} "
        f"requested_cue={args.get('request_cue_revision', '?')} "
        f"requested_generation={args.get('request_generation', '?')} "
        f"current_cue={args.get('cue_revision', '?')} generation={args.get('generation', '?')}"
    )


_DIAGNOSIS = {
    "subtitle_geometry_source": _source_diagnosis,
    "subtitle_geometry_acquisition": _acquisition_diagnosis,
    "subtitle_pixel_ownership": _ownership_diagnosis,
    "subtitle_geometry_clock": _clock_diagnosis,
    "subtitle_geometry_cache": _cache_diagnosis,
    "subtitle_geometry_decision": _decision_diagnosis,
    "subtitle_geometry_fallback": _decision_diagnosis,
    "subtitle_geometry_prepare": _prepare_diagnosis,
    "subtitle_geometry_libass": _libass_diagnosis,
}


def geometry_diagnosis(span: dict) -> str:
    return _DIAGNOSIS.get(span["name"], _render_diagnosis)(span.get("args", {}))


def font_resolution_line(source: Path) -> str:
    """What mpv's subtitle renderer resolved each family to, from the log every bundle carries.

    Reported without our own side: the measuring renderer's choices are not in the bundle, so this
    lists mpv's and leaves the comparison to a session that has both. A substituted face is a
    different advance and therefore a wrong box, so naming what mpv chose is worth the line on its
    own.
    """
    try:
        log = _read_member(source, "mpv.log")
    except (OSError, ValueError, zipfile.BadZipFile):
        # This is one line of a report, not the report: an unreadable bundle is `load_trace`'s to
        # raise on, and failing here would take the whole rendering down over a diagnostic.
        return "  font resolution: report archive unreadable"
    if log is None:
        return "  font resolution: no mpv.log in this report"
    return font_resolution.summary(log, {})


def render_geometry(source: Path, events: list[dict], *, nested: bool = False) -> str:
    spans = geometry_spans(events)
    heading = "##" if nested else "#"
    lines = [f"{heading} native subtitle geometry" + ("" if nested else f" — {source.name}")]
    lines.append(font_resolution_line(source))
    if not spans:
        lines.append("  (no native-geometry spans — enable telemetry before reproducing)")
        return "\n".join(lines) + "\n"
    decisions = [
        span
        for span in spans
        if span["name"] in {"subtitle_geometry_decision", "subtitle_geometry_fallback"}
    ]
    current = decisions[-1].get("args", {}) if decisions else {}
    lines.append(
        f"  current={current.get('outcome', '?')} reason={current.get('reason', '?')} · "
        f"{len(decisions)} decision(s), "
        f"{sum(span['name'] == 'subtitle_geometry_prepare' for span in spans)} prepare, "
        f"{sum(span['name'] == 'subtitle_geometry_render' for span in spans)} request, "
        f"{sum(span['name'] == 'subtitle_geometry_libass' for span in spans)} libass"
    )
    sources = [span for span in spans if span["name"] == "subtitle_geometry_source"]
    if sources:
        lines.append("  source: " + _source_diagnosis(sources[-1].get("args", {})))
    start = spans[0].get("ts", 0.0)
    for span in spans:
        elapsed = (span.get("ts", start) - start) / 1_000_000
        label = span["name"].removeprefix("subtitle_geometry_").removeprefix("subtitle_pixel_")
        lines.append(f"  t+{elapsed:7.1f}s  {label:<8} {geometry_diagnosis(span)}")
    return "\n".join(lines) + "\n"
