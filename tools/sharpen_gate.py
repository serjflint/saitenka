"""Anti-lobotomization gate for the Sharpen loop — deterministic given a mutation session DB.

A green suite proves *nothing* about a quality edit — that's the loop's whole reason to exist. So a
proposed test edit clears two checks (no LLM) before it can reach the human gate:

  A. Efficacy / no-lobotomy — the touched module's mutation score must NOT drop. A targeted cosmic-ray
     replay: every mutant the sharpen *claims* to now kill is verified killed (earned), AND **every**
     mutant the campaign previously killed in the touched function is re-checked (the control set) —
     any that now survives is a regression = a lobotomy the green suite hid = BOUNCE. The control set
     is the *complete* prior-killed set for the function, not a sample, so a weakening that only
     evaporates kills outside the edit's own targets is still caught.

  B. Anti-cheat (static, AST) — the edit must not fake the kill. Bounces a removed / weakened /
     trivially-true assertion, or an expected value read from the code under test.

**Limit, honestly:** static subsumption is undecidable, so Arm B catches *cross-tier* weakening
(equality → truthiness / `is not None`, or a dropped strong assert) but NOT narrowing *within* a tier
(`x == whole` → `x['k'] == part`). That residual is exactly what Arm A's full control set catches —
the narrowed assert lets a previously-killed mutant survive. Neither arm replaces the human merge gate;
together they stop the lobotomies a green run hides.

Replay primitive: ``cosmic-ray apply <module> <operator> <occurrence>`` mutates the module in place by
the exact coordinate stored in a prior campaign's session DB (tools/mutate/run.py); we run the impacted
tests, read the exit code (fail = killed), and restore the exact pre-run bytes. Not in ``poe all`` —
minutes to run.
"""

from __future__ import annotations

import argparse
import ast
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from itertools import starmap
from pathlib import Path

from byte_transaction import ByteSnapshot

# ---------------------------------------------------------------------------------------------------
# A. Efficacy replay
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mutant:
    operator: str  # e.g. "core/ReplaceComparisonOperator_LtE_Lt"
    occurrence: int
    row: int  # source line (for reporting only)


def survivors(db: Path, module_def: str | None = None) -> list[Mutant]:
    """Mutants that SURVIVED the recorded campaign, optionally scoped to one function."""
    return _query(db, "SURVIVED", module_def)


def killed(db: Path, module_def: str | None = None) -> list[Mutant]:
    """Mutants the recorded campaign KILLED — scoped to a function, this is the control set."""
    return _query(db, "KILLED", module_def)


def _query(db: Path, outcome: str, module_def: str | None) -> list[Mutant]:
    con = sqlite3.connect(db)
    sql = (
        "select s.operator_name, s.occurrence, s.start_pos_row "
        "from work_results r join mutation_specs s on r.job_id = s.job_id "
        "where r.test_outcome = ?"
    )
    params: list[object] = [outcome]
    if module_def is not None:
        sql += " and s.definition_name = ?"
        params.append(module_def)
    rows = con.execute(sql, params).fetchall()
    con.close()
    return list(starmap(Mutant, rows))


# A replay predicate: does the current suite kill this mutant? Injectable so the gate logic is testable
# without a real cosmic-ray run.
Replay = Callable[[Path, Mutant, list[str], Path], bool]


