#!/usr/bin/env python3
"""Automate the two halves of RELEASING.md: ``prepare`` (on the release branch, before merge) and
``publish`` (on ``main``, after merge). Mirrors the manual procedure exactly — this script only
removes the typing, not the review gates: changelog content is always supplied by a human (never
generated unattended), and publishing a GitHub Release still requires an explicit ``--publish``.

Write the curated changelog entry to ``RELEASE_NOTES.md`` (repo root, gitignored scratch file — its
content is folded into ``CHANGELOG.md``, not committed itself) before running ``prepare``:

    $EDITOR RELEASE_NOTES.md
    uv run install/release.py prepare [--bump auto|patch|minor|major] [--push]
    uv run install/release.py publish [--publish]

``--notes PATH`` overrides the default ``RELEASE_NOTES.md`` location. Run from anywhere; paths
resolve from ``__file__``.

Distribution is **PyPI** (`uv tool install "saitenka[full]"`) plus the hosted install scripts
(`serjflint.github.io/saitenka`, see `poe pages`); a release is a git tag + a notes-only GitHub
Release. This script builds and smoke-tests the wheel but does NOT upload it — publish the built wheel
to PyPI yourself with ``cd overlay && uv publish`` (needs your token; irreversible).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OVERLAY_DIR = REPO_ROOT / "overlay"
PYPROJECT = OVERLAY_DIR / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
DEFAULT_NOTES = REPO_ROOT / "RELEASE_NOTES.md"

VERSION_RE = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)
# Mirrors cliff.toml's commit_parsers: what decides Added vs Fixed there also decides the bump here.
BREAKING_RE = re.compile(r"^\w+(\([^)]*\))?!:|BREAKING CHANGE:", re.MULTILINE)
FEAT_RE = re.compile(r"^feat(\([^)]*\))?:", re.MULTILINE)


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True, capture: bool = False):
    print(f"$ {' '.join(cmd)}" + (f"   (cwd={cwd})" if cwd else ""))
    return subprocess.run(
        cmd, cwd=cwd, check=check, text=True, capture_output=capture
    )  # noqa: S603 — list args, no shell


# --- version -----------------------------------------------------------------------------------


def read_version() -> str:
    m = VERSION_RE.search(PYPROJECT.read_text())
    if not m:
        raise RuntimeError(f"couldn't find a `version = \"X.Y.Z\"` line in {PYPROJECT}")
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"


def write_version(new_version: str) -> None:
    text = PYPROJECT.read_text()
    new_text, n = VERSION_RE.subn(f'version = "{new_version}"', text, count=1)
    if n != 1:
        raise RuntimeError(f"expected exactly one version line in {PYPROJECT}, patched {n}")
    PYPROJECT.write_text(new_text)


def last_tag() -> str | None:
    r = run(["git", "describe", "--tags", "--abbrev=0"], cwd=REPO_ROOT, check=False, capture=True)
    return r.stdout.strip() if r.returncode == 0 else None


def detect_bump(since: str | None) -> str:
    """patch/minor/major from Conventional Commits since ``since`` (or all history if None) —
    breaking→major, feat→minor, everything else→patch. Same signal cliff.toml's commit_parsers use
    to sort commits into Added/Fixed/etc., just reduced to a single verdict."""
    range_spec = f"{since}..HEAD" if since else "HEAD"
    log = run(
        ["git", "log", range_spec, "--format=%s%n%b%n---"], cwd=REPO_ROOT, capture=True
    ).stdout
    if BREAKING_RE.search(log):
        return "major"
    if FEAT_RE.search(log):
        return "minor"
    return "patch"


def bump_version(current: str, level: str, *, allow_major: bool) -> str:
    major, minor, patch = (int(p) for p in current.split("."))
    if level == "major":
        if major == 0 and not allow_major:
            # Pre-1.0 policy (RELEASING.md): minor IS the effective major. A detected breaking
            # change still only bumps the minor unless the human explicitly opts into 1.0.0.
            print("breaking change detected, but pre-1.0 → bumping minor, not major "
                  "(pass --allow-major to cut 1.0.0 instead)")
            return f"{major}.{minor + 1}.0"
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump level: {level!r}")


# --- changelog -----------------------------------------------------------------------------------


def insert_changelog_section(notes: str, version: str) -> None:
    """Replace the (possibly empty) `## [Unreleased]` section with the curated ``notes`` under a new
    `## [version] - date` heading, and open a fresh empty Unreleased above it — the same edit made
    by hand every release so far."""
    text = CHANGELOG.read_text()
    marker = "## [Unreleased]"
    if marker not in text:
        raise RuntimeError(f"no `{marker}` heading found in {CHANGELOG}")
    head, _, rest = text.partition(marker)
    # Drop whatever was directly under the old Unreleased heading (up to the next `## [`) — it's
    # either empty already or content this release's `notes` supersedes.
    _, _, tail = rest.partition("\n## [")
    today = date.today().isoformat()
    new_section = f"{marker}\n\n## [{version}] - {today}\n\n{notes.strip()}\n\n## [{tail}"
    CHANGELOG.write_text(head + new_section)


def changelog_section(version: str) -> str:
    """Extract the already-released `## [version] - ...` section (for release notes)."""
    text = CHANGELOG.read_text()
    m = re.search(
        rf"(## \[{re.escape(version)}\][^\n]*\n.*?)(?=\n## \[|\Z)", text, re.DOTALL
    )
    if not m:
        raise RuntimeError(f"no changelog section found for {version} in {CHANGELOG}")
    return m.group(1).strip() + "\n"


# --- build / smoke test ----------------------------------------------------------------------


def build_wheel(version: str) -> Path:
    """Build the wheel (the artifact `uv publish` uploads to PyPI) and return its path."""
    dist = OVERLAY_DIR / "dist"
    for old in dist.glob("saitenka-*.whl"):
        old.unlink()
    run(["uv", "build", "--wheel"], cwd=OVERLAY_DIR)
    wheels = list(dist.glob(f"saitenka-{version}-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one saitenka-{version} wheel in {dist}, found {wheels}")
    return wheels[0]


def smoke_test(wheel: Path, expected_version: str) -> None:
    """Install the built wheel in an isolated env and confirm the version — catches packaging breakage
    (entry point, data files, deps) that `poe all` can't."""
    out = run(["uvx", "--from", str(wheel), "saitenka", "--version"], capture=True).stdout.strip()
    if out != expected_version:
        raise RuntimeError(f"smoke test: --version printed {out!r}, expected {expected_version!r}")
    print(f"smoke test OK: isolated wheel install reports {out}")


