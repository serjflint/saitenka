"""Deterministic teeth-gate for the Grow loop — the mirror of `sharpen_gate.py`, reversed.

Sharpen guards an EDIT (a change must not drop a test's power); Grow guards an ADDITION (a new test must
ADD power, provably, not just be green). A green suite proves nothing either way — so a proposed grown test
clears the applicable arms below (no LLM in the gate) before it can reach the human. Arms 1-3 apply to an
ordinary scenario/config gap; arm 4 replaces them for a concurrency gap (which has no coverage-line delta
or cosmic-ray property mutant in the usual sense).

  1 property-mutant (load-bearing + genuine growth) — a scenario-encoding mutant must be KILLED by the
    grown suite AND have SURVIVED the pre-existing suite. `growth_gate` uses `sharpen_gate`'s cosmic-ray
    replay (only the 4 `poe mutate` targets); `growth_adhoc_gate` generalises it to ANY module via an
    author-supplied one-line text mutation (apply → old survives → new kills → restore), so this arm is
    available off the cosmic-ray allowlist — the common case (C2).
  2 oracle-liveness (falsifiable, not vacuous) — negate the grown test's OWN asserts one at a time; a LIVE
    assert makes the test fail. Trivial asserts (`assert True`, `x == x`) are rejected. A `pytest.raises`/
    `warns` block counts as a live oracle without negation (C8).
  3 context-delta (newly-exercised) — the grown test lights a coverage line the existing suite never ran
    (the dead-config detector). Necessary, not sufficient alone (reached ≠ checked — that's arm 1/2). The
    OLD baseline must EXCLUDE the grown test (`--deselect`) or extend-before-add collapses the delta (C3).
  4 concurrency — the grown test ships as a PAIR of PASSING tests: a regression (the guard prevents the
    bug under the forced schedule) + a self-certifying negative control that unguards a throwaway instance
    and asserts the bug REPRODUCES (`blanket` scripts the interleaving; see tests/test_cache_race.py). The
    teeth are the control's own falsifiable assertion — a passing control with a LIVE oracle proves the
    schedule reproduces the bug unguarded (C6). Both pass; arm-2 liveness on the control gives it teeth.

Plus a Grow↔Sharpen boundary check (`additive_gate`): the edit may only ADD assert nodes — a real adds-only
diff, NOT `sharpen_gate.anticheat_diff` (which only flags a specificity drop, so a same-tier value change
slips past as 'additive' — C4).

Design mirrors `sharpen_gate`: every arm is a pure function over an INJECTED primitive (replay / test-run /
coverage), so the gate logic is unit-tested without a real cosmic-ray, pytest, or coverage run (see
`test_grow_gate.py`). The CLI wires the real subprocess implementations. Not in `poe all` — minutes to run.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sharpen_gate as sg  # reuse Mutant / Replay / replay_is_killed (arm-1) and git_show
from byte_transaction import ByteSnapshot
from tool_json import InstrumentError

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


# Arm 1, off the cosmic-ray allowlist: the author supplies a one-line textual mutation that ENCODES the
# scenario's violation (e.g. route a mining kwarg to the wrong group). We apply it, require the OLD suite
# to survive it (previously-uncaught) and the GROWN suite to kill it (teeth + relevance), then restore.
# This makes "genuine growth over covered code" checkable for any module, not just the 4 mutate targets
# (C2) — the mechanism `vibe/proto_arms_1_3.py` validated, generalised from monkeypatch to a text edit.

ApplyMutation = Callable[[Path, str, str, Path], bool]  # (cut, find, replace, cwd) -> applied?
Snapshot = Callable[[Path], ByteSnapshot]


@dataclass
class AdhocGrowthReport:
    applied: bool  # the textual mutation matched exactly once and was written
    survived_old: bool  # the existing suite passed under the mutant → previously-uncaught
    killed_new: bool  # the grown suite failed under the mutant → teeth + relevance

    @property
    def ok(self) -> bool:
        return self.applied and self.survived_old and self.killed_new


def _apply_text_mutation(cut: Path, find: str, replace: str, cwd: Path) -> bool:
    """Apply ``find`` → ``replace`` in the CUT iff ``find`` occurs EXACTLY once (an ambiguous or absent
    match is not a clean scenario mutant — refuse rather than mutate the wrong site)."""
    path = cwd / cut
    src = path.read_text(encoding="utf-8")
    if src.count(find) != 1:
        return False
    path.write_text(src.replace(find, replace), encoding="utf-8")
    return True


def growth_adhoc_gate(
    cut: Path,
    find: str,
    replace: str,
    old_cmd: list[str],
    new_cmd: list[str],
    run: RunExit,
    *,
    apply_mutation: ApplyMutation = _apply_text_mutation,
    snapshot: Snapshot = ByteSnapshot.capture,
    cwd: Path,
) -> AdhocGrowthReport:
    """Apply the author's scenario-encoding text mutation; the OLD suite must SURVIVE (exit 0) and the
    GROWN suite must be KILLED (non-zero); always restore. Injectable so the logic is unit-tested without
    touching disk. Restoration uses the pre-run bytes, not Git, so dirty worktrees are preserved."""
    before = snapshot(cwd / cut)
    try:
        if not apply_mutation(cut, find, replace, cwd):
            return AdhocGrowthReport(applied=False, survived_old=False, killed_new=False)
        survived_old = run(old_cmd) == 0
        killed_new = run(new_cmd) != 0
    finally:
        before.restore()
    return AdhocGrowthReport(applied=True, survived_old=survived_old, killed_new=killed_new)


# ---------------------------------------------------------------------------------------------------
# The Grow↔Sharpen boundary — a real adds-only assert diff (NOT sharpen_gate's specificity check)
# ---------------------------------------------------------------------------------------------------


@dataclass
class AdditiveReport:
    removed: list[
        str
    ]  # assert-test nodes present before but altered/removed after — a MUTATIVE edit

    @property
    def ok(self) -> bool:
        return not self.removed


def _assert_dumps(src: str) -> Counter[str]:
    """Multiset of normalised assert-test dumps across every ``test_*`` function in the file."""
    dumps: Counter[str] = Counter()
    for fn in ast.walk(ast.parse(src)):
        if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test"):
            for n in ast.walk(fn):
                if isinstance(n, ast.Assert):
                    dumps[ast.dump(n.test)] += 1
    return dumps


def additive_gate(before_src: str, after_src: str) -> AdditiveReport:
    """Grow may only ADD assertions; altering or removing an existing one is Sharpen's job. Compare the
    assert-node multiset: any before-assert missing (or less frequent) after was changed/removed → MUTATIVE
    → bounce. Pure additions and moves/renames (same nodes elsewhere) pass. This is the deterministic
    boundary `sharpen_gate.anticheat_diff` does NOT provide — it flags a specificity DROP, so a same-tier
    value change (`== 1` → `== 2`, a change-detector or Sharpen-scope edit) slips past it as 'additive' (C4)."""
    leftover = _assert_dumps(before_src) - _assert_dumps(after_src)
    return AdditiveReport(sorted(leftover))


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
    raises: int  # `pytest.raises`/`warns` oracle blocks — live-by-construction (see below)
    no_asserts: bool  # no oracle of ANY kind (neither an assert nor a raises/warns block)
    passes_pristine: bool  # green on pristine code (red ⇒ latent bug → issue, not grow)

    @property
    def ok(self) -> bool:
        # A `with pytest.raises(X):` block IS a falsifiable oracle: it passed on pristine, so the code DID
        # raise X; if the code stops raising, the test fails. We count it as live-by-construction rather
        # than negate it (unwrapping the CM is a heavier transform; the precision of X is not liveness-
        # checked — a documented limit). So teeth = ≥1 live assert OR ≥1 raises/warns block.
        return (
            not self.no_asserts
            and self.passes_pristine
            and (bool(self.live) or self.raises > 0)
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


def _raises_count(src: str, test_name: str) -> int:
    """Number of ``with pytest.raises(...)`` / ``pytest.warns(...)`` oracle blocks in ``test_name`` — an
    exception-oracle test carries no ``assert`` node but is not vacuous (C8)."""
    fn = _target_func(src, test_name)
    if fn is None:
        return 0
    n = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call):
                    f = call.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name in {"raises", "warns"}:
                        n += 1
    return n


def liveness_gate(src: str, test_name: str, run_test: RunTest) -> LivenessReport:
    """Per-assert falsifiability. Trivial asserts are recorded but skipped (their negation is unreliable);
    every other assert is negated in isolation and the test re-run — a fail = a live oracle. A
    ``pytest.raises``/``warns`` block counts as a live oracle without negation (see ``LivenessReport.ok``)."""
    asserts = _asserts_in(src, test_name)
    raises = _raises_count(src, test_name)
    if not asserts and not raises:
        return LivenessReport([], [], [], raises=0, no_asserts=True, passes_pristine=False)
    passes_pristine = run_test(src)
    trivial = [i for i, a in enumerate(asserts) if _is_trivial(a)]
    live, dead = [], []
    for i in range(len(asserts)):
        if i in trivial:
            continue
        (live if not run_test(_negate_assert_in(src, test_name, i)) else dead).append(i)
    return LivenessReport(
        live, dead, trivial, raises=raises, no_asserts=False, passes_pristine=passes_pristine
    )


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
# Arm 4 — concurrency: a pair of PASSING tests (regression + a self-certifying negative control)
# ---------------------------------------------------------------------------------------------------

# test command -> process exit code. Injected so the arm is testable without a real race run.
RunExit = Callable[[list[str]], int]


@dataclass
class ConcurrencyReport:
    regression_passed: bool  # the guard prevents the bug under the forced schedule (green)
    control_passed: bool  # the negative control passes — it asserts the bug REPRODUCES unguarded
    control_has_live_oracle: bool  # arm-2 liveness on the control: its assertion is falsifiable

    @property
    def ok(self) -> bool:
        # Both are PASSING tests (matching the shipped test_cache_race.py structure): the regression
        # (guard present → no error) and a negative control that unguards a throwaway instance and asserts
        # the bug DOES surface. The teeth are the control's own falsifiable assertion — a passing control
        # with a LIVE oracle deterministically proves the forced schedule reproduces the bug when unguarded
        # (if it stopped reproducing, the live assertion would fail). So arm-4 = regression green + control
        # green + control-oracle-live. (A stronger form — apply-the-unguard-and-require-the-regression-to-
        # fail — is `growth_adhoc_gate`'s territory; deferred for races, which resist textual mutation.)
        return self.regression_passed and self.control_passed and self.control_has_live_oracle


def concurrency_gate(
    regression_cmd: list[str],
    control_cmd: list[str],
    run: RunExit,
    *,
    control_has_live_oracle: bool,
) -> ConcurrencyReport:
    """Regression + negative-control, both PASSING; ``control_has_live_oracle`` is the arm-2 liveness
    verdict on the control (computed by the caller), which is what gives the passing control teeth."""
    return ConcurrencyReport(
        run(regression_cmd) == 0, run(control_cmd) == 0, control_has_live_oracle
    )


# ---------------------------------------------------------------------------------------------------
# Real primitives for the CLI (not unit-tested — subprocess/coverage side effects; mirror
# sharpen_gate.replay_is_killed)
# ---------------------------------------------------------------------------------------------------


def _pytest(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:randomly", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def run_pytest_source(test_file: Path, src: str, test_name: str, cwd: Path) -> bool:
    """Write ``src`` to ``test_file``, run the single ``test_name``, restore. True = the test passed."""
    original = ByteSnapshot.capture(test_file)
    try:
        test_file.write_text(src, encoding="utf-8")
        return _pytest(cwd, f"{test_file}::{test_name}").returncode == 0
    finally:
        original.restore()


def covered_lines(cut_file: Path, test_cmd: list[str], cwd: Path) -> set[int]:
    """Executed lines in ``cut_file`` when ``test_cmd`` runs under coverage.py (branch mode). Uses an
    isolated data file so a concurrent `poe cov` run can't collide."""
    import coverage

    data_file = cwd / ".grow_gate.coverage"
    data_file.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
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
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
            raise InstrumentError(f"coverage measurement failed ({proc.returncode}): {detail}")
        if not data_file.is_file():
            raise InstrumentError("coverage measurement produced no data file")
        data = coverage.CoverageData(basename=str(data_file))
        data.read()
        return set(data.lines(str((cwd / cut_file).resolve())) or [])
    finally:
        data_file.unlink(missing_ok=True)


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
        f"liveness: {'PASS' if rep.ok else 'BOUNCE'} "
        f"(live={rep.live} dead={rep.dead} trivial={rep.trivial} raises={rep.raises})"
    )
    return 0 if rep.ok else 1


