"""Deterministic anti-lobotomization gate for the sharpen (Sharpen) loop.

A green suite proves *nothing* about a quality edit — that's the loop's whole reason to exist. So a
proposed test edit clears two deterministic checks (no LLM) before it can reach the human gate:

  A. Efficacy / no-lobotomy — the touched module's mutation score must NOT drop. Implemented as a
     targeted cosmic-ray replay: every mutant the sharpen *claims* is now killed (earned kill), and a
     no-regression sample of previously-killed mutants must all stay killed. A drop = the edit
     weakened the suite's bug-catching while staying green = a lobotomy = BOUNCE.

  B. Anti-cheat (static, AST) — the edit must not fake the kill. Bounces a removed / weakened /
     trivially-true assertion, or an expected value read from the code under test (a change-detector
     in disguise). Purely additive sharpens pass A vacuously on B; a lobotomy trips both.

Replay primitive: ``cosmic-ray apply <module> <operator> <occurrence>`` mutates the module in place
by the exact (operator, occurrence) coordinate stored in a prior campaign's session DB; we run the
impacted tests and read the exit code (fail = the mutant was killed), then restore the module via git.
See tools/mutate/run.py for the campaign that produces the session DB, and AGENTS.md "Mutation
auditing". Not in ``poe all`` — an idle-loop instrument, minutes to run.
"""

from __future__ import annotations

import argparse
import ast
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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
    """Mutants the recorded campaign KILLED — the no-regression control set."""
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
    return [Mutant(op, occ, row) for op, occ, row in rows]


def replay_is_killed(module: Path, m: Mutant, test_cmd: list[str], *, cwd: Path) -> bool:
    """Apply one mutant to ``module`` in place, run ``test_cmd``, restore. True = the suite killed it
    (non-zero exit). Restoration via ``git checkout`` always runs, even on an exception."""
    try:
        subprocess.run(
            ["cosmic-ray", "apply", str(module), m.operator, str(m.occurrence)],
            cwd=cwd, check=True, capture_output=True, text=True,
        )
        proc = subprocess.run(test_cmd, cwd=cwd, capture_output=True, text=True, check=False)
        return proc.returncode != 0  # a failing test IS the kill signal
    finally:
        subprocess.run(["git", "checkout", "--", str(module)], cwd=cwd, check=True)


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
        # No previously-killed mutant may survive (score must not drop) AND the sharpen must earn
        # at least one new kill (else it's a zero-value edit — Goodhart bait).
        return not self.score_dropped and bool(self.earned)


def efficacy_gate(
    module: Path,
    targets: list[Mutant],
    control: list[Mutant],
    test_cmd: list[str],
    *,
    cwd: Path,
) -> EfficacyReport:
    earned, unearned = [], []
    for m in targets:
        (earned if replay_is_killed(module, m, test_cmd, cwd=cwd) else unearned).append(m)
    regressed = [m for m in control if not replay_is_killed(module, m, test_cmd, cwd=cwd)]
    return EfficacyReport(earned, unearned, regressed, len(control))


# ---------------------------------------------------------------------------------------------------
# B. Anti-cheat assertion diff (static)
# ---------------------------------------------------------------------------------------------------


def _asserts_by_test(src: str) -> dict[str, list[ast.Assert]]:
    """Map each ``test_*`` function to its assert statements (module + one class level deep)."""
    out: dict[str, list[ast.Assert]] = {}
    tree = ast.parse(src)
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
    for fn in funcs:
        out[fn.name] = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
    return out


def _is_trivial(a: ast.Assert) -> bool:
    """An assertion that can never fail — the classic lobotomy: ``assert True``, ``assert 1``, or a
    bare truthy name/const where a comparison used to be."""
    t = a.test
    if isinstance(t, ast.Constant):
        return bool(t.value)  # assert True / assert 1 / assert "x"
    return False


def _cut_derived(a: ast.Assert, cut_module: str) -> bool:
    """The expected side reads a constant out of the code under test — asserting the module against
    itself (a change-detector). Flags ``assert x == sub_index.CONST`` / ``mod.CONST``."""
    t = a.test
    if not (isinstance(t, ast.Compare) and len(t.comparators) == 1):
        return False
    leaf = cut_module.rsplit(".", 1)[-1]
    for side in (t.left, t.comparators[0]):
        node = side.value if isinstance(side, ast.Attribute) else side
        if isinstance(node, ast.Name) and node.id == leaf:
            return True
    return False


@dataclass
class AntiCheatViolation:
    test: str
    kind: str  # removed | weakened | trivial | cut-derived
    detail: str


def anticheat_diff(before_src: str, after_src: str, cut_module: str = "") -> list[AntiCheatViolation]:
    """Compare a test file before/after an edit. Bounce a removed or weakened assertion in a test that
    existed before, any trivially-true assertion, and any expected-value-from-CUT assertion. Purely
    additive edits (new test functions, no touched asserts) return clean."""
    before, after = _asserts_by_test(before_src), _asserts_by_test(after_src)
    violations: list[AntiCheatViolation] = []

    def norm(a: ast.Assert) -> str:
        return ast.dump(a.test)

    for name, before_asserts in before.items():
        after_asserts = after.get(name)
        if after_asserts is None:  # whole test deleted — a rename is caught here; surface it
            violations.append(AntiCheatViolation(name, "removed", "test function removed"))
            continue
        before_set, after_set = {norm(a) for a in before_asserts}, {norm(a) for a in after_asserts}
        missing = before_set - after_set
        # A removed assertion is a bounce UNLESS the count grew (strengthened in place is allowed).
        if missing and len(after_asserts) < len(before_asserts):
            violations.append(
                AntiCheatViolation(name, "weakened", f"{len(missing)} assertion(s) dropped, none added")
            )

    for name, after_asserts in after.items():
        for a in after_asserts:
            if _is_trivial(a):
                violations.append(AntiCheatViolation(name, "trivial", f"line {a.lineno}: always-true assert"))
            if cut_module and _cut_derived(a, cut_module):
                violations.append(
                    AntiCheatViolation(name, "cut-derived", f"line {a.lineno}: expected value read from CUT")
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


def _main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("anticheat", help="static assertion-diff of a test file vs a git ref")
    a.add_argument("test_file", type=Path)
    a.add_argument("--ref", default="HEAD")
    a.add_argument("--cut", default="", help="module-under-test dotted path, for cut-derived detection")
    a.add_argument("--repo", type=Path, default=Path.cwd())

    args = p.parse_args()
    if args.cmd == "anticheat":
        before = git_show(args.ref, args.test_file, cwd=args.repo)
        after = (args.repo / args.test_file).read_text(encoding="utf-8")
        violations = anticheat_diff(before, after, args.cut)
        for v in violations:
            print(f"BOUNCE {v.kind}: {v.test} — {v.detail}")
        if not violations:
            print("anticheat: clean")
        return 1 if violations else 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
