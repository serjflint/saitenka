"""Deterministic teeth-gate for the Grow loop — the mirror of `sharpen_gate.py`, reversed.

Sharpen guards an EDIT (a change must not drop a test's power); Grow guards an ADDITION (a new test must
ADD power, provably, not just be green). A green suite proves nothing either way — so a proposed grown test
clears the applicable arms below (no LLM in the gate) before it can reach the human. Arms 1-3 apply to an
ordinary scenario/config gap; arm 4 replaces them for a concurrency gap (which has no coverage-line delta
or cosmic-ray property mutant in the usual sense).

  1 property-mutant (load-bearing + genuine growth) — a scenario-encoding mutant must be KILLED by the
    grown suite AND have SURVIVED the pre-existing suite. survives-old ⇒ the behaviour was previously
    uncaught (real growth, not a redundant restatement); killed-new ⇒ teeth + relevance. Reuses
    `sharpen_gate`'s cosmic-ray replay primitive.
  2 oracle-liveness (falsifiable, not vacuous) — negate the grown test's OWN asserts one at a time; a LIVE
    assert makes the test fail. A test that stays green when its oracle is negated asserts nothing
    (swallowed / unreachable / tautological) → BOUNCE. Static-trivial asserts (`assert True`, `x == x`)
    are rejected too — negating a tautology fails spuriously, so it can't be trusted as "live".
  3 context-delta (newly-exercised) — the grown test lights a coverage line the existing suite never ran
    (the dead-config detector). Necessary, not sufficient alone (reached ≠ checked — that's arm 1/2).
  4 concurrency (race fails-on-bug, passes-on-fix) — the grown test ships as a PAIR: a regression that
    PASSES against the guarded code and a negative control that FAILS against the unguarded variant
    (`blanket` scripts the exact interleaving; see tests/test_cache_race.py). The paired control is
    arm-2's oracle-liveness made permanent for a race that has no in-process assert to negate.

Design mirrors `sharpen_gate`: every arm is a pure function over an INJECTED primitive (replay / test-run /
coverage), so the gate logic is unit-tested without a real cosmic-ray, pytest, or coverage run (see
`test_grow_gate.py`). The CLI wires the real subprocess implementations. Not in `poe all` — minutes to run.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sharpen_gate as sg  # reuse Mutant / Replay / replay_is_killed (arm-1) and git_show

# ---------------------------------------------------------------------------------------------------
# Arm 1 — property-mutant: load-bearing AND genuine growth
# ---------------------------------------------------------------------------------------------------


@dataclass
class GrowthReport:
    killed_new: bool  # the grown suite kills the property mutant (teeth + relevance)
    survived_old: bool  # the pre-existing suite did NOT kill it (previously-uncaught behaviour)

    @property
    def ok(self) -> bool:
        # Redundant if the old suite already killed it (survived_old False); vacuous if the grown suite
        # doesn't kill it (killed_new False). Genuine growth needs BOTH.
        return self.killed_new and self.survived_old


def growth_gate(
    module: Path,
    mutant: sg.Mutant,
    old_cmd: list[str],
    new_cmd: list[str],
    *,
    cwd: Path,
    replay: sg.Replay = sg.replay_is_killed,
) -> GrowthReport:
    """Replay one scenario-encoding mutant against the OLD suite (must survive) and the NEW suite
    (grown test included; must kill). ``old_cmd`` excludes the grown test; ``new_cmd`` includes it."""
    survived_old = not replay(module, mutant, old_cmd, cwd)
    killed_new = replay(module, mutant, new_cmd, cwd)
    return GrowthReport(killed_new, survived_old)


# ---------------------------------------------------------------------------------------------------
# Arm 2 — oracle-liveness: at least one falsifiable assert, no vacuous ones
# ---------------------------------------------------------------------------------------------------

# A grown test's source is written to disk and run; RunTest returns True iff it PASSES. Injected so the
# arm's logic is testable without a real pytest subprocess.
RunTest = Callable[[str], bool]


@dataclass
class LivenessReport:
    live: list[int]  # asserts whose negation flips the test red — the teeth
    dead: list[int]  # asserts whose negation left the test green — swallowed / unreachable
    trivial: list[int]  # statically always-true asserts (assert True / x == x)
    no_asserts: bool
    passes_pristine: (
        bool  # a grown test must be green on pristine code (red ⇒ latent bug → issue, not grow)
    )

    @property
    def ok(self) -> bool:
        return (
            not self.no_asserts
            and self.passes_pristine
            and bool(self.live)
            and not self.dead
            and not self.trivial
        )


def _target_func(src: str, test_name: str) -> ast.FunctionDef | None:
    return next(
        (
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == test_name
        ),
        None,
    )


def _asserts_in(src: str, test_name: str) -> list[ast.Assert]:
    fn = _target_func(src, test_name)
    return [] if fn is None else [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]


def _is_trivial(a: ast.Assert) -> bool:
    """Always-true regardless of the code — parity with `sharpen_gate._is_trivial`: constant, `x == x`,
    or `... or True`. Negating one of these fails spuriously, so it can never count as a live oracle."""
    t = a.test
    if isinstance(t, ast.Constant):
        return bool(t.value)
    if (
        isinstance(t, ast.Compare)
        and len(t.comparators) == 1
        and isinstance(t.ops[0], (ast.Eq, ast.Is))
    ):
        return ast.dump(t.left) == ast.dump(t.comparators[0])
    if isinstance(t, ast.BoolOp) and isinstance(t.op, ast.Or):
        return any(isinstance(v, ast.Constant) and v.value for v in t.values)
    return False


def _negate_assert_in(src: str, test_name: str, index: int) -> str:
    """Return ``src`` with the ``index``-th assert of ``test_name`` wrapped in ``not (...)``. Matched by
    node identity, so source-order vs walk-order can't misfire."""
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == test_name)
    target = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)][index]

    class _Negate(ast.NodeTransformer):
        def visit_Assert(self, node: ast.Assert) -> ast.Assert:
            if node is target:
                flipped = ast.Assert(
                    test=ast.UnaryOp(op=ast.Not(), operand=node.test), msg=node.msg
                )
                return ast.copy_location(flipped, node)
            return node

    return ast.unparse(ast.fix_missing_locations(_Negate().visit(tree)))


