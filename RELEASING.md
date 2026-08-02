# Releasing Saitenka

Releases are cut **manually from macOS** (automated release CI is deferred). Distribution is **PyPI**
(`uv tool install "saitenka[full]"`) plus the hosted install scripts (`serjflint.github.io/saitenka`,
kept in sync by `poe pages`); a release is a **git tag + a notes-only GitHub Release**, and `uv publish`
uploads the wheel to PyPI. This file is the source of truth for the human steps — `install/release.py`
automates them (see "Automated" below) but the review gates it can't remove (curating the changelog,
merging the PR, publishing the Release, uploading to PyPI) still happen exactly where they're described here.

## Versioning

[SemVer](https://semver.org). Pre-1.0, the **minor is the effective major**: any feature batch (or a
breaking change) is a minor bump — `0.2.0 → 0.3.0`. Don't reach for `1.0.0` until the CLI/config is
being frozen.

Single source of truth: **`overlay/pyproject.toml` `version`** (read at runtime via
`importlib.metadata`). Nothing else hardcodes the version.

## Automated

`install/release.py` (via `poe release-prepare` / `poe release-publish`) runs steps 1-6 and 8-9
below for you. It still requires a human at two points: the changelog entry (write it to
`RELEASE_NOTES.md` first — never generated unattended) and the actual publish (`--publish`, or
`gh release edit --draft=false` by hand) and PR merge (step 7 — never automated).

```sh
$EDITOR RELEASE_NOTES.md              # curate the changelog entry (bullets, no heading)
uv run poe release-prepare             # steps 1-5: bump version (auto patch/minor/major from
                                        # Conventional Commits, capped at minor pre-1.0), insert
                                        # the changelog section, `poe pre-release`, build+smoke-test the
                                        # wheel, commit `chore(overlay): release X.Y.Z`
git push && gh pr create               # step 6: open the PR, get it reviewed, merge it

git checkout main && git pull
uv run poe release-publish             # steps 7-8: tag, rebuild+smoke-test the wheel from the
                                        # merged commit, create the (notes-only) Release as a draft
uv run poe release-publish --publish   # flip the draft live (or `gh release edit` by hand)
cd overlay && uv publish               # step 9: upload the wheel to PyPI (needs your token)
```

Flags forward straight through poe (no `--` separator needed): `poe release-prepare --bump
minor|patch|major` (override the auto-detected bump), `--version X.Y.Z` (exact override), `--dry-run`
(print the computed version + notes, change nothing), `--push` (also push after committing). `poe
release-publish --skip-gate` to re-run after a failed step without re-running the whole gate. Both
wrap `uv run install/release.py prepare|publish --help` for the full set.

## Before merge (on the release branch, in the PR)

1. **Bump** `overlay/pyproject.toml` `version` to the new `X.Y.Z`.
2. **Changelog** — draft with `uv run poe changelog` (git-cliff), then **hand-curate** `CHANGELOG.md`:
   promote `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`, open a fresh empty `## [Unreleased]`. Curate for
   readers (Added / Changed / Fixed / Development) — never ship raw git-cliff output.
3. **Build the wheel:** `cd overlay && uv build --wheel` → `overlay/dist/saitenka-X.Y.Z-py3-none-any.whl`
   (the artifact `uv publish` uploads).
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

`overlay/dist/` is git-ignored — the wheel is **not** committed; `uv publish` uploads it to PyPI.

## After merge (on the default branch)

7. **Annotated tag on the merge commit**, then push it:
   ```sh
   git tag -a vX.Y.Z -m "Release X.Y.Z" && git push origin vX.Y.Z
   ```
   Tag *after* merge so it points at the exact shipped commit.
8. **GitHub Release, draft-first** (notes only — no binary assets; the auto "Source code" archive covers source):
   ```sh
   gh release create vX.Y.Z --draft --title "X.Y.Z" \
     --notes-file <(sed -n '/## \[X.Y.Z\]/,/## \[/p' CHANGELOG.md | sed '$d')
   ```
   Verify the draft (notes correct), then **publish** (`gh release edit vX.Y.Z --draft=false`).
9. **PyPI** — the install channel; this is what makes `uv tool install` / `curl … | sh` see the new
   version. Publish the Apache-2.0 core wheel (the GPL `deinflect` add-on is its own package, already on
   PyPI, so the license boundary stays clean):
   ```sh
   cd overlay && uv publish        # uploads dist/saitenka-X.Y.Z-py3-none-any.whl (needs your token)
   ```
10. **Post-release:** confirm `## [Unreleased]` is empty on top, tags == `pyproject.toml`, and
    `uv tool install "saitenka[full]"` pulls the new version.

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
- **Automate later:** a `pypa/gh-action-pypi-publish` (trusted publishing, no token) on `push: tags: v*`
  could replace the manual `uv publish` — tied to enabling CI. Not worth it while releases are local.
