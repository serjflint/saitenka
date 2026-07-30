"""Impacted-test selector for the fast inner loop (`poe affected`).

Selects the tests a change can affect, so the edit→feedback cycle runs a subset instead of the full ~32s
`poe test`. It is NOT a gate — `poe all` / `poe test-ft` is the pre-push safety net, and *that* is what
guarantees correctness. This only speeds iteration: it soundly over-approximates the STATIC import edges,
and leaves the dynamic edges (fixtures, runtime imports) to the pre-push full run.

How it works:
  - `ruff analyze graph --direction dependents` gives a ONE-HOP `file → importers` map; we compute the
    reverse-transitive closure ourselves (BFS) — direct dependents alone would drop tests that reach a
    module through an intermediary.
  - A change touching a static-graph blind spot forces a FULL default-tier run: `conftest.py` (fixtures),
    image goldens, the `.lua` asset, a dynamic-import loader (`importlib`/`entry_points`), config/
    lockfiles, or the `deinflect` package overlay imports. A source module with no dependent test → FULL.
  - Residual (not chased here, backstopped by the pre-push full run + a periodic selected-vs-full failure
    audit): a change to a module `conftest` *imports* can shift a fixture used by tests outside the
    closure. Escalating on that would collapse almost every selection to full (conftest pulls in the
    god-objects), so it is deliberately left to `poe all`.

Runs pytest with `-p no:cacheprovider` so a subset run never overwrites `.pytest_cache` (which would
corrupt the selected-vs-full failure diff used to audit for escaped failures).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import deque
from pathlib import Path

OVERLAY = Path(__file__).resolve().parents[1]  # overlay/
ROOT = (
    OVERLAY.parent
)  # repo root; git paths are root-relative, ruff/pytest paths are overlay-relative
OV = "overlay/"

TIER = "not (slow or integration or requires_display or e2e)"  # mirror `poe test`'s fast universe
DYNAMIC_IMPORT = ("importlib.import_module", "__import__(", "entry_points", "pkgutil")


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def changed_files(base: str) -> list[str]:
    """Root-relative paths changed vs `base` (HEAD = working tree) plus untracked files."""
    files = set(_git("diff", "--name-only", base)) | set(
        _git("ls-files", "--others", "--exclude-standard")
    )
    return sorted(files)


def _is_dynamic(root_path: str) -> bool:
    """A changed overlay .py that does runtime import resolution — the graph can't see its edges."""
    try:
        src = (ROOT / root_path).read_text(encoding="utf-8")
    except OSError:
        return False
    return any(tok in src for tok in DYNAMIC_IMPORT)


def classify(changed: list[str]) -> tuple[list[str], set[str], set[str]]:
    """(full_run_reasons, overlay_py_relpaths, changed_test_relpaths). A non-empty first element means a
    static-graph blind spot was touched → run the full default tier."""
    full: list[str] = []
    overlay_py: set[str] = set()
    changed_tests: set[str] = set()
    for f in changed:
        name = f.rsplit("/", 1)[-1]
        if f.startswith("deinflect/") or name in ("pyproject.toml", "uv.lock"):
            full.append(f)  # code dep overlay imports, or a config/lock change
        elif not f.startswith(OV):
            continue  # docs / install / .github — irrelevant to the overlay suite
        elif name == "conftest.py" or f.startswith(OV + "tests/golden/") or f.endswith(".lua"):
            full.append(f)  # fixture injection / data goldens / runtime asset — no import edge
        elif f.endswith(".py"):
            if _is_dynamic(f):
                full.append(f)
            else:
                rel = f[len(OV) :]
                overlay_py.add(rel)
                if rel.startswith("tests/"):
                    changed_tests.add(rel)
    return full, overlay_py, changed_tests


def dependents_graph() -> dict[str, list[str]]:
    out = subprocess.run(
        ["ruff", "analyze", "graph", "--direction", "dependents"],
        cwd=OVERLAY,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)  # overlay-relative keys


def closure_tests(seeds: set[str], graph: dict[str, list[str]]) -> set[str]:
    """Reverse-transitive closure over the one-hop dependents map → the test files it reaches.
    `conftest.py` is excluded: it isn't a test, and because it imports the god-objects it sits in almost
    every module's closure — escalating on it would collapse every selection to a full run. A change to
    conftest *itself* is caught earlier (`classify` → full); fixture-behaviour shifts from a change to a
    module conftest *imports* are a known residual the pre-push full `poe all` backstops."""
    seen: set[str] = set()
    q = deque(seeds)
    while q:
        for dep in graph.get(q.popleft(), []):
            if dep not in seen:
                seen.add(dep)
                q.append(dep)
    return {
        f
        for f in seen
        if f.startswith("tests/") and f.endswith(".py") and not f.endswith("conftest.py")
    }


def select(base: str) -> tuple[list[str] | None, str]:
    """Return (test files to run, reason), or (None, reason) to mean 'run the full default tier'."""
    changed = changed_files(base)
    if not changed:
        return [], "no changes"
    full, overlay_py, changed_tests = classify(changed)
    if full:
        return None, f"full run — blind-spot change: {full[:3]}"
    src_changed = {f for f in overlay_py if f.startswith("src/")}
    tests = closure_tests(overlay_py, dependents_graph()) | changed_tests
    if src_changed and not tests:
        return None, f"full run — changed source has no dependent test: {sorted(src_changed)[:3]}"
    return sorted(tests), f"{len(tests)} impacted test file(s)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base",
        default="HEAD",
        help="git diff base (HEAD = working tree; or main / a merge-base ref)",
    )
    ap.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="print the selection, don't run pytest",
    )
    args = ap.parse_args()

    selected, reason = select(args.base)
    print(f"affected: {reason}", file=sys.stderr)
    if args.print_only:
        print("<FULL default tier>" if selected is None else "\n".join(selected))
        return 0
    if selected == []:
        return 0  # nothing impacted
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", TIER]
    if selected:
        cmd += selected
    return subprocess.run(cmd, cwd=OVERLAY, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