def liveness_gate(src: str, test_name: str, run_test: RunTest) -> LivenessReport:
    """Per-assert falsifiability. Trivial asserts are recorded but skipped (their negation is unreliable);
    every other assert is negated in isolation and the test re-run — a fail = a live oracle."""
    asserts = _asserts_in(src, test_name)
    if not asserts:
        return LivenessReport([], [], [], no_asserts=True, passes_pristine=False)
    passes_pristine = run_test(src)
    trivial = [i for i, a in enumerate(asserts) if _is_trivial(a)]
    live, dead = [], []
    for i in range(len(asserts)):
        if i in trivial:
            continue
        (live if not run_test(_negate_assert_in(src, test_name, i)) else dead).append(i)
    return LivenessReport(live, dead, trivial, no_asserts=False, passes_pristine=passes_pristine)


# ---------------------------------------------------------------------------------------------------
# Arm 3 — context-delta: the grown test reaches code the existing suite never did
# ---------------------------------------------------------------------------------------------------

# test command -> executed line numbers in the CUT file. Injected so the arm is testable without coverage.
CoverageFn = Callable[[list[str]], set[int]]


@dataclass
class ContextDeltaReport:
    old_lines: set[int]
    new_lines: set[int]

    @property
    def delta(self) -> set[int]:
        return self.new_lines - self.old_lines

    @property
    def ok(self) -> bool:
        return bool(self.delta)


def context_delta_gate(
    old_cmd: list[str], new_cmd: list[str], coverage_fn: CoverageFn
) -> ContextDeltaReport:
    """Lines the grown suite executes in the CUT minus those the existing suite already did. Non-empty =
    a genuinely newly-exercised path (the dead-config signal)."""
    return ContextDeltaReport(coverage_fn(old_cmd), coverage_fn(new_cmd))


# ---------------------------------------------------------------------------------------------------
# Arm 4 — concurrency: a paired regression (passes-guarded) + negative control (fails-unguarded)
# ---------------------------------------------------------------------------------------------------

# test command -> process exit code. Injected so the arm is testable without a real race run.
RunExit = Callable[[list[str]], int]


@dataclass
class ConcurrencyReport:
    regression_passed: bool  # guarded code survives the forced interleaving
    control_failed: bool  # unguarded variant DOES raise under the same schedule (the teeth)

    @property
    def ok(self) -> bool:
        return self.regression_passed and self.control_failed


def concurrency_gate(
    regression_cmd: list[str], control_cmd: list[str], run: RunExit
) -> ConcurrencyReport:
    """A concurrency gap's teeth live in the pair, not a negated assert: the regression must pass against
    the guarded code and the negative control must fail against the unguarded variant."""
    return ConcurrencyReport(run(regression_cmd) == 0, run(control_cmd) != 0)


# ---------------------------------------------------------------------------------------------------
# Real primitives for the CLI (not unit-tested — subprocess/coverage side effects; mirror
# sharpen_gate.replay_is_killed)
# ---------------------------------------------------------------------------------------------------