def replay_is_killed(module: Path, m: Mutant, test_cmd: list[str], cwd: Path) -> bool:
    """Apply one mutant to ``module`` in place, run ``test_cmd``, restore. True = the suite killed it
    (non-zero exit). The exact pre-run bytes are restored even for a dirty worktree."""
    before = ByteSnapshot.capture(cwd / module)
    try:
        subprocess.run(
            ["cosmic-ray", "apply", str(module), m.operator, str(m.occurrence)],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        proc = subprocess.run(test_cmd, cwd=cwd, capture_output=True, text=True, check=False)
        return proc.returncode != 0  # a failing test IS the kill signal
    finally:
        before.restore()


@dataclass
class EfficacyReport:
    earned: list[Mutant]  # target survivors the sharpened suite now kills
    unearned: list[Mutant]  # target survivors still surviving (likely equivalent)
    regressed: list[Mutant]  # previously-killed mutants that now survive — a LOBOTOMY
    checked_control: int

    @property
    def score_dropped(self) -> bool:
        return bool(self.regressed)

    @property
    def ok(self) -> bool:
        # No previously-killed mutant may survive (score must not drop) AND the sharpen must earn at
        # least one new kill (else it's a zero-value edit — Goodhart bait).
        return not self.score_dropped and bool(self.earned)


def efficacy_gate(
    module: Path,
    targets: list[Mutant],
    control: list[Mutant],
    test_cmd: list[str],
    *,
    cwd: Path,
    replay: Replay = replay_is_killed,
) -> EfficacyReport:
    """Replay each target (claimed kill) and each control (must-stay-killed) mutant. ``control`` should
    be the COMPLETE prior-killed set for the touched function — see the module docstring on why a sample
    is unsafe."""
    earned, unearned = [], []
    for m in targets:
        (earned if replay(module, m, test_cmd, cwd) else unearned).append(m)
    regressed = [m for m in control if not replay(module, m, test_cmd, cwd)]
    return EfficacyReport(earned, unearned, regressed, len(control))


@dataclass
class PreservationReport:
    applied: bool
    killed_before: bool
    killed_after: bool

    @property
    def ok(self) -> bool:
        return self.applied and self.killed_before and self.killed_after


def preservation_adhoc_gate(
    module: Path,
    find: str,
    replace: str,
    test_file: Path,
    before_test: str,
    test_cmd: list[str],
    *,
    cwd: Path,
    run: Callable[[list[str]], int],
) -> PreservationReport:
    """Prove an assertion-changing Sharpen edit retains one pre-existing kill off the mutation allowlist."""
    module_snapshot = ByteSnapshot.capture(cwd / module)
    test_snapshot = ByteSnapshot.capture(cwd / test_file)
    try:
        source = module_snapshot.data.decode("utf-8")
        if source.count(find) != 1:
            return PreservationReport(False, False, False)
        (cwd / module).write_text(source.replace(find, replace), encoding="utf-8")
        (cwd / test_file).write_text(before_test, encoding="utf-8")
        killed_before = run(test_cmd) != 0
        if not killed_before:
            return PreservationReport(True, False, False)
        (cwd / test_file).write_bytes(test_snapshot.data)
        killed_after = run(test_cmd) != 0
        return PreservationReport(True, killed_before, killed_after)
    finally:
        test_snapshot.restore()
        module_snapshot.restore()


# ---------------------------------------------------------------------------------------------------
# B. Anti-cheat assertion diff (static)
# ---------------------------------------------------------------------------------------------------


def _asserts_by_test(src: str) -> dict[str, list[ast.Assert]]:
    """Map each ``test_*`` function to its assert statements (module + one class level deep)."""
    out: dict[str, list[ast.Assert]] = {}
    tree = ast.parse(src)
    funcs = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test")
    ]
    for fn in funcs:
        out[fn.name] = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
    return out


# Specificity tiers — higher = harder to pass by accident. Used to detect cross-tier weakening.
_TRIVIAL, _WEAK, _RICH, _STRONG = 0, 1, 2, 3


def _strength(node: ast.expr) -> int:
    """Rough tier of an assert's test expression. equality-against-a-value=STRONG; partial constraint
    (`<`, `in`, `!=`, dirty-equals call)=RICH; truthiness / `is (not) None`=WEAK; constant=TRIVIAL."""
    if isinstance(node, ast.Constant):
        return _TRIVIAL
    if isinstance(node, ast.Compare):
        comps = [node.left, *node.comparators]
        if any(isinstance(c, ast.Constant) and c.value is None for c in comps):
            return _WEAK  # `is None` / `== None`
        if any(isinstance(o, (ast.Eq, ast.Is)) for o in node.ops):
            return _STRONG  # equality/identity against a value
        return _RICH  # <, <=, >, >=, !=, in, not in
    if isinstance(node, ast.BoolOp):
        return min((_strength(v) for v in node.values), default=_WEAK)
    return _WEAK  # bare name / attribute / call / subscript → truthiness


def _is_trivial(a: ast.Assert) -> bool:
    """An assertion that can never fail: ``assert True`` / ``assert 1``, or a tautology like
    ``assert x == x`` / ``assert 1 == 1``, or ``... or True``."""
    t = a.test
    if isinstance(t, ast.Constant):
        return bool(t.value)
    if (
        isinstance(t, ast.Compare)
        and len(t.comparators) == 1
        and isinstance(t.ops[0], (ast.Eq, ast.Is))
    ):
        return ast.dump(t.left) == ast.dump(t.comparators[0])  # x == x
    if isinstance(t, ast.BoolOp) and isinstance(t.op, ast.Or):
        return any(isinstance(v, ast.Constant) and v.value for v in t.values)  # ... or True
    return False


