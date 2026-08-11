"""Produce a REAL coverage-context under-specification signal for `grow_triage` (kills C5-deep).

Triage's default under-spec proxy is the private-attr seam count, which scales with test VOLUME, not
adequacy (review C5). This runs the suite once under coverage with per-test dynamic contexts and, per
overlay module, counts the code that is genuinely under-specified:

  - UNCOVERED executable lines — the classic lower bound; and
  - WEAKLY-covered lines — executed by ≤1 real test context (covered-but-under-specified, the loop's
    reframe: the line runs, but no more than one test pins it, so its scenarios/combinations are unexplored).

Emits versioned module rows with ``under_spec`` and the executing ``test_nodeids`` for
``grow_triage.py --contexts-json``. Runs the suite under
**pytest-cov + xdist** (the `poe cov` fast path) so it finishes in ~`poe cov` time (~20s), NOT the age a
serial ``coverage run`` takes under free-threaded 3.14t. A one-off producer, NOT part of the gate. The
aggregation is injectable (like the gate arms) so it is unit-tested without a real coverage run.

    uv run python tools/grow_contexts.py --out ../.grow-contexts.json   # feeds grow_triage --contexts-json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sharpen_ledger as sl
from tool_json import InstrumentError

# injected coverage accessors, so aggregate() is pure/testable
MissingFn = Callable[[str], list[int]]  # file -> uncovered executable line numbers
ContextsFn = Callable[[str], dict[int, list[str]]]  # file -> {lineno: [context labels]}


def aggregate(
    measured: Iterable[str], base: Path, missing_fn: MissingFn, contexts_fn: ContextsFn
) -> dict[str, dict[str, object]]:
    """`module_key -> adequacy + executing test ids`, for every measured file under ``base``.
    A line is WEAK when ≤1 real (non-empty) test context executed it — covered, but under-specified."""
    out: dict[str, dict[str, object]] = {}
    for f in measured:
        try:
            mk = str(Path(f).resolve().relative_to(base))
        except ValueError:
            continue  # not under src/overlay (a dependency / test file)
        if not mk.endswith(".py"):
            continue
        contexts = contexts_fn(f)
        weak = sum(1 for cs in contexts.values() if len([c for c in cs if c]) <= 1)
        tests = sorted({c.split("|", 1)[0] for cs in contexts.values() for c in cs if c})
        out[mk] = {"under_spec": len(missing_fn(f)) + weak, "test_nodeids": tests}
    return out


def under_spec_by_module(
    root: Path, test_args: list[str], *, use_contexts: bool = True
) -> dict[str, dict[str, object]]:
    """Run the suite under **pytest-cov + xdist** — the same fast path `poe cov` uses. A raw single-process
    `coverage run` is glacial under free-threaded 3.14t (the C tracer is off), but `-n auto` distributes the
    trace across worker processes, so this finishes in ~`poe cov` time. Then aggregate per module:
      - always: UNCOVERED executable lines (the classic lower bound);
      - with ``use_contexts`` (default): pytest-cov ``--cov-context=test`` per-test dynamic contexts add the
        WEAK (covered-by-≤1-test) refinement — the covered-but-under-specified signal, the loop's reframe.
    Excludes the slow/live tiers (mpv)."""
    import coverage

    data_file = root / ".grow_contexts.coverage"
    data_file.unlink(missing_ok=True)
    env = {**os.environ, "COVERAGE_FILE": str(data_file)}
    cov_ctx = ["--cov-context=test"] if use_contexts else []
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--extra",
            "full",
            "pytest",
            "-q",
            "-n",
            "auto",
            "--cov=overlay",
            "--cov-branch",
            *cov_ctx,
            "--cov-report=",
            "-p",
            "no:randomly",
            "-m",
            "not (slow or live)",
            *test_args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    try:
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
            raise InstrumentError(f"coverage-context run failed ({proc.returncode}): {detail}")
        if not data_file.exists():
            raise InstrumentError("coverage-context run produced no coverage data")
        cov = coverage.Coverage(data_file=str(data_file))
        cov.load()
        data = cov.get_data()
        base = (root / sl.SRC).resolve()
        contexts_fn = data.contexts_by_lineno if use_contexts else (lambda _f: {})
        return aggregate(
            data.measured_files(),
            base,
            lambda f: cov.analysis2(f)[3],  # index 3 = missing (uncovered) line numbers
            contexts_fn,
        )
    finally:
        data_file.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="write the {module: under_spec} JSON here (else stdout)")
    ap.add_argument(
        "--tests", nargs="*", default=["tests"], help="pytest target(s) for the context run"
    )
    ap.add_argument(
        "--no-contexts",
        dest="use_contexts",
        action="store_false",
        help="uncovered-lines only; skip the per-test WEAK-line refinement",
    )
    args = ap.parse_args()
    root = Path.cwd()
    signal = under_spec_by_module(root, args.tests, use_contexts=args.use_contexts)
    payload_obj = {"version": 2, "modules": signal}
    payload = json.dumps(payload_obj, indent=2, sort_keys=True)
    if args.out:
        (root / args.out).write_text(payload + "\n", encoding="utf-8")
    for mk, row in sorted(signal.items(), key=lambda kv: -int(kv[1]["under_spec"]))[:20]:
        print(f"{int(row['under_spec']):5}  {mk}", file=sys.stderr)
    print(payload if not args.out else f"wrote {args.out} ({len(signal)} modules)")


if __name__ == "__main__":
    main()
