"""Git-guarded cosmic-ray mutation campaign over the pure algorithmic core.

Opt-in (`uv run poe mutate [module …]`), never in `poe all` — each mutant reruns the suite, so it's
minutes per module. cosmic-ray mutates the target IN PLACE; the guard refuses a dirty tree and always
restores via `git checkout`, so an interrupted run can't leave a mutated source behind.

`TARGETS` below is the **canonical pure-core mutation allowlist** — the single source of truth for
"which modules are mutation-audited" (AGENTS.md points here rather than re-listing; the Sharpen harness
gates its Efficacy axis on `--list`). Maintenance is a *deliberate, human-gated* step:

  ADD a module only when all three hold — (1) it is pure/algorithmic: no subprocess/socket/display/fs/
  wall-clock (the same predicate the `test-mislevelled-realio` lint encodes); (2) it has focused unit +
  property tests; (3) a human ran an initial `poe mutate <m>` and triaged the survivors to Hypothesis.
  REMOVE a module if it grows I/O and becomes glue (glue floods equivalent mutants — controller/mpvio
  are excluded for exactly this). Never add a module just to make a tool run against it.

Survivors are a to-harden list, not a score to chase (equivalent mutants make 100% unreachable) — feed
them to Hypothesis properties and re-run to confirm.

The session DB is content-addressed by module path under $TMPDIR, so a completed campaign is REUSED on
the next run (resumable; pass `--force` to rebuild). A tool that only needs to *read* survivors — the
Sharpen gate — consumes that DB directly and never launches a campaign (it can't: a campaign outlives a
10-minute step budget). Mutation is a slow pre-req produced out-of-band, never inline in a fast loop.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

# Persistent, portable campaign cache — a completed campaign is expensive, so it lives in the repo (not
# $TMPDIR, which is volatile and would force a rebuild every reboot). Gitignored. Each worktree gets its
# own; build the campaign in the checkout you'll consume it from (the Sharpen harness defers Efficacy if
# the DB is absent rather than launching a minutes-long campaign inline).
CACHE_DIR = Path(__file__).resolve().parents[2] / ".mutation-cache"

# module → the tightest suite that exercises it. THE canonical allowlist — see the module docstring for
# the add/remove procedure. Keep it pure-core only (glue floods equivalent mutants).
TARGETS = {
    "subtitle_index": (
        "src/saitenka/subtitles/index.py",
        "tests/test_sub_index.py tests/test_sub_index_properties.py",
    ),
    "subtitle_parsers": (
        "src/saitenka/subtitles/parsers.py",
        "tests/test_sub_index.py tests/test_sub_index_properties.py tests/test_subtitle_metamorphic.py",
    ),
    "fsrs": (
        "saitenka-wordstate/src/saitenka_wordstate/fsrs.py",
        "tests/test_fsrs.py tests/test_fsrs_properties.py",
    ),
    # The classification moved into saitenka-wordstate; what is left in app/scoring.py is the palette,
    # whose every literal is already pinned concretely by test_palette_literals_are_concrete.
    "scoring": (
        "saitenka-wordstate/src/saitenka_wordstate/scorer.py",
        "tests/test_coloring.py tests/test_scoring_properties.py",
    ),
    "verdict": (
        "saitenka-wordstate/src/saitenka_wordstate/verdict.py",
        "tests/test_coloring.py tests/test_scoring_properties.py",
    ),
    "window": ("src/saitenka/render/window.py", "tests/test_window_geometry.py"),
}


def db_path(module: str) -> str:
    """The content-addressed session DB for a module — stable across runs so a campaign is reusable."""
    CACHE_DIR.mkdir(exist_ok=True)
    return str(CACHE_DIR / (module.replace("/", "_") + ".sqlite"))


def _pending(db: str) -> bool:
    """True if the DB has un-executed work (a partial campaign to resume). Missing DB ⇒ nothing done."""
    out = subprocess.run(["cosmic-ray", "dump", db], capture_output=True, text=True).stdout
    # a completed job line carries a "test_outcome"; a pending one does not.
    return any(ln.strip() and "test_outcome" not in ln for ln in out.splitlines())


def campaign(name: str, module: str, tests: str, *, force: bool = False) -> None:
    db = db_path(module)
    if Path(db).exists() and not force and not _pending(db):
        print(f"\n=== {name} ({module}) — reusing complete campaign at {db} ===")
        subprocess.run(["cr-rate", db])
        return
    if subprocess.run(
        ["git", "status", "--porcelain", "--", module], text=True, capture_output=True
    ).stdout.strip():
        sys.exit(
            f"{module} has uncommitted changes — commit/stash first (mutation edits in place)."
        )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".toml", delete=False) as cfg:
        cfg.write(
            f'[cosmic-ray]\nmodule-path = "{module}"\ntimeout = 30.0\nexcluded-modules = []\n'
            f'test-command = "python -m pytest -x -q --no-header -p no:randomly {tests}"\n'
            f'[cosmic-ray.distributor]\nname = "local"\n'
        )
    try:
        if force and Path(db).exists():
            Path(db).unlink()
        if not Path(db).exists():
            subprocess.run(["cosmic-ray", "init", cfg.name, db], check=True)
        subprocess.run(
            ["cosmic-ray", "exec", cfg.name, db], check=True
        )  # resumes pending; no-op if done
    finally:
        subprocess.run(["git", "checkout", "--", module])  # always restore the in-place mutation
    print(f"\n=== {name} ({module}) ===")
    subprocess.run(["cr-rate", db])  # newer cr-rate takes the session file as an arg, not stdin


def main() -> None:
    args = sys.argv[1:]
    if "--list" in args:  # the machine-readable allowlist (the harness reads this to gate Efficacy)
        print("\n".join(TARGETS))
        return
    force = "--force" in args
    names = [a for a in args if not a.startswith("--")]
    for name in names or list(TARGETS):
        if name not in TARGETS:
            sys.exit(f"unknown target {name!r}; known: {', '.join(TARGETS)}")
        campaign(name, *TARGETS[name], force=force)


if __name__ == "__main__":
    main()
