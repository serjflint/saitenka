"""Composite triage — point the Grow loop at the most under-tested valuable code (SPEC → *Triage*).

Where Sharpen SUMS its signals (any one can justify a re-audit of an existing test), Grow ranks the
PRODUCT of two axes — a target is worth growing only if it is BOTH valuable AND under-specified. High
value + fully-specified = leave it; under-specified + dead code = don't bother. Either axis at zero zeroes
the score, by design: either alone wastes effort.

  value/risk        fan-in (ruff-analyze import in-degree) · churn (git activity, a recency proxy — NOT
                    centrality; repowise get_risk is the documented, still-unwired centrality input).
  under-specified   missing-public-seam proxy (test-lint's private-attr metric count — behaviour only
                    assertable through a private) · survivors (un-killed mutants, if a campaign JSON is
                    supplied). The latter is optional so triage runs with no mutation campaign on hand;
                    a missing source prints "—", never a silent zero masquerading as "healthy".

The composite is transparent (every component is a column, never just a scalar). Module-level exclusions
are hard drops: a module any OPEN PR is editing (grow at rest, don't fight in-flight work). Per-GAP
exclusion (a `.ledger.grow.jsonl` gap already closed-current / unclosable) happens later, when the picked
module's scenario map is enumerated — not here. Run from the repository root:
    uv run python tools/grow_triage.py                       # ranked table
    uv run python tools/grow_triage.py --top 1               # just the pick
    uv run python tools/grow_triage.py --survivors-json s.json       # add mutation evidence
    uv run python tools/grow_triage.py --no-network          # offline: open-PR exclusion disabled (warned)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sharpen_ledger as sl
import sharpen_triage as st
from tool_json import repository_root, run_json


@dataclass
class Candidate:
    module: str
    tests: list[str]
    fan_in: int = 0
    churn: int = 0
    priv_seam: int = 0  # missing-public-seam proxy (private-attr metric hits)
    survivors: int | None = None  # None = no campaign supplied for this module
    untested: bool = False  # no mapped test file at all → maximally under-specified
    excluded: str = ""
    value: float = 0.0
    underspec: float = 0.0
    score: float = 0.0
    notes: list[str] = field(default_factory=list)


def fan_in_by_module(root: Path) -> dict[str, int]:
    """Import in-degree per module — how many other overlay modules import it (value/centrality proxy).
    From `ruff analyze graph` (a map: file → [files it imports]); we invert it to count dependents."""
    graph = run_json(["uv", "run", "ruff", "analyze", "graph", "src/saitenka"], root, dict)

    def key(path: str) -> str | None:
        p = Path(path).as_posix()
        marker = f"{sl.SRC}/"
        return p.split(marker, 1)[1] if marker in p else None

    indeg: dict[str, int] = {}
    for deps in graph.values():
        for dep in deps:
            mk = key(dep)
            if mk:
                indeg[mk] = indeg.get(mk, 0) + 1
    return indeg


def all_modules(root: Path) -> list[str]:
    """Every overlay source module key (relative to src/saitenka). Genuinely UNTESTED modules must be
    candidates too — they are the most under-specified code of all."""
    base = root / sl.SRC
    return sorted(str(p.relative_to(base)) for p in base.rglob("*.py"))


def score_candidates(cands: list[Candidate]) -> None:
    """Fill value / underspec / score IN PLACE for the non-excluded candidates. score = normalised
    value × normalised under-specification (the product — pure, so it is unit-tested directly). An UNTESTED
    module gets under-spec = 1.0 (maximal): with no tests, the private-attr seam proxy is 0 and would
    otherwise zero the score."""
    live = [c for c in cands if not c.excluded]
    nfan = st._norm([float(c.fan_in) for c in live])
    nchurn = st._norm([float(c.churn) for c in live])
    nseam = st._norm([float(c.priv_seam) for c in live])
    nsurv = st._norm([float(c.survivors or 0) for c in live])
    # Mutation evidence dominates when it exists for this module. Missing evidence remains unknown and
    # falls back to the weak seam proxy instead of masquerading as zero survivors.
    for c, fi, ch, seam, surv in zip(live, nfan, nchurn, nseam, nsurv, strict=True):
        c.value = 0.6 * fi + 0.4 * ch
        if c.untested:
            c.underspec = 1.0
        elif c.survivors is not None:
            c.underspec = 0.8 * surv + 0.2 * seam
        else:
            c.underspec = 0.5 * seam  # weak proxy only (warned) — dampened so untested still wins
        c.score = c.value * c.underspec


def rank(
    root: Path,
    *,
    survivors: dict[str, int] | None = None,
    check_network: bool = True,
) -> list[Candidate]:
    test_map = sl.map_tests_to_modules(root)
    conf = st.conformance_by_module(root, test_map)
    fan = fan_in_by_module(root)
    pr_paths = st.open_pr_paths(root) if check_network else set()

    # Include every source module so untested code remains rankable.
    universe = {module: test_map.get(module, []) for module in all_modules(root)}

    cands: list[Candidate] = []
    for module, tests in sorted(universe.items()):
        c = Candidate(module=module, tests=tests, untested=not tests)
        _total, _actionable, c.priv_seam = conf.get(module, (0, 0, 0))
        c.fan_in = fan.get(module, 0)
        c.churn, _age = st.churn_and_age(root, module, tests)
        c.survivors = None if survivors is None else survivors.get(module)
        touched = {f"{sl.SRC}/{module}", *tests} & pr_paths
        if touched:
            c.excluded = f"open-PR: {min(touched)}"
        cands.append(c)

    score_candidates(cands)
    cands.sort(key=lambda c: (c.excluded != "", -c.score, c.module))
    return cands


def _fmt(c: Candidate) -> str:
    surv = "—" if c.survivors is None else str(c.survivors)
    tag = f"  EXCLUDED[{c.excluded}]" if c.excluded else ("  TESTLESS" if c.untested else "")
    return (
        f"{c.score:5.2f}  val={c.value:.2f} uspec={c.underspec:.2f} | "
        f"fan={c.fan_in:<3} churn={c.churn:<2} seam={c.priv_seam:<2} surv={surv:<3} "
        f"{c.module}{tag}"
    )


def _load_json_map(path: str | None, root: Path) -> dict[str, int] | None:
    if not path:
        return None
    data = json.loads((root / path).read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=0, help="print only the top N live candidates")
    ap.add_argument("--no-network", action="store_true", help="skip gh (open-PR) checks")
    ap.add_argument(
        "--survivors-json", help="optional {module_key: survivor_count} from a mutate campaign"
    )
    args = ap.parse_args()
    root = repository_root(Path.cwd())
    if args.no_network:
        print(
            "WARNING: --no-network — open-PR exclusion DISABLED; may pick a module under active work.",
            file=sys.stderr,
        )
    if not args.survivors_json:
        print(
            "WARNING: no --survivors-json — for TESTED modules the under-spec axis is only "
            "the private-attr seam proxy (scales with test VOLUME, not adequacy), so their ranking is "
            "low-confidence. TESTLESS modules are ranked reliably. Supply a real signal before trusting the "
            "tested-module order.",
            file=sys.stderr,
        )
    cands = rank(
        root,
        survivors=_load_json_map(args.survivors_json, root),
        check_network=not args.no_network,
    )
    live = [c for c in cands if not c.excluded]
    for c in live[: args.top] if args.top else cands:
        print(_fmt(c))
    if live:
        top = live[0]
        print(
            f"\n→ pick: {top.module}  (score {top.score:.2f} = val {top.value:.2f} × uspec {top.underspec:.2f})"
        )


if __name__ == "__main__":
    main()