def build_and_smoke_test(version: str) -> Path:
    wheel = build_wheel(version)
    smoke_test(wheel, version)
    return wheel


# --- git / gh --------------------------------------------------------------------------------


def require_clean_tree() -> None:
    status = run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture=True).stdout
    if status.strip():
        raise RuntimeError(
            "working tree isn't clean — commit/stash first:\n" + status
        )


def current_branch() -> str:
    return run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT, capture=True
    ).stdout.strip()


@dataclass
class PrepareArgs:
    notes: Path
    bump: str
    version: str | None
    allow_major: bool
    push: bool
    skip_gate: bool
    dry_run: bool


def cmd_prepare(args: PrepareArgs) -> int:
    if not args.notes.exists() or not args.notes.read_text().strip():
        raise RuntimeError(
            f"{args.notes} is missing or empty — write the curated changelog entry there first "
            "(bullet points, no heading; `poe changelog` drafts raw material to review against)."
        )
    if not args.dry_run:
        require_clean_tree()
    current = read_version()
    if args.version:
        next_version = args.version
    else:
        level = args.bump if args.bump != "auto" else detect_bump(last_tag())
        next_version = bump_version(current, level, allow_major=args.allow_major)
    print(f"{current} -> {next_version}")
    notes_text = args.notes.read_text()

    if args.dry_run:
        print("--- would insert into CHANGELOG.md ---")
        print(f"## [{next_version}] - {date.today().isoformat()}\n\n{notes_text.strip()}")
        return 0

    write_version(next_version)
    run(["uv", "lock"], cwd=OVERLAY_DIR)  # refresh uv.lock's own pinned version to match
    insert_changelog_section(notes_text, next_version)

    if not args.skip_gate:
        run(["uv", "run", "poe", "pre-release"], cwd=OVERLAY_DIR)

    build_and_smoke_test(next_version)

    run(["git", "add", "pyproject.toml", "uv.lock"], cwd=OVERLAY_DIR)
    run(["git", "add", "CHANGELOG.md"], cwd=REPO_ROOT)
    run(["git", "commit", "-m", f"chore(overlay): release {next_version}"], cwd=REPO_ROOT)

    if args.push:
        run(["git", "push"], cwd=REPO_ROOT)

    branch = current_branch()
    print(f"\nReady: {next_version} committed on `{branch}`.")
    if not args.push:
        print(f"  git push   # then open/update the PR")
    print("Next: get the PR reviewed and merged into main, then run `release.py publish`.")
    return 0


