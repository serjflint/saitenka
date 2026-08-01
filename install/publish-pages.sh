#!/usr/bin/env bash
# Publish the install scripts to the `gh-pages` branch (served at https://serjflint.github.io/saitenka/).
# Single-source: the Pages `install.sh`/`install.ps1` are copies of `install/overlay-install.{sh,ps1}`.
# Re-run after changing either installer (and at each release) — via `uv run poe pages`. Uses a throwaway
# worktree so your working checkout is never touched. `--no-verify` skips the pre-commit hook (the empty
# gh-pages tree has no hook config).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WT="$(mktemp -d)"
cleanup() { git -C "$ROOT" worktree remove --force "$WT" 2>/dev/null || true; rm -rf "$WT"; }
trap cleanup EXIT

git -C "$ROOT" fetch --quiet origin gh-pages
git -C "$ROOT" worktree add --quiet "$WT" gh-pages
cp "$ROOT/install/overlay-install.sh" "$WT/install.sh"
cp "$ROOT/install/overlay-install.ps1" "$WT/install.ps1"

if git -C "$WT" diff --quiet; then
    echo "[pages] already up to date — nothing to publish"
    exit 0
fi
git -C "$WT" commit --no-verify --quiet -am "Sync install scripts to GitHub Pages"
git -C "$WT" push --quiet origin gh-pages
echo "[pages] published → https://serjflint.github.io/saitenka/"
