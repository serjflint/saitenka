"""Git-guarded cosmic-ray mutation campaign over the pure algorithmic core.

Opt-in (`uv run poe mutate [module …]`), never in `poe all` — each mutant reruns the suite, so it's
minutes per module. cosmic-ray mutates the target IN PLACE; the guard refuses a dirty tree and always
restores via `git checkout`, so an interrupted run can't leave a mutated source behind.

Scope is the pure core — parsers/rankers where a survivor is a real latent bug. Glue (controller/mpvio)
is excluded: I/O-bound, floods equivalent mutants. Survivors are a to-harden list, not a score to chase
(equivalent mutants make 100% unreachable) — feed them to Hypothesis properties and re-run to confirm.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

# module → the tightest suite that exercises it. Add a module here once it has focused tests.
TARGETS = {
    "sub_index": (
        "src/overlay/app/sub_index.py",
        "tests/test_sub_index.py tests/test_sub_index_properties.py",
    ),
    "fsrs": ("src/overlay/app/fsrs.py", "tests/test_fsrs.py"),
    "scoring": (
        "src/overlay/app/scoring.py",
        "tests/test_coloring.py tests/test_scoring_properties.py",
    ),
}


def campaign(name: str, module: str, tests: str) -> None:
    if subprocess.run(
        ["git", "status", "--porcelain", "--", module], text=True, capture_output=True
    ).stdout.strip():
        sys.exit(
            f"{module} has uncommitted changes — commit/stash first (mutation edits in place)."
        )
    db = os.path.join(tempfile.gettempdir(), module.replace("/", "_") + ".sqlite")
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as cfg:
        cfg.write(
            f'[cosmic-ray]\nmodule-path = "{module}"\ntimeout = 30.0\nexcluded-modules = []\n'
            f'test-command = "python -m pytest -x -q --no-header -p no:randomly {tests}"\n'
            f'[cosmic-ray.distributor]\nname = "local"\n'
        )
    try:
        if os.path.exists(db):
            os.remove(db)
        subprocess.run(["cosmic-ray", "init", cfg.name, db], check=True)
        subprocess.run(["cosmic-ray", "exec", cfg.name, db], check=True)
    finally:
        subprocess.run(["git", "checkout", "--", module])  # always restore the in-place mutation
    print(f"\n=== {name} ({module}) ===")
    subprocess.run(["cr-rate", db])  # newer cr-rate takes the session file as an arg, not stdin


def main() -> None:
    for name in sys.argv[1:] or list(TARGETS):
        if name not in TARGETS:
            sys.exit(f"unknown target {name!r}; known: {', '.join(TARGETS)}")
        campaign(name, *TARGETS[name])


if __name__ == "__main__":
    main()
