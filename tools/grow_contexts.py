"""Produce a REAL coverage-context under-specification signal for `grow_triage` (kills C5-deep).

Triage's default under-spec proxy is the private-attr seam count, which scales with test VOLUME, not
adequacy (review C5). This runs the suite once under coverage with per-test dynamic contexts and, per
overlay module, counts the code that is genuinely under-specified:

  - UNCOVERED executable lines — the classic lower bound; and
  - WEAKLY-covered lines — executed by ≤1 real test context (covered-but-under-specified, the loop's
    reframe: the line runs, but no more than one test pins it, so its scenarios/combinations are unexplored).

Emits versioned module rows with ``under_spec``, line coordinates, and executing ``test_nodeids`` for
``grow_triage.py --contexts-json``. Runs the suite under
**pytest-cov + xdist** (the `poe cov` fast path) so it finishes in ~`poe cov` time (~20s), NOT the age a
serial ``coverage run`` takes under free-threaded 3.14t. A one-off producer, NOT part of the gate. The
aggregation is injectable (like the gate arms) so it is unit-tested without a real coverage run.

    uv run python tools/grow_contexts.py --out ../.grow-contexts.json   # feeds grow_triage --contexts-json
    uv run python tools/grow_contexts.py --inspect ../.grow-contexts.json --module app/controller.py
    uv run python tools/grow_contexts.py --inspect ../.grow-contexts.json --module app/controller.py --show lines
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


def validate_row(row: object, module: str, version: int) -> dict[str, object]:
    """Validate one persisted module row before presenting it as evidence."""
    if not isinstance(row, dict):
        raise InstrumentError(f"contexts JSON has invalid evidence for {module}")
    under_spec = row.get("under_spec")
    nodeids = row.get("test_nodeids")
    if (
        type(under_spec) is not int
        or not isinstance(nodeids, list)
        or not all(isinstance(nodeid, str) for nodeid in nodeids)
    ):
        raise InstrumentError(f"contexts JSON has invalid evidence for {module}")
    if version == 2:
        return row
    uncovered = row.get("uncovered_lines")
    weak = row.get("weak_lines")

    def valid_weak_item(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        item_nodeids = item.get("test_nodeids")
        return (
            type(item.get("line")) is int
            and isinstance(item_nodeids, list)
            and all(isinstance(nodeid, str) for nodeid in item_nodeids)
        )

    valid_uncovered = isinstance(uncovered, list) and all(type(line) is int for line in uncovered)
    valid_weak = isinstance(weak, list) and all(valid_weak_item(item) for item in weak)
    if not valid_uncovered or not valid_weak:
        raise InstrumentError(f"contexts JSON has invalid v3 line evidence for {module}")
    assert isinstance(uncovered, list) and isinstance(weak, list)
    if under_spec != len(uncovered) + len(weak):
        raise InstrumentError(f"contexts JSON has invalid v3 line evidence for {module}")
    return row


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
            continue  # not under src/saitenka (a dependency / test file)
        if not mk.endswith(".py"):
            continue
        contexts = contexts_fn(f)
        uncovered = sorted(set(missing_fn(f)))
        weak_lines = []
        for line, labels in sorted(contexts.items()):
            nodeids = sorted({label.split("|", 1)[0] for label in labels if label})
            if len(nodeids) <= 1:
                weak_lines.append({"line": line, "test_nodeids": nodeids})
        tests = sorted(
            {label.split("|", 1)[0] for labels in contexts.values() for label in labels if label}
        )
        out[mk] = {
            "under_spec": len(uncovered) + len(weak_lines),
            "uncovered_lines": uncovered,
            "weak_lines": weak_lines,
            "test_nodeids": tests,
        }
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
        "--inspect", help="read an existing contexts JSON instead of rerunning coverage"
    )
    ap.add_argument("--module", help="module row to print with --inspect, e.g. app/controller.py")
    ap.add_argument("--show", choices=("summary", "lines", "tests", "full"), default="summary")
    ap.add_argument("--limit", type=int, default=50, help="maximum line/test details to print")
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
    if args.inspect:
        if not args.module:
            ap.error("--inspect requires --module")
        data = json.loads((root / args.inspect).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") not in {2, 3}:
            raise InstrumentError("contexts JSON is not v2/v3; regenerate it with grow_contexts.py")
        modules = data.get("modules")
        if not isinstance(modules, dict) or args.module not in modules:
            raise InstrumentError(f"contexts JSON has no module {args.module}")
        row = validate_row(modules[args.module], args.module, int(data["version"]))
        if args.show == "summary":
            output = {
                "under_spec": row.get("under_spec"),
                "uncovered_lines": len(row.get("uncovered_lines", [])),
                "weak_lines": len(row.get("weak_lines", [])),
                "test_nodeids": len(row.get("test_nodeids", [])),
                "line_evidence": "available" if "weak_lines" in row else "regenerate-v3",
            }
        elif args.show == "lines":
            output = {
                "uncovered_lines": row.get("uncovered_lines", [])[: args.limit],
                "weak_lines": row.get("weak_lines", [])[: args.limit],
            }
        elif args.show == "tests":
            output = {"test_nodeids": row.get("test_nodeids", [])[: args.limit]}
        else:
            output = row
        print(json.dumps(output, indent=2, sort_keys=True))
        return
    if args.module:
        ap.error("--module requires --inspect")
    signal = under_spec_by_module(root, args.tests, use_contexts=args.use_contexts)
    payload_obj = {"version": 3, "modules": signal}
    payload = json.dumps(payload_obj, indent=2, sort_keys=True)
    if args.out:
        (root / args.out).write_text(payload + "\n", encoding="utf-8")
    for mk, row in sorted(signal.items(), key=lambda kv: -int(kv[1]["under_spec"]))[:20]:
        print(f"{int(row['under_spec']):5}  {mk}", file=sys.stderr)
    print(payload if not args.out else f"wrote {args.out} ({len(signal)} modules)")


if __name__ == "__main__":
    main()