def _cut_names(src: str, cut_module: str) -> set[str]:
    """Names imported FROM the code-under-test module — the pool for bare-name change-detectors
    (`from saitenka.app.mod import CONST; assert x == CONST`)."""
    if not cut_module:
        return set()
    names: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module == cut_module:
            names |= {a.asname or a.name for a in node.names}
    return names


def _cut_derived(a: ast.Assert, cut_leaf: str, cut_names: set[str]) -> bool:
    """The expected side reads a constant out of the CUT — asserting the module against itself. Catches
    both `assert x == mod.CONST` (module-qualified) and `assert x == CONST` (bare, imported from CUT)."""
    t = a.test
    if not (isinstance(t, ast.Compare) and len(t.comparators) == 1):
        return False
    for side in (t.left, t.comparators[0]):
        if (
            isinstance(side, ast.Attribute)
            and isinstance(side.value, ast.Name)
            and side.value.id == cut_leaf
        ):
            return True  # mod.CONST
        if isinstance(side, ast.Name) and side.id in cut_names:
            return True  # bare CONST imported from the CUT
    return False


@dataclass
class AntiCheatViolation:
    test: str
    kind: str  # removed | weakened | trivial | cut-derived
    detail: str


def anticheat_diff(
    before_src: str, after_src: str, cut_module: str = ""
) -> list[AntiCheatViolation]:
    """Compare a test file before/after an edit. Bounce a removed/weakened/trivial assertion in a test
    that existed before, and any expected-value-from-CUT assertion. Purely additive edits, and genuine
    *strengthening* (a weak assert replaced by a stronger one), pass clean. Merging a test into another
    (its assertions reappear elsewhere in the file) is not a `removed` bounce."""
    before, after = _asserts_by_test(before_src), _asserts_by_test(after_src)
    cut_leaf = cut_module.rsplit(".", 1)[-1] if cut_module else ""
    cut_names = _cut_names(after_src, cut_module)
    all_after_norms = {ast.dump(a.test) for asserts in after.values() for a in asserts}
    violations: list[AntiCheatViolation] = []

    def norm(a: ast.Assert) -> str:
        return ast.dump(a.test)

    for name, before_asserts in before.items():
        after_asserts = after.get(name)
        if after_asserts is None:
            # test vanished — a real delete unless its assertions were merged into another test
            orphaned = [a for a in before_asserts if norm(a) not in all_after_norms]
            if orphaned:
                violations.append(
                    AntiCheatViolation(
                        name, "removed", f"test removed; {len(orphaned)} assertion(s) lost"
                    )
                )
            continue
        missing = {norm(a) for a in before_asserts} - {norm(a) for a in after_asserts}
        if not missing:
            continue  # purely additive (existing asserts all retained) → fine
        # Something changed. Bounce if specificity dropped: fewer strong/rich asserts, or fewer total.
        sb = sum(1 for a in before_asserts if _strength(a.test) >= _STRONG)
        sa = sum(1 for a in after_asserts if _strength(a.test) >= _STRONG)
        rb = sum(1 for a in before_asserts if _strength(a.test) >= _RICH)
        ra = sum(1 for a in after_asserts if _strength(a.test) >= _RICH)
        if sa < sb or ra < rb or len(after_asserts) < len(before_asserts):
            violations.append(
                AntiCheatViolation(name, "weakened", "assertion(s) changed and specificity dropped")
            )

    for name, after_asserts in after.items():
        for a in after_asserts:
            if _is_trivial(a):
                violations.append(
                    AntiCheatViolation(name, "trivial", f"line {a.lineno}: always-true assert")
                )
            elif cut_module and _cut_derived(a, cut_leaf, cut_names):
                violations.append(
                    AntiCheatViolation(
                        name, "cut-derived", f"line {a.lineno}: expected value read from CUT"
                    )
                )
    return violations


