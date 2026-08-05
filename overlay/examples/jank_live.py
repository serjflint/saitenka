"""Live-mpv jank harness (#32): drive the overlay against a REAL playing mpv and poll mpv's OWN
frame-drop counters — the only true real-time signal (the headless FakeIPC benches can't see mpv's
compositor). For each interaction (hover / scroll / nested / sweep / dismiss) it samples mpv's
cumulative ``frame-drop-count`` (decoder-late) and ``vo-delayed-frame-count`` (VO couldn't present in
time — the overlay-compositing signal), then reports per-step dropped frames.

Opt-in: needs a real display + mpv (``SAITENKA_LIVE=1``). Run via ``uv run poe jank-live``. Emits a
JSON baseline and, with ``--bench-json``, github-action-benchmark ``customSmallerIsBetter`` (dropped
frames, smaller = better) so it rides the same gh-pages dashboard as the render bench.

    SAITENKA_LIVE=1 uv run --extra full python examples/jank_live.py --json /tmp/jank.json
    SAITENKA_LIVE=1 uv run --extra full python examples/jank_live.py --max-drops 20   # e2e gate
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from itertools import pairwise
from pathlib import Path

# The live setup is shared with the L3 smoke tests (tests/live_harness.py); this diagnostic script
# deliberately reuses it rather than duplicate the real-mpv boot.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

_DROP = "frame-drop-count"  # mpv: frames dropped because the decoder was late
_DELAY = (
    "vo-delayed-frame-count"  # mpv: frames the VO couldn't present in time (overlay-compositing)
)


def reduce_jank_samples(samples: list[dict]) -> dict:
    """Turn a list of cumulative-counter snapshots (each ``{"step", "drop", "delay"}``) into per-step
    dropped/delayed frames. Deltas are clamped at 0 so a counter reset (or a ``None`` from a build
    lacking the property, recorded as 0) can never report negative jank. Pure — the unit-tested seam."""
    steps: list[dict] = []
    for prev, cur in pairwise(samples):
        steps.append(
            {
                "step": cur["step"],
                "dropped": max(0, cur["drop"] - prev["drop"]),
                "delayed": max(0, cur["delay"] - prev["delay"]),
            }
        )
    return {
        "steps": steps,
        "total_dropped": sum(s["dropped"] for s in steps),
        "total_delayed": sum(s["delayed"] for s in steps),
        "max_step_dropped": max((s["dropped"] for s in steps), default=0),
        "max_step_delayed": max((s["delayed"] for s in steps), default=0),
    }


def to_bench_json(result: dict) -> list[dict]:
    """github-action-benchmark customSmallerIsBetter for the jank totals (frames, smaller = better)."""
    return [
        {
            "name": "live jank: total dropped frames",
            "unit": "frames",
            "value": result["total_dropped"],
        },
        {
            "name": "live jank: total delayed frames",
            "unit": "frames",
            "value": result["total_delayed"],
        },
    ]


def _counter(ipc, prop: str) -> int:
    data = ipc.command("get_property", prop).get("data")
    return int(data) if isinstance(data, (int, float)) else 0


def run(*, settle_s: float = 0.4) -> dict:
    """Drive the scripted workload against a live, PLAYING mpv and return the reduced jank report."""
    from live_harness import live_reader

    with live_reader(paused=False) as (_tmp, reader, ipc):
        samples: list[dict] = []

        def sample(step: str) -> None:
            # let playback advance so any overlay-induced VO delay accrues before we read the counters
            deadline = time.perf_counter() + settle_s
            while time.perf_counter() < deadline:
                reader.poll_once()
                time.sleep(0.02)
            samples.append(
                {"step": step, "drop": _counter(ipc, _DROP), "delay": _counter(ipc, _DELAY)}
            )

        i = next(k for k, t in enumerate(reader.tokens) if t.is_content)
        box = next(b for b in reader.boxes if b.index == i)
        ox, oy = reader.sub_origin
        cx, cy = int(ox + box.x + box.w / 2), int(oy + box.y + box.h / 2)

        # Sustained playback with the subtitle overlay drawn — the primary signal: does compositing our
        # overlay drop frames while video advances? (≈0 on fast HW; Xvfb software rendering in CI is more
        # sensitive.) The interaction steps below mostly read ≈0 BY DESIGN — saitenka's pause-lease pauses
        # playback on hover, so the VO isn't advancing frames to drop.
        sample("baseline")

        ipc.command("mouse", cx, cy)  # hover → tooltip composites (pause-lease engages)
        sample("hover")

        for _ in range(4):  # scroll the tooltip — repeated OSD re-uploads, the scroll-jank path
            reader._scroll_tip(round(reader.osd[1] * 0.12))
            reader.poll_once()
        sample("scroll")

        if reader._tip_rect is not None:  # nested popup over an inner word
            tx, ty, tw, th = reader._tip_rect
            ipc.command("mouse", int(tx + tw / 2), int(ty + th / 2))
            sample("nested")

        for k in range(len(reader.boxes)):  # horizontal sweep across the line
            b = reader.boxes[k]
            ipc.command("mouse", int(ox + b.x + b.w / 2), int(oy + b.y + b.h / 2))
            reader.poll_once()
        sample("sweep")

        ipc.command("mouse", 5, 5)  # leave → tooltip torn down
        sample("dismiss")

    return reduce_jank_samples(samples)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", metavar="PATH", help="write the full jank report as JSON")
    ap.add_argument(
        "--bench-json",
        metavar="PATH",
        help="write github-action-benchmark customSmallerIsBetter JSON",
    )
    ap.add_argument(
        "--max-drops",
        type=int,
        default=None,
        help="fail (exit 1) if total dropped frames exceeds this — the e2e catastrophic-jank gate",
    )
    ap.add_argument("--settle-s", type=float, default=0.4, help="playback seconds sampled per step")
    args = ap.parse_args()

    from overlay.mpvio.discover import find_mpv

    if not find_mpv(None):
        print("mpv not found — skipping live jank harness", file=sys.stderr)
        return 0

    result = run(settle_s=args.settle_s)
    print("\nSaitenka overlay — LIVE jank harness (mpv frame-drop counters)")
    for s in result["steps"]:
        print(f"  {s['step']:10} dropped {s['dropped']:4}   delayed {s['delayed']:4}")
    print(
        f"  {'TOTAL':10} dropped {result['total_dropped']:4}   delayed {result['total_delayed']:4}  "
        f"(worst step: {result['max_step_dropped']} dropped)"
    )

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote jank report → {args.json}")
    if args.bench_json:
        Path(args.bench_json).write_text(
            json.dumps(to_bench_json(result), indent=2), encoding="utf-8"
        )
        print(f"wrote github-action-benchmark JSON → {args.bench_json}")

    if args.max_drops is not None and result["total_dropped"] > args.max_drops:
        print(
            f"\nFAIL: {result['total_dropped']} dropped frames > --max-drops {args.max_drops}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