def _pytest(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:randomly", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def run_pytest_source(test_file: Path, src: str, test_name: str, cwd: Path) -> bool:
    """Write ``src`` to ``test_file``, run the single ``test_name``, restore. True = the test passed."""
    original = test_file.read_text(encoding="utf-8")
    try:
        test_file.write_text(src, encoding="utf-8")
        return _pytest(cwd, f"{test_file}::{test_name}").returncode == 0
    finally:
        test_file.write_text(original, encoding="utf-8")


def covered_lines(cut_file: Path, test_cmd: list[str], cwd: Path) -> set[int]:
    """Executed lines in ``cut_file`` when ``test_cmd`` runs under coverage.py (branch mode). Uses an
    isolated data file so a concurrent `poe cov` run can't collide."""
    import coverage

    data_file = cwd / ".grow_gate.coverage"
    data_file.unlink(missing_ok=True)
    subprocess.run(
        [
            "coverage",
            "run",
            f"--data-file={data_file}",
            "--branch",
            "-m",
            "pytest",
            "-q",
            *test_cmd,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    data = coverage.CoverageData(basename=str(data_file))
    data.read()
    lines = set(data.lines(str((cwd / cut_file).resolve())) or [])
    data_file.unlink(missing_ok=True)
    return lines


# ---------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------


def _run_liveness(args: argparse.Namespace) -> int:
    test_file = args.repo / args.test_file
    src = test_file.read_text(encoding="utf-8")
    rep = liveness_gate(
        src, args.test, lambda s: run_pytest_source(test_file, s, args.test, args.repo)
    )
    if rep.no_asserts:
        print(f"liveness: BOUNCE — {args.test} has no assertions")
        return 1
    if not rep.passes_pristine:
        print(
            f"liveness: BOUNCE — {args.test} is RED on pristine code (latent bug → file an issue, not a grow)"
        )
        return 1
    for i in rep.trivial:
        print(f"  BOUNCE trivial: assert #{i} is always-true")
    for i in rep.dead:
        print(f"  BOUNCE dead: assert #{i} stayed green when negated (swallowed/unreachable)")
    print(
        f"liveness: {'PASS' if rep.ok else 'BOUNCE'} (live={rep.live} dead={rep.dead} trivial={rep.trivial})"
    )
    return 0 if rep.ok else 1


def _run_context(args: argparse.Namespace) -> int:
    def cov(cmd: list[str]) -> set[int]:
        return covered_lines(Path(args.cut), cmd, args.repo)

    rep = context_delta_gate(args.old, args.new, cov)
    print(
        f"context-delta: {'PASS' if rep.ok else 'BOUNCE'} — newly-exercised lines: {sorted(rep.delta)}"
    )
    return 0 if rep.ok else 1


def _run_concurrency(args: argparse.Namespace) -> int:
    def run(cmd: list[str]) -> int:
        return _pytest(args.repo, *cmd).returncode

    rep = concurrency_gate(args.regression, args.control, run)
    print(
        f"concurrency: {'PASS' if rep.ok else 'BOUNCE'} "
        f"(regression_passed={rep.regression_passed} control_failed={rep.control_failed})"
    )
    return 0 if rep.ok else 1


def _run_growth(args: argparse.Namespace) -> int:
    mutant = sg.Mutant(args.operator, args.occurrence, args.occurrence)
    old = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:randomly", *args.old]
    new = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:randomly", *args.new]
    rep = growth_gate(Path(args.module), mutant, old, new, cwd=args.repo)
    print(
        f"growth: {'PASS' if rep.ok else 'BOUNCE'} "
        f"(survived_old={rep.survived_old} killed_new={rep.killed_new})"
    )
    return 0 if rep.ok else 1


def _main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    liv = sub.add_parser("liveness", help="arm 2 — negate each assert; ≥1 must flip the test red")
    liv.add_argument("test_file", type=Path, help="test file relative to repo (overlay/)")
    liv.add_argument("--test", required=True, help="the grown test function name")
    liv.add_argument("--repo", type=Path, default=Path.cwd())

    ctx = sub.add_parser(
        "context", help="arm 3 — grown suite must light a line the existing suite never ran"
    )
    ctx.add_argument("--cut", required=True, help="code-under-test file relative to repo")
    ctx.add_argument("--old", nargs="+", required=True, help="existing-suite pytest args")
    ctx.add_argument(
        "--new", nargs="+", required=True, help="grown-suite pytest args (existing + the new test)"
    )
    ctx.add_argument("--repo", type=Path, default=Path.cwd())

    con = sub.add_parser(
        "concurrency", help="arm 4 — regression passes (guarded) + control fails (unguarded)"
    )
    con.add_argument(
        "--regression", nargs="+", required=True, help="pytest args for the regression test"
    )
    con.add_argument(
        "--control", nargs="+", required=True, help="pytest args for the negative-control test"
    )
    con.add_argument("--repo", type=Path, default=Path.cwd())

    gro = sub.add_parser("growth", help="arm 1 — property mutant survives-old + killed-new")
    gro.add_argument("--module", required=True, help="CUT module path relative to repo")
    gro.add_argument("--operator", required=True, help="cosmic-ray operator name")
    gro.add_argument("--occurrence", type=int, required=True)
    gro.add_argument(
        "--old", nargs="+", required=True, help="existing-suite pytest args (excludes grown test)"
    )
    gro.add_argument(
        "--new", nargs="+", required=True, help="grown-suite pytest args (includes grown test)"
    )
    gro.add_argument("--repo", type=Path, default=Path.cwd())

    args = p.parse_args()
    return {
        "liveness": _run_liveness,
        "context": _run_context,
        "concurrency": _run_concurrency,
        "growth": _run_growth,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(_main())
