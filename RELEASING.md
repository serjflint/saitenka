# Releasing Saitenka

Distribution is **PyPI** (`uv tool install "saitenka[full]"`) plus the hosted install scripts
(`serjflint.github.io/saitenka`, kept in sync by `poe pages`); a release is a **git tag + a GitHub
Release**. The `vX.Y.Z` tag push is the **sole release trigger**: the `.github/workflows/release.yml`
CI builds once and publishes **both** the GitHub Release **and** PyPI (Trusted Publishing / OIDC — no
token). Everything up to and including the tag is prepared locally; `release.yml` owns the publish half.

This file is the source of truth for the human steps — `install/release.py` automates the local half
(bump, changelog, gate, build, commit), but the review gates it can't remove — curating the changelog
and merging the PR — still happen exactly where they're described here.

> **Do NOT run `release.py publish` / `poe release-publish` or `uv publish`.** `release.yml` supersedes
> both: it creates the Release (running `release-publish` too double-creates it) and uploads to PyPI
> (tokenless OIDC — a manual `uv publish` is a redundant, token-needing second upload). They remain in
> `release.py` only for a CI-down manual fallback.

## Versioning

[SemVer](https://semver.org). Pre-1.0, the **minor is the effective major**: any feature batch (or a
breaking change) is a minor bump — `0.2.0 → 0.3.0`. Don't reach for `1.0.0` until the CLI/config is
being frozen.

Single source of truth: **`overlay/pyproject.toml` `version`** (read at runtime via
`importlib.metadata`). Nothing else hardcodes the version.

## Automated

`install/release.py prepare` (via `poe release-prepare`) runs the local steps 1-5 below. It requires a
human at two points: the changelog entry (write it to `RELEASE_NOTES.md` first — never generated
unattended) and the PR merge. After merge, the tag push is the whole publish — `release.yml` takes it
from there.

```sh
$EDITOR RELEASE_NOTES.md              # curate the changelog entry (bullets, no heading). Nothing to
                                        # write yet? `## [Unreleased]` in CHANGELOG.md already IS the
                                        # curated entry — lift its body verbatim (no heading).
uv run poe release-prepare             # steps 1-5: bump version (auto patch/minor/major from
                                        # Conventional Commits, capped at minor pre-1.0), insert
                                        # the changelog section, `poe pre-release`, build+smoke-test the
                                        # wheel, commit `chore(overlay): release X.Y.Z`
git push && gh pr create               # step 6: open the PR, get it reviewed, merge it

# step 7 — after merge: tag the MERGED commit by SHA and push it. The tag push is the SOLE trigger;
# release.yml then builds once and publishes the GitHub Release + PyPI. No `release-publish`, no
# `uv publish`. Tag by SHA (not "checkout main") so it works from a worktree, where `main` is checked
# out elsewhere and can't be checked out again.
git fetch origin main
git tag -a vX.Y.Z <merge-sha> -m "Release X.Y.Z" && git push origin vX.Y.Z
gh run watch --workflow=release.yml   # confirm the publish job goes green
```

Flags forward straight through poe (no `--` separator needed): `poe release-prepare --bump
minor|patch|major` (override the auto-detected bump), `--version X.Y.Z` (exact override), `--dry-run`
(print the computed version + notes, change nothing), `--push` (also push after committing),
`--skip-gate` (skip `poe pre-release` only — the wheel build + `--version` smoke-test still run, so
packaging breakage is still caught). See `uv run install/release.py prepare --help` for the full set.

## Before merge (on the release branch, in the PR)

1. **Bump** `overlay/pyproject.toml` `version` to the new `X.Y.Z`.
2. **Changelog** — draft with `uv run poe changelog` (git-cliff), then **hand-curate** `CHANGELOG.md`:
   promote `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`, open a fresh empty `## [Unreleased]`. Curate for
   readers (Added / Changed / Fixed / Development) — never ship raw git-cliff output.
3. **Build the wheel:** `cd overlay && uv build --wheel` → `overlay/dist/saitenka-X.Y.Z-py3-none-any.whl`
   (a local packaging check; `release.yml` rebuilds the published artifact from the tag).
4. **Smoke-test the artifact, not the tree** — install the *built wheel* in an isolated env:
   ```sh
   uvx --from overlay/dist/saitenka-X.Y.Z-py3-none-any.whl saitenka --version   # → X.Y.Z
   ```
   This catches packaging breakage (missing data files, entry point, deps) that `poe pre-release` can't.
