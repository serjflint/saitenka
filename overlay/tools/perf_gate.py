"""Local perf rot-guard (#33): compare a fresh `--vocab` bench run to a committed baseline.

The responsiveness bench lives OUTSIDE `poe all`, so a perf regression rots unnoticed (a ~4x slowdown
once slipped past the windowed refactor). This ratchets it: `perf-baseline.json` is the committed
baseline, `poe perf-check` fails when a fresh run regresses past a GENEROUS tolerance, `--bless`
regenerates it after a deliberate change — the same shape as the complexipy ratchet.

It gates the `--vocab` mode (pure render throughput: no real sleeps, no threading contention → the
most machine-noise-resistant metric) on `full_median_ms` + `full_p99_ms`. The tolerance is generous
(+50% default) ON PURPOSE: it catches GROSS rot (a 2-4x regression), never micro-drift, so normal
run-to-run wall-clock noise can't flap it. Because it's wall-clock and machine-specific, it is
LOCAL/OPT-IN — never folded into `poe all` or CI (that needs the noise characterization + CI history
#33's harder half still tracks). Deliberate, like `poe mutate`.

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
GATED = ("full_median_ms", "full_p99_ms")  # the --vocab metrics we ratchet
DEFAULT_TOLERANCE = 0.5  # +50%: catch gross rot, never micro-drift


def regressions(
    baseline: dict, current: dict, tolerance: float
) -> list[tuple[str, float, float, float]]:
    """The gated metrics whose current value exceeds ``baseline * (1 + tolerance)``, as
    ``(metric, base, cur, ratio)``. Pure — the unit-tested seam. A metric missing from either side (a
    baseline from an older bench schema) or a non-positive baseline is skipped, never a false fail."""
    out = []
    for k in GATED:
        base, cur = baseline.get(k), current.get(k)
        if base is None or cur is None or base <= 0:
            continue
        if cur > base * (1 + tolerance):
            out.append((k, base, cur, cur / base))
    return out


def _run_vocab_bench(reps: int) -> dict:
    """Run the `--vocab` bench in a subprocess (it re-execs itself with PYTHON_GIL=0) and return the
    metrics dict it writes. Raises if the bench fails — `--vocab` needs dicts configured in overlay.toml."""
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as f:
        dest = Path(f.name)
    try:
        rc = subprocess.run(  # fixed argv, no shell, our own bench script
            [sys.executable, str(BENCH), "--vocab", "--reps", str(reps), "--json", str(dest)],
            check=False,
        ).returncode
        if rc != 0:
            raise SystemExit(f"bench failed (rc={rc}) — `--vocab` needs dicts in overlay.toml")
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
        "--tolerance", type=float, default=DEFAULT_TOLERANCE, help="fail past base*(1+tol)"
    )
    ap.add_argument("--reps", type=int, default=2, help="bench reps (more = steadier percentiles)")
    args = ap.parse_args()

    current = _run_vocab_bench(args.reps)

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
    if regs:
        print(f"\nFAIL: perf regressed past +{args.tolerance * 100:.0f}%:", file=sys.stderr)
        for k, b, c, r in regs:
            print(f"  {k}: {b:.1f} → {c:.1f} ms ({r:.2f}x)", file=sys.stderr)
        print("If deliberate, re-bless: `uv run poe perf-bless`.", file=sys.stderr)
        return 1
    print(f"\nPASS: within +{args.tolerance * 100:.0f}% of baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
