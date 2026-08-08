"""Local perf rot-guard (#33): compare a fresh `--synth` bench run to a committed baseline.

The responsiveness bench lives OUTSIDE `poe all`, so a perf regression rots unnoticed (a ~4x slowdown
once slipped past the windowed refactor). This ratchets it: `perf-baseline.json` is the committed
baseline, `poe perf-check` fails when a fresh run regresses past a GENEROUS tolerance, `--bless`
regenerates it after a deliberate change — the same shape as the complexipy ratchet.

It gates the `--synth` mode — a DICT-FREE deterministic corpus (constructed entries, no `overlay.toml`,
no randomness → runnable anywhere, unlike `--vocab`) — on `synth_median_ms` + `synth_p99_ms`. The
tolerance is generous (+50% default) ON PURPOSE: it catches GROSS rot (a 2-4x regression), never
micro-drift, so normal run-to-run wall-clock noise can't flap it (median CV ~1%, p99 CV ~25% at this
sample size — see the `--loops` characterization; the gated p99 is pooled across loops, so it is far
steadier than the per-loop CV).

This is the LOCAL/OPT-IN hard guard (wall-clock + machine-specific → not in `poe all`; deliberate, like
`poe mutate`). The STATISTICAL trend gate is `github-action-benchmark` on gh-pages (see
`.github/workflows/perf.yml`) — the same `--synth` numbers, tracked over CI history with a chart.

    uv run poe perf-check          # fail if regressed past the tolerance
    uv run poe perf-bless          # regenerate the baseline (after a deliberate perf change)
    uv run python tools/perf_gate.py --tolerance 0.3   # tighter gate
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
BASELINE = _ROOT / "perf-baseline.json"
BENCH = _ROOT / "examples" / "bench_responsiveness.py"
# Per-metric tolerance, set from the measured noise (`--synth --loops`): median is stable (CV ~1%) so it
# stays tight; p99 is a tail metric that resamples ~50-75% between back-to-back runs even pooled, so a
# flat +50% would flap → it gets +100%. Both still catch a genuine 2-4x rot (which moves median too).
# A single --tolerance on the CLI overrides all of these.
GATED = {"synth_median_ms": 0.5, "synth_p99_ms": 1.0}
DEFAULT_TOLERANCE = 0.5  # legacy alias (the median tolerance); the CLI default is per-metric (None)


def regressions(
    baseline: dict, current: dict, tolerance: float | None = None
) -> list[tuple[str, float, float, float]]:
    """The gated metrics whose current value exceeds ``baseline * (1 + tol)``, as
    ``(metric, base, cur, ratio)``. ``tolerance`` overrides the per-metric ``GATED`` values when given.
    Pure — the unit-tested seam. A metric missing from either side (a baseline from an older bench
    schema) or a non-positive baseline is skipped, never a false fail."""
    out = []
    for k, per_metric in GATED.items():
        tol = per_metric if tolerance is None else tolerance
        base, cur = baseline.get(k), current.get(k)
        if base is None or cur is None or base <= 0:
            continue
        if cur > base * (1 + tol):
            out.append((k, base, cur, cur / base))
    return out


def _run_synth_bench(reps: int, loops: int) -> dict:
    """Run the `--synth` bench in a subprocess (it re-execs itself with PYTHON_GIL=0) and return the
    metrics dict it writes. Dict-free → needs no overlay.toml, so it runs anywhere (local or CI)."""
    with tempfile.NamedTemporaryFile("r", encoding="utf-8", suffix=".json", delete=False) as f:
        dest = Path(f.name)
    try:
        rc = subprocess.run(  # fixed argv, no shell, our own bench script
            [
                sys.executable,
                str(BENCH),
                "--synth",
                "--reps",
                str(reps),
                "--loops",
                str(loops),
                "--json",
                str(dest),
            ],
            check=False,
        ).returncode
        if rc != 0:
            raise SystemExit(f"synth bench failed (rc={rc})")
        return json.loads(dest.read_text(encoding="utf-8"))
    finally:
        dest.unlink(missing_ok=True)


def _print_row(baseline: dict, current: dict) -> None:
    for k in GATED:
        b, c = baseline.get(k), current.get(k)
        if b and c:
            print(f"  {k:16} baseline {b:7.1f} → current {c:7.1f} ms  ({c / b:.2f}x)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Local perf rot-guard (#33).")
    ap.add_argument("--bless", action="store_true", help="regenerate the committed baseline")
    ap.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="fail past base*(1+tol); overrides the per-metric GATED tolerances when set",
    )
    ap.add_argument("--reps", type=int, default=5, help="bench reps (more = steadier percentiles)")
    ap.add_argument(
        "--loops", type=int, default=3, help="corpus repeats (pools the p99 sample → steadier)"
    )
    args = ap.parse_args()

    current = _run_synth_bench(args.reps, args.loops)

    if args.bless:
        BASELINE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"blessed perf baseline → {BASELINE}")
        for k in GATED:
            print(f"  {k}: {current.get(k)}")
        return 0

    if not BASELINE.exists():
        raise SystemExit("no perf-baseline.json — run `uv run poe perf-bless` first")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    _print_row(baseline, current)
    regs = regressions(baseline, current, args.tolerance)
    limit = (
        "the per-metric tolerance" if args.tolerance is None else f"+{args.tolerance * 100:.0f}%"
    )
    if regs:
        print(f"\nFAIL: perf regressed past {limit}:", file=sys.stderr)
        for k, b, c, r in regs:
            print(f"  {k}: {b:.1f} → {c:.1f} ms ({r:.2f}x)", file=sys.stderr)
        print("If deliberate, re-bless: `uv run poe perf-bless`.", file=sys.stderr)
        return 1
    print(f"\nPASS: within {limit} of baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