def git_show(ref: str, path: Path, *, cwd: Path) -> str:
    """A file's contents at a git ref (the sharpen's 'before'), or '' if it didn't exist there."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=cwd, capture_output=True, text=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else ""


# ---------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------


def _run_anticheat(args: argparse.Namespace) -> int:
    before = git_show(args.ref, args.test_file, cwd=args.repo)
    after = (args.repo / args.test_file).read_text(encoding="utf-8")
    violations = anticheat_diff(before, after, args.cut)
    for v in violations:
        print(f"BOUNCE {v.kind}: {v.test} — {v.detail}")
    print("anticheat: clean" if not violations else f"anticheat: {len(violations)} bounce(s)")
    return 1 if violations else 0


def _run_efficacy(args: argparse.Namespace) -> int:
    db = Path(args.db)
    targets = survivors(
        db, args.func
    )  # the survivors in the touched function the sharpen aims to kill
    control = killed(
        db, args.func
    )  # COMPLETE prior-killed set for the function — the regression net
    test_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-x",
        "-q",
        "--no-header",
        "-p",
        "no:randomly",
        *args.tests,
    ]
    rep = efficacy_gate(Path(args.module), targets, control, test_cmd, cwd=args.repo)
    print(
        f"earned {len(rep.earned)}/{len(targets)} | regressed {len(rep.regressed)}/{rep.checked_control}"
    )
    if rep.regressed:
        for m in rep.regressed:
            print(
                f"  REGRESSED {m.operator} @{m.occurrence} (line {m.row}) — was killed, now survives"
            )
    print(f"efficacy: {'PASS' if rep.ok else 'BOUNCE'} (score_dropped={rep.score_dropped})")
    return 0 if rep.ok else 1


def _run_preservation(args: argparse.Namespace) -> int:
    before = git_show(args.ref, args.test_file, cwd=args.repo)
    test_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-x",
        "-q",
        "--no-header",
        "-p",
        "no:randomly",
        *args.tests,
    ]

    def run(cmd: list[str]) -> int:
        return subprocess.run(
            cmd, cwd=args.repo, capture_output=True, text=True, check=False
        ).returncode

    rep = preservation_adhoc_gate(
        args.module,
        args.find,
        args.replace,
        args.test_file,
        before,
        test_cmd,
        cwd=args.repo,
        run=run,
    )
    print(
        f"preservation: {'PASS' if rep.ok else 'BOUNCE'} "
        f"(applied={rep.applied}, before={rep.killed_before}, after={rep.killed_after})"
    )
    if rep.applied and not rep.killed_before:
        print("witness preflight: old test does not kill this mutation; choose an executed path")
    return 0 if rep.ok else 1


def conformance_improved(before_actionable: int, after_actionable: int) -> bool:
    return before_actionable > 0 and after_actionable < before_actionable


def _run_conformance(args: argparse.Namespace) -> int:
    import sharpen_ledger as sl
    import sharpen_triage as st

    test_map = sl.map_tests_to_modules(args.repo)
    after = st.conformance_by_module(args.repo, test_map).get(args.module, (0, 0, 0))[1]
    improved = conformance_improved(args.before_actionable, after)
    print(
        f"conformance: {'PASS' if improved else 'BOUNCE'} "
        f"(actionable={args.before_actionable}->{after})"
    )
    return 0 if improved else 1


def _main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("anticheat", help="static assertion-diff of a test file vs a git ref")
    a.add_argument("test_file", type=Path)
    a.add_argument("--ref", default="HEAD")
    a.add_argument(
        "--cut", default="", help="module-under-test dotted path, for cut-derived detection"
    )
    a.add_argument("--repo", type=Path, default=Path.cwd())

    e = sub.add_parser(
        "efficacy", help="mutation replay: earned kills + full-control no-regression"
    )
    e.add_argument("--db", required=True, help="cosmic-ray session sqlite from a prior campaign")
    e.add_argument(
        "--module", required=True, help="module path relative to repo, e.g. src/saitenka/app/x.py"
    )
    e.add_argument(
        "--func", required=True, help="the touched function/def name (scopes targets+control)"
    )
    e.add_argument("--tests", nargs="+", required=True, help="test files/args the campaign used")
    e.add_argument("--repo", type=Path, default=Path.cwd())

    h = sub.add_parser(
        "preserve", help="off-allowlist witness: a pre-existing kill must remain killed"
    )
    h.add_argument("--module", type=Path, required=True)
    h.add_argument("--find", required=True, help="exact source text occurring once")
    h.add_argument("--replace", required=True, help="scenario-breaking replacement text")
    h.add_argument("--test-file", type=Path, required=True)
    h.add_argument("--ref", default="HEAD")
    h.add_argument("--tests", nargs="+", required=True)
    h.add_argument("--repo", type=Path, default=Path.cwd())

    c = sub.add_parser(
        "conformance", help="require target-grounded actionable findings to decrease"
    )
    c.add_argument("--module", required=True, help="module key relative to src/saitenka")
    c.add_argument("--before-actionable", type=int, required=True)
    c.add_argument("--repo", type=Path, default=Path.cwd())

    args = p.parse_args()
    if args.cmd == "anticheat":
        return _run_anticheat(args)
    if args.cmd == "efficacy":
        return _run_efficacy(args)
    if args.cmd == "preserve":
        return _run_preservation(args)
    return _run_conformance(args)


if __name__ == "__main__":
    sys.exit(_main())
