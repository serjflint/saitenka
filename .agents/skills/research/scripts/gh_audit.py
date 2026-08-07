#!/usr/bin/env python3
"""GitHub activity-audit for the `research` skill — the insight layer above verify.py.

`verify.py` answers pass/fail (exists · maintained · release · link). This answers
*how healthy* each candidate is, as a triage table: stars, archived, commit
freshness, 90-day commit activity, latest release, and license — with a one-word
verdict per repo. Feed it the repos a research report names and it surfaces the
"archived", "abandoned", "niche", and "fabricated (404)" ones in one shot, so a
report's confident claim never outruns ground truth.

Usage:
    gh_audit.py owner/repo owner2/repo2 ...      # markdown table (default)
    gh_audit.py --json owner/repo ...            # machine-readable
    gh_audit.py < repos.txt                      # one owner/repo per line

Shells `gh` (GitHub API) — a real binary, never the PATH-shimmed grep/find
(AGENTS.md -> Tooling). Network tool; the smoke does not run it. Exit non-zero if
any repo is NOT FOUND (a 404 is a likely fabrication — the same gate as verify.py).
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys

ABANDONED_DAYS = 3 * 365  # ~3yr idle = abandoned (matches verify.py's calibrated bar)
STALE_DAYS = 365  # 1yr quiet = stale-but-alive; < that = maintained
NICHE_STARS = 10  # below this, flag as niche — NOT a disqualifier (a 6-star tool can be perfect)
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        return p.returncode, p.stdout.strip()
    except Exception as exc:  # noqa: BLE001 - a flaky net call must not crash the sweep
        return 1, f"error: {exc}"


def _gh_json(path: str) -> dict | list | None:
    code, out = _run(["gh", "api", path])
    if code != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        today = datetime.datetime.now(tz=datetime.UTC).date()
        return (today - datetime.date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def _commits_90d(repo: str) -> str:
    since = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=90)).date().isoformat()
    data = _gh_json(f"repos/{repo}/commits?since={since}T00:00:00Z&per_page=100")
    if not isinstance(data, list):
        return "?"
    return "100+" if len(data) >= 100 else str(len(data))


def audit(repo: str) -> dict:
    """One repo -> a dict of ground-truth fields + a one-word verdict."""
    meta = _gh_json(f"repos/{repo}")
    if not isinstance(meta, dict) or "full_name" not in meta:
        return {"repo": repo, "verdict": "FABRICATED", "note": "404 — no such repo (likely fabricated/renamed)"}

    pushed = meta.get("pushed_at", "")
    days = _days_since(pushed)
    stars = meta.get("stargazers_count", 0)
    archived = bool(meta.get("archived"))
    lic = (meta.get("license") or {}).get("spdx_id") or "none"

    rel = _gh_json(f"repos/{repo}/releases/latest")
    release = f"{rel['tag_name']} @ {rel['published_at'][:10]}" if isinstance(rel, dict) and rel.get("tag_name") else "—"

    if archived:
        verdict = "ARCHIVED"
    elif days is None:
        verdict = "UNKNOWN"
    elif days > ABANDONED_DAYS:
        verdict = f"ABANDONED(~{days // 365}yr)"
    elif days > STALE_DAYS:
        verdict = "STALE"
    else:
        verdict = "MAINTAINED"

    note = "niche" if stars < NICHE_STARS and verdict == "MAINTAINED" else ""
    return {
        "repo": meta["full_name"],
        "verdict": verdict,
        "stars": stars,
        "pushed": (pushed[:10] or "?"),
        "idle_days": days,
        "commits_90d": _commits_90d(repo),
        "release": release,
        "license": lic,
        "note": note,
    }


def _print_table(rows: list[dict]) -> None:
    cols = ("verdict", "repo", "stars", "pushed", "commits_90d", "release", "license", "note")
    header = ("Verdict", "Repo", "Stars", "Pushed", "90d", "Latest release", "License", "Note")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for r in rows:
        print("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    items = [a for a in argv if a != "--json"] or [ln.strip() for ln in sys.stdin if ln.strip()]
    repos = [i for i in items if _REPO.match(i)]
    if not repos:
        print("usage: gh_audit.py [--json] owner/repo ...", file=sys.stderr)
        return 2

    rows = [audit(r) for r in repos]
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        _print_table(rows)

    bad = sum(1 for r in rows if r["verdict"] == "FABRICATED")
    dead = sum(1 for r in rows if r["verdict"].startswith(("ARCHIVED", "ABANDONED")))
    if not as_json:
        print(f"\n{len(rows)} audited · {bad} fabricated (404) · {dead} archived/abandoned")
    return 1 if bad else 0  # fabrication is the only hard gate; archived/stale are advisory


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