def _run_context(args: argparse.Namespace) -> int:
    def cov(cmd: list[str]) -> set[int]:
        return covered_lines(Path(args.cut), cmd, args.repo)

    # C3: the grown test lives IN one of the --old files when extending (extend-before-add). Deselect it
    # from the OLD baseline so `old` is genuinely the pre-grow suite, or delta collapses to ∅ (false bounce).
    old_cmd = [*args.old, *(x for node in args.deselect for x in ("--deselect", node))]
    rep = context_delta_gate(old_cmd, args.new, cov)
    print(
        f"context-delta: {'PASS' if rep.ok else 'BOUNCE'} — newly-exercised lines: {sorted(rep.delta)}"
    )
    return 0 if rep.ok else 1


def _run_concurrency(args: argparse.Namespace) -> int:
    def run(cmd: list[str]) -> int:
        return _pytest(args.repo, *cmd).returncode

    # The control's teeth are its own falsifiable assertion — run arm-2 liveness on it here (the caller
    # can override with --control-live if it computed liveness separately).
    control_live = args.control_live
    if control_live is None and args.control_file and args.control_test:
        cf = args.repo / args.control_file
        lv = liveness_gate(
            cf.read_text(encoding="utf-8"),
            args.control_test,
            lambda s: run_pytest_source(cf, s, args.control_test, args.repo),
        )
        control_live = lv.ok
    rep = concurrency_gate(
        args.regression, args.control, run, control_has_live_oracle=bool(control_live)
    )
    print(
        f"concurrency: {'PASS' if rep.ok else 'BOUNCE'} "
        f"(regression_passed={rep.regression_passed} control_passed={rep.control_passed} "
        f"control_has_live_oracle={rep.control_has_live_oracle})"
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


def _run_growth_adhoc(args: argparse.Namespace) -> int:
    def run(cmd: list[str]) -> int:
        return _pytest(args.repo, *cmd).returncode

    # --deselect keeps the grown test OUT of --old (the existing suite must SURVIVE the mutant); when
    # extending an existing file the grown test would otherwise sit in --old and spuriously "kill" it.
    old = [*args.old, *(x for node in args.deselect for x in ("--deselect", node))]
    rep = growth_adhoc_gate(
        Path(args.cut), args.find, args.replace, old, args.new, run, cwd=args.repo
    )
    print(
        f"growth-adhoc: {'PASS' if rep.ok else 'BOUNCE'} "
        f"(applied={rep.applied} survived_old={rep.survived_old} killed_new={rep.killed_new})"
    )
    return 0 if rep.ok else 1


def _run_additive(args: argparse.Namespace) -> int:
    before = sg.git_show(args.ref, args.test_file, cwd=args.repo)
    after = (args.repo / args.test_file).read_text(encoding="utf-8")
    rep = additive_gate(before, after)
    for d in rep.removed:
        print(
            f"  BOUNCE mutative: an existing assertion was altered/removed → Sharpen scope: {d[:80]}"
        )
    print(
        f"additive: {'PASS' if rep.ok else 'BOUNCE'} ({len(rep.removed)} altered/removed assert(s))"
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
    ctx.add_argument(
        "--deselect",
        nargs="*",
        default=[],
        help="node id(s) to deselect from --old (the grown test, when extending an existing file)",
    )
    ctx.add_argument("--repo", type=Path, default=Path.cwd())

    con = sub.add_parser(
        "concurrency", help="arm 4 — regression + self-certifying negative control, both PASS"
    )
    con.add_argument(
        "--regression", nargs="+", required=True, help="pytest args for the regression test"
    )
    con.add_argument(
        "--control", nargs="+", required=True, help="pytest args for the negative-control test"
    )
    con.add_argument(
        "--control-file", help="control test file (to run arm-2 liveness on its oracle)"
    )
    con.add_argument("--control-test", help="control test function name")
    con.add_argument(
        "--control-live",
        type=lambda s: s.lower() == "true",
        default=None,
        help="override: pass the arm-2 liveness verdict on the control (true/false)",
    )
    con.add_argument("--repo", type=Path, default=Path.cwd())

    gro = sub.add_parser(
        "growth", help="arm 1 — cosmic-ray property mutant survives-old + killed-new"
    )
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

    adh = sub.add_parser(
        "growth-adhoc", help="arm 1 off-allowlist — author text mutant survives-old + killed-new"
    )
    adh.add_argument("--cut", required=True, help="CUT file relative to repo")
    adh.add_argument(
        "--find", required=True, help="exact source snippet to mutate (must occur once)"
    )
    adh.add_argument("--replace", required=True, help="the scenario-violating replacement")
    adh.add_argument("--old", nargs="+", required=True, help="existing-suite pytest args")
    adh.add_argument("--new", nargs="+", required=True, help="grown-suite pytest args")
    adh.add_argument(
        "--deselect",
        nargs="*",
        default=[],
        help="node id(s) to deselect from --old (the grown test)",
    )
    adh.add_argument("--repo", type=Path, default=Path.cwd())

    add = sub.add_parser(
        "additive", help="Grow↔Sharpen boundary — the edit only ADDS asserts (never alters/removes)"
    )
    add.add_argument("test_file", type=Path, help="edited test file relative to repo")
    add.add_argument("--ref", default="HEAD", help="git ref for the 'before' state")
    add.add_argument("--repo", type=Path, default=Path.cwd())

    args = p.parse_args()
    return {
        "liveness": _run_liveness,
        "context": _run_context,
        "concurrency": _run_concurrency,
        "growth": _run_growth,
        "growth-adhoc": _run_growth_adhoc,
        "additive": _run_additive,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(_main())
