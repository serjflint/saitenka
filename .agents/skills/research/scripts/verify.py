#!/usr/bin/env python3
"""Grounded-verification helper for the `research` skill.

Given the repos / URLs a research report names, ground each against reality
instead of trusting the report: existence (a NOT-FOUND is a likely fabrication),
maintenance (abandoned = ~3 years idle, the calibrated bar), and — for GitHub —
the latest release. This is the mechanical citation / atomic verification step
(ALCE / FActScore) made concrete: run it before folding a claim in.

Usage:
    verify.py owner/repo  owner2/repo2  https://example.com/page
    verify.py < list.txt            # one repo or URL per line

Shells `gh` (GitHub API) and `curl` — real binaries, never the PATH-shimmed
grep/find (AGENTS.md -> Tooling). Network tool; the smoke does not run it.
Exit non-zero if any item is NOT FOUND or a link is broken.
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys

ABANDONED_DAYS = 3 * 365  # ~3 years idle = abandoned; a year quiet is still maintained
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        return p.returncode, p.stdout.strip()
    except Exception as exc:  # noqa: BLE001 - a flaky net call must not crash the sweep
        return 1, f"error: {exc}"


def _days_since(iso: str) -> int | None:
    try:
        today = datetime.datetime.now(tz=datetime.UTC).date()
        return (today - datetime.date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def check_repo(repo: str) -> tuple[str, str]:
    """Return (verdict, detail) for a GitHub owner/repo."""
    code, full = _run(["gh", "api", f"repos/{repo}", "--jq", ".full_name"])
    if code != 0 or not full:
        return "NOT-FOUND", "no such repo — likely fabricated or renamed"
    _, pushed = _run(["gh", "api", f"repos/{repo}", "--jq", ".pushed_at"])
    _, stars = _run(["gh", "api", f"repos/{repo}", "--jq", ".stargazers_count"])
    _, archived = _run(["gh", "api", f"repos/{repo}", "--jq", ".archived"])
    _, rel = _run(
        ["gh", "api", f"repos/{repo}/releases/latest", "--jq",
         '.tag_name + " @ " + (.published_at|.[0:10])']
    )
    rel = rel if rel and "message" not in rel.lower() else "(no release)"
    days = _days_since(pushed)
    if archived == "true":
        verdict = "ARCHIVED"
    elif days is not None and days > ABANDONED_DAYS:
        verdict = f"ABANDONED (~{days // 365}yr idle)"
    else:
        verdict = "active"
    return verdict, f"push {pushed[:10]}  stars {stars}  latest {rel}"


def check_url(url: str) -> tuple[str, str]:
    """Return (verdict, detail) for a non-GitHub URL — does the link resolve?"""
    code, status = _run(["curl", "-sIL", "-o", "/dev/null", "-w", "%{http_code}", url])
    if code != 0:
        return "UNREACHABLE", status
    return ("resolves" if status[:1] in {"2", "3"} else "BROKEN-LINK"), f"HTTP {status}"


def main(argv: list[str]) -> int:
    items = argv or [line.strip() for line in sys.stdin if line.strip()]
    if not items:
        print("usage: verify.py owner/repo ... | https://url ...", file=sys.stderr)
        return 2
    bad = 0
    for item in items:
        if item.startswith(("http://", "https://")):
            verdict, detail = check_url(item)
        elif _REPO.match(item):
            verdict, detail = check_repo(item)
        else:
            verdict, detail = "SKIPPED", "not an owner/repo or URL"
        if verdict in {"NOT-FOUND", "BROKEN-LINK", "UNREACHABLE"}:
            bad += 1
        print(f"[{verdict:>16}] {item:<40} {detail}")
    print(f"\n{len(items)} checked, {bad} unverified/fabricated")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
