"""Composite triage — point the sharpen loop at the right module (SPEC → *Triage*).

Ranks candidate modules by a transparent composite of always-cheap signals, and prints the components
(never just a scalar — signals localise work, they are not a score to maximise). Excludes what the loop
must not touch: sharpened-and-unchanged modules, grow-filed gaps with an open issue, and any module a
currently-open PR is editing (sharpen at rest, don't fight in-flight work).

Signals per module:
  - survival (Efficacy)  recorded non-equiv mutation survival from the ledger — the strongest "still has
                         sharpen work" signal; a half-done Efficacy module ranks HIGH, not last (else —).
  - actionable           per-hit-fixable `poe test-lint` violations (actionable rules, not the metric ones)
                         — the Sharpen-relevant conformance term.
  - conformance          total test-lint hits (metric + actionable); a high metric count is a Grow/
                         architecture signal, shown as a column, NOT the Sharpen ranker.
  - churn / age          git commits touching module+tests over a window (a recency/activity proxy — NOT
                         centrality: repowise get_risk is the documented, still-unwired centrality input).
  - ledger status        unseen / stale-sha / stale-toolset / in-progress / sharpened-current / dry-run.

Composite (higher = sharpen sooner) = 0.4·survival + 0.4·actionable + 0.2·churn + freshness-bonus
(unseen/stale). Efficacy and actionable-conformance are co-primary; the metric count is a separate
column. Exclusions (sharpened-and-unchanged, grow-filed, open-PR) are hard drops; grow-filed and
in-progress work offline (fail-closed), open-PR needs `gh`. Run from `overlay/`:
    uv run python tools/sharpen_triage.py            # ranked table
    uv run python tools/sharpen_triage.py --top 1    # just the pick
    uv run python tools/sharpen_triage.py --no-network   # offline: open-PR exclusion disabled (warned)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sharpen_ledger as sl

# Actionable rules yield per-hit fixes; metric rules are per-file coupling counts (rank, don't enumerate).
METRIC_RULES = {"test-assert-private-attr", "test-monkeypatch-private-target"}

CHURN_WINDOW = "90 days ago"


@dataclass
class Candidate:
    module: str
    tests: list[str]
    conformance: int = 0
    actionable: int = 0
    churn: int = 0
    age_days: int | None = None
    survival: float | None = None
    status: str = sl.UNSEEN
    excluded: str = ""  # non-empty → dropped, with the reason
    score: float = 0.0
    notes: list[str] = field(default_factory=list)


def _run(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False).stdout


def conformance_by_module(root: Path, test_map: dict[str, list[str]]) -> dict[str, tuple[int, int]]:
    """module -> (total hits, actionable hits) from a single `test-lint --json` scan."""
    raw = _run(
        ["uv", "run", "ast-grep", "scan", "-c", "sgconfig-tests.yml", "--json=compact", "tests"],
        root,
    )
    hits = json.loads(raw) if raw.strip() else []
    file_to_module = {t: m for m, ts in test_map.items() for t in ts}
    out: dict[str, list[int]] = {m: [0, 0] for m in test_map}
    for h in hits:
        m = file_to_module.get(h["file"])
        if m is None:
            continue
        out[m][0] += 1
        if h["ruleId"] not in METRIC_RULES:
            out[m][1] += 1
    return {m: (t, a) for m, (t, a) in out.items()}


def churn_and_age(root: Path, module: str, tests: list[str]) -> tuple[int, int | None]:
    paths = [f"{sl.SRC}/{module}", *tests]
    log = _run(["git", "log", f"--since={CHURN_WINDOW}", "--format=%H", "--", *paths], root)
    churn = len([ln for ln in log.splitlines() if ln.strip()])
    last = _run(["git", "log", "-1", "--format=%ct", "--", *paths], root).strip()
    if not last:
        return churn, None
    now = _run(
        ["git", "log", "-1", "--format=%ct"], root
    ).strip()  # HEAD time; no wall clock in-script
    age = (int(now) - int(last)) // 86400 if now else None
    return churn, age


def open_pr_paths(root: Path) -> set[str]:
    """Files any OPEN PR is editing — modules under active work are excluded from triage."""
    raw = _run(["gh", "pr", "list", "--state", "open", "--json", "files"], root.parent)
    try:
        prs = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError:
        return set()
    return {f["path"] for pr in prs for f in pr.get("files", [])}


def survival_from_ledger(ledger: sl.Ledger, module: str) -> float | None:
    rec = ledger.latest(module)
    if rec and isinstance(rec.get("axes", {}).get("survival"), dict):
        return rec["axes"]["survival"].get("after")
    return None


def open_issue(root: Path, ref: str) -> bool:
    num = ref.lstrip("#").split("#")[-1].split()[0].strip("#")
    if not num.isdigit():
        return False
    out = _run(["gh", "issue", "view", num, "--json", "state"], root.parent)
    try:
        return json.loads(out).get("state") == "OPEN" if out.strip() else False
    except json.JSONDecodeError:
        return False


def _norm(vals: list[float]) -> list[float]:
    hi = max(vals) if vals else 0
    return [v / hi if hi else 0.0 for v in vals]


def rank(root: Path, ledger_path: Path, *, check_network: bool = True) -> list[Candidate]:
    ledger = sl.Ledger.load(ledger_path)
    test_map = sl.map_tests_to_modules(root)
    conf = conformance_by_module(root, test_map)
    grow = ledger.grow_filed()
    pr_paths = open_pr_paths(root) if check_network else set()

    cands: list[Candidate] = []
    for module, tests in sorted(test_map.items()):
        c = Candidate(module=module, tests=tests)
        c.conformance, c.actionable = conf.get(module, (0, 0))
        c.churn, c.age_days = churn_and_age(root, module, tests)
        c.status = ledger.status(module, root, tests)
        c.survival = survival_from_ledger(ledger, module)
        # exclusions (hard drops). Grow-filed and healed-state work offline (ledger-backed);
        # grow-filed is FAIL-CLOSED — excluded unless we can positively confirm every issue closed.
        touched = {f"{sl.SRC}/{module}", *tests} & pr_paths
        if touched:
            c.excluded = f"open-PR: {min(touched)}"
        elif c.status == sl.SHARPENED_CURRENT:
            c.excluded = "sharpened & unchanged"
        elif module in grow and (
            not check_network or any(open_issue(root, i) for i in grow[module])
        ):
            c.excluded = f"grow-filed{'' if check_network else ' (offline, assumed open)'} ({','.join(grow[module])})"
        cands.append(c)

    live = [c for c in cands if not c.excluded]
    # Four-axis composite (SPEC → Triage): Efficacy (survival) and actionable-conformance are co-primary
    # Sharpen signals; churn is a recency/activity proxy (NOT centrality — repowise get_risk is the
    # documented, still-unwired centrality/risk input); an unseen/stale module gets a freshness bonus.
    nact = _norm([float(c.actionable) for c in live])
    nsurv = _norm([c.survival or 0.0 for c in live])
    nchurn = _norm([float(c.churn) for c in live])
    for c, a, s, ch in zip(live, nact, nsurv, nchurn, strict=True):
        bonus = 0.2 if c.status in {sl.UNSEEN, sl.STALE_SHA, sl.STALE_TOOLSET} else 0.0
        c.score = 0.4 * a + 0.4 * s + 0.2 * ch + bonus
    cands.sort(key=lambda c: (c.excluded != "", -c.score, c.module))
    return cands


def _fmt(c: Candidate) -> str:
    surv = f"{c.survival:.2f}" if c.survival is not None else "—"
    age = f"{c.age_days}d" if c.age_days is not None else "—"
    tag = f"  EXCLUDED[{c.excluded}]" if c.excluded else ""
    return (
        f"{c.score:5.2f}  conf={c.conformance:<3} act={c.actionable:<3} churn={c.churn:<2} "
        f"age={age:<5} surv={surv:<5} {c.status:<14} {c.module}{tag}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=0, help="print only the top N live candidates")
    ap.add_argument(
        "--no-network", action="store_true", help="skip gh (open-PR / grow-issue) checks"
    )
    ap.add_argument("--ledger", default="../.ledger.sharpen.jsonl")
    args = ap.parse_args()
    root = Path.cwd()
    if args.no_network:
        print(
            "WARNING: --no-network — open-PR exclusion is DISABLED; may pick a module under active "
            "work. Grow-filed exclusion stays on (fail-closed).",
            file=sys.stderr,
        )
    cands = rank(root, (root / args.ledger).resolve(), check_network=not args.no_network)
    live = [c for c in cands if not c.excluded]
    shown = live[: args.top] if args.top else cands
    for c in shown:
        print(_fmt(c))
    if live:
        print(f"\n→ pick: {live[0].module}  (score {live[0].score:.2f}, {live[0].status})")


if __name__ == "__main__":
    main()
