"""Matched synthetic work: native-mask fidelity and worker phase cost, without mpv."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
from compare_cached_characters import geometry_request_for
from saitenka_subtitles.geometry import FontProvider, FontSetup, GeometryPaletteEntry
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from synthetic_characters import ROOT, SPEC, documents

from saitenka.app.subtitle_fonts import FontEnvironment


class Evidence:
    def __init__(self):
        self.spans = {}

    @contextmanager
    def span(self, name):
        values = {}
        self.spans[name] = values

        class Span:
            def set(self, key, value):
                values[key] = value

        yield Span()

    def record(self, _name, _milliseconds):
        pass


def canvas(snapshot, size):
    image = np.zeros((size[1], size[0]), dtype=np.uint8)
    for token in snapshot.tokens:
        box = token.bounds
        target = image[box.y : box.y + box.height, box.x : box.x + box.width]
        pixels = np.frombuffer(token.coverage, dtype=np.uint8).reshape(box.height, box.width)
        np.maximum(target, pixels, out=target)
    return image


def sample(backend, sink, inputs, reference):
    sink.spans.clear()
    started, cpu = time.perf_counter_ns(), time.thread_time_ns()
    try:
        snapshot = backend.render(inputs)
    except ValueError as error:
        return {
            "outcome": "failed",
            "reason": str(error),
            "phases": sink.spans.copy(),
            "cpu_ms": (time.thread_time_ns() - cpu) / 1_000_000,
            "elapsed_ms": (time.perf_counter_ns() - started) / 1_000_000,
        }
    elapsed_cpu = (time.thread_time_ns() - cpu) / 1_000_000
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    actual = canvas(snapshot, inputs.frame_size)
    return {
        "outcome": "rendered",
        "cpu_ms": elapsed_cpu,
        "elapsed_ms": elapsed,
        "found_tokens": len(snapshot.tokens),
        "mask_bytes": snapshot.coverage_bytes,
        "mask_source": snapshot.mask_source,
        "libass_version": snapshot.libass_version,
        "native_differing_pixels": int(np.count_nonzero(actual != reference)),
        "phases": sink.spans.copy(),
    }


def benchmark(*, cycles=3, track_copies=25, sizes=((1280, 720), (3440, 1440))):
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    font = (ROOT / spec["font"]["path"]).read_bytes()
    if hashlib.sha256(font).hexdigest() != spec["font"]["sha256"]:
        raise ValueError("synthetic font digest mismatch")
    fonts = FontEnvironment(
        FontSetup(default_family=spec["font"]["family"], font_provider=FontProvider.NONE),
        attachments=(("corpus.ttf", font),),
    )
    full_track = documents({**spec, "cases": spec["cases"] * track_copies})["ass"]
    evidence = []
    for size in sizes:
        sink = Evidence()
        corrected, legacy = LibassGeometryBackend(telemetry=sink), LibassGeometryBackend()
        oracle = LibassGeometryBackend()
        try:
            for cycle in range(cycles):
                for index, case in enumerate(spec["cases"]):
                    inputs, _tokens = geometry_request_for(
                        full_track, 1500 + index * 2000, size, fonts
                    )
                    whole = replace(
                        inputs,
                        ass=inputs.native_ass,
                        native_ass=b"",
                        reserved_rgb=(),
                        palette=(GeometryPaletteEntry(inputs.palette[0].event_id, 0, 0xFFFFFF),),
                    )
                    reference = canvas(oracle.render(whole), size)
                    result = sample(corrected, sink, inputs, reference)
                    old = sample(legacy, Evidence(), replace(inputs, native_ass=b""), reference)
                    evidence.append(
                        {
                            "case": case["id"],
                            "size": size,
                            "cycle": cycle,
                            "eligible_tokens": len(inputs.palette),
                            "corrected": result,
                            "identity_hint_baseline": old,
                        }
                    )
        finally:
            corrected.close()
            legacy.close()
            oracle.close()
    return {
        "schema": 1,
        "scope": "same-renderer synthetic masks; not mpv qualification",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "font_sha256": spec["font"]["sha256"],
        },
        "source_bytes": len(full_track.encode()),
        "source_events": len(spec["cases"]) * track_copies,
        "source_sha256": hashlib.sha256(full_track.encode()).hexdigest(),
        "cycles": cycles,
        "rows": evidence,
        "qualification": "no timing threshold; inspect phase costs alongside fidelity and census",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--track-copies", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True, help="new local JSON evidence file")
    args = parser.parse_args()
    if not 1 <= args.cycles <= 100 or not 1 <= args.track_copies <= 100:
        parser.error("cycles and track copies must be between 1 and 100")
    result = benchmark(cycles=args.cycles, track_copies=args.track_copies)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"rows": len(result["rows"]), "output": str(args.output)}))
    return int(any(row["corrected"].get("native_differing_pixels") != 0 for row in result["rows"]))


if __name__ == "__main__":
    raise SystemExit(main())