@dataclass
class PublishArgs:
    version: str | None
    publish: bool
    skip_gate: bool
    require_main: bool


def cmd_publish(args: PublishArgs) -> int:
    if args.require_main:
        run(["git", "fetch", "origin", "main"], cwd=REPO_ROOT)
        branch = current_branch()
        if branch != "main":
            raise RuntimeError(f"on `{branch}`, not `main` — checkout main first (or pass --no-require-main)")
        local = run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture=True).stdout.strip()
        remote = run(["git", "rev-parse", "origin/main"], cwd=REPO_ROOT, capture=True).stdout.strip()
        if local != remote:
            raise RuntimeError("local main isn't up to date with origin/main — `git pull` first")

    version = args.version or read_version()
    tag = f"v{version}"
    if run(["git", "rev-parse", tag], cwd=REPO_ROOT, check=False, capture=True).returncode == 0:
        raise RuntimeError(f"tag {tag} already exists")

    if not args.skip_gate:
        run(["uv", "run", "poe", "pre-release"], cwd=OVERLAY_DIR)

    wheel = build_and_smoke_test(version)

    run(["git", "tag", "-a", tag, "-m", f"Release {version}"], cwd=REPO_ROOT)
    run(["git", "push", "origin", tag], cwd=REPO_ROOT)

    notes = changelog_section(version)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(notes)
        notes_path = Path(f.name)
    # Notes-only GitHub Release — no binary assets. Distribution is PyPI + the hosted install scripts;
    # the auto "Source code" archive covers source. `uv publish` (below) is the load-bearing step.
    run(
        [
            "gh", "release", "create", tag,
            "--draft", "--title", version,
            "--notes-file", str(notes_path),
        ],
        cwd=REPO_ROOT,
    )

    url = run(
        ["gh", "release", "view", tag, "--json", "url", "-q", ".url"], cwd=REPO_ROOT, capture=True
    ).stdout.strip()
    if args.publish:
        run(["gh", "release", "edit", tag, "--draft=false"], cwd=REPO_ROOT)
        print(f"\nPublished: {url}")
    else:
        print(f"\nDraft ready — review it, then publish:\n  gh release edit {tag} --draft=false\n  {url}")
    # PyPI is the install channel — this is what makes `uv tool install`/`curl … | sh` see the new
    # version. Manual on purpose (needs your token, irreversible):
    print(f"\nUpload the wheel to PyPI:\n  (cd overlay && uv publish)   # built: {wheel.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="bump version, insert changelog, build+gate+commit (pre-merge)")
    p.add_argument(
        "--notes", type=Path, default=DEFAULT_NOTES,
        help=f"markdown file with the curated changelog entry (default: {DEFAULT_NOTES.name})",
    )
    p.add_argument("--bump", choices=["auto", "patch", "minor", "major"], default="auto")
    p.add_argument("--version", help="exact X.Y.Z override (skips --bump)")
    p.add_argument("--allow-major", action="store_true", help="let a detected breaking change cross to 1.0.0")
    p.add_argument("--push", action="store_true", help="also `git push` after committing")
    p.add_argument("--skip-gate", action="store_true", help="skip `poe pre-release` (faster iteration only)")
    p.add_argument("--dry-run", action="store_true", help="print the computed version + notes, change nothing")

    q = sub.add_parser("publish", help="tag, build, draft-release, optionally publish (post-merge)")
    q.add_argument("--version", help="defaults to overlay/pyproject.toml's current version")
    q.add_argument("--publish", action="store_true", help="flip the draft live (default: leave as draft)")
    q.add_argument("--skip-gate", action="store_true", help="skip `poe pre-release`")
    q.add_argument(
        "--no-require-main", dest="require_main", action="store_false",
        help="allow running off a branch other than main (rare; e.g. re-running after a failed step)",
    )

    ns = ap.parse_args()
    if ns.command == "prepare":
        return cmd_prepare(PrepareArgs(
            notes=ns.notes, bump=ns.bump, version=ns.version, allow_major=ns.allow_major,
            push=ns.push, skip_gate=ns.skip_gate, dry_run=ns.dry_run,
        ))
    return cmd_publish(PublishArgs(
        version=ns.version, publish=ns.publish, skip_gate=ns.skip_gate, require_main=ns.require_main,
    ))


if __name__ == "__main__":
    sys.exit(main())