5. **Gate:** `uv run poe pre-release` green — the fast `poe all` plus the release-only checks pulled
   from the PR loop (supply-chain `audit`/`licenses`, installer `shell`) and the heavier `links-net`
   (network), `smoke-live` (real mpv), and `bench` smokes; needs mpv + network. The advisory tier
   (`hygiene`/`ps1`/`perf-risk`) is human-triage — run it separately, it doesn't gate. Update any
   version-referenced docs.
6. **Merge the PR** into the default branch.

`overlay/dist/` is git-ignored — the wheel is **not** committed; `release.yml` rebuilds it from the tag.

## After merge (on the default branch) — the tag push publishes everything

7. **Annotated tag on the merge commit**, then push it — this is the whole publish:
   ```sh
   git fetch origin main
   git tag -a vX.Y.Z <merge-sha> -m "Release X.Y.Z" && git push origin vX.Y.Z
   ```
   Tag the **merged** commit by SHA (`gh pr view <n> --json mergeCommit`), so the tag points at the
   exact shipped commit and this works from a worktree (`main` is checked out elsewhere and can't be
   re-checked-out). `release.yml` then builds once and publishes the GitHub Release **and** PyPI (the
   Apache-2.0 core + the GPL `deinflect` add-on as separate packages, Trusted Publishing / OIDC). No
   `gh release create`, no `uv publish` — running either duplicates what CI already did.
8. **Watch it go green:** `gh run watch --workflow=release.yml`. If it fails mid-way, fix forward and
   re-run the job (or, CI-down, fall back to `release.py publish` — the only time it's the right tool).
9. **Post-release:** confirm the Release + both PyPI packages are live, `## [Unreleased]` is empty on
   top, the tag matches `pyproject.toml`, and `uv tool install "saitenka[full]"` pulls the new version.

## taffylite (optional Rust layout engine)

`taffylite` (the pyo3 binding behind the dev-only `layout-engine` extra, #146) versions and publishes
**independently** of the steps above, via its own `.github/workflows/taffylite-release.yml`:

- **Tag namespace:** `taffylite-vX.Y.Z` (own `Cargo.toml`/`taffylite/pyproject.toml` `version`, not
  saitenka's `vX.Y.Z`). Tag the merged commit by SHA, same as step 7 above, and push it — that's the
  sole trigger.
- **Build:** a maturin wheel matrix (manylinux x86_64/aarch64, macOS arm64/x86_64, Windows x86_64) ×
  every free-threading-capable interpreter maturin's `--find-interpreter` finds on each runner
  (cp313/cp314/cp314t; cp315t once 3.15 ships and the runners carry it — no abi3, so each interpreter
  needs its own wheel, see `taffylite/Cargo.toml`), plus an sdist.
- **Publish:** Trusted Publishing (OIDC, no token) to PyPI project `taffylite`, same as the packages
  above. `workflow_dispatch` does a TestPyPI dry-run of the current commit.
- **One-time PyPI setup** (already done): a pending Trusted Publisher for `taffylite` on both pypi.org
  and test.pypi.org, repo `serjflint/saitenka`, workflow `taffylite-release.yml`.

Publishing this doesn't change `saitenka[layout-engine]`'s dev-only posture — flipping
`overlay/pyproject.toml`'s `[tool.uv.sources]` editable pin to resolve from PyPI is separate follow-up
work, gated on the first real `taffylite` publish existing to resolve against.

## Notes

- **Compare-link footers** in `CHANGELOG.md` need tags to exist. No `v0.2.0` tag was ever cut; to make
  the links resolve, optionally create it retroactively on the 0.2.0 release commit
  (`git tag -a v0.2.0 c6d3dfb -m "Release 0.2.0" && git push origin v0.2.0`), then add
  `[X.Y.Z]: .../compare/v<prev>..vX.Y.Z` under the placeholder comment.
- **Release candidates:** tag `vX.Y.Z-rc1`, publish as a **pre-release**, smoke-test, then cut `vX.Y.Z`
  from the same commit.
- **Install scripts:** the hosted `install.sh`/`install.ps1` (`serjflint.github.io/saitenka`) install
  from PyPI, so they're release-independent — re-run `poe pages` only when you edit
  `install/overlay-install.{sh,ps1}`, not every release.
- **Trusted Publishing setup** (one-time, can't live in code): register a PyPI publisher for **both**
  `saitenka` and `saitenka-deinflect`, on pypi.org (env `pypi`) and test.pypi.org (env `testpypi`) —
  repo `serjflint/saitenka`, workflow `release.yml`. Details in `release.yml`'s header. A
  `workflow_dispatch` run does a TestPyPI dry-run of the current commit.
