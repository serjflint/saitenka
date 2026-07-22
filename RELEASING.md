# Releasing Saitenka

Releases are cut **manually from macOS** (CI is deferred — see `ROADMAP.md`). The distributable is a
single `dist/saitenka-overlay-<ver>.zip` (wheel + GPL `deinflect` sdist + installers), published as a
**GitHub Release**. This file is the source of truth for the human steps.

## Versioning

[SemVer](https://semver.org). Pre-1.0, the **minor is the effective major**: any feature batch (or a
breaking change) is a minor bump — `0.2.0 → 0.3.0`. Don't reach for `1.0.0` until the CLI/config is
being frozen.

Single source of truth: **`overlay/pyproject.toml` `version`** (read at runtime via
`importlib.metadata`, and by `install/make_bundle.py`). Nothing else hardcodes the version.

## Before merge (on the release branch, in the PR)

1. **Bump** `overlay/pyproject.toml` `version` to the new `X.Y.Z`.
2. **Changelog** — draft with `uv run poe changelog` (git-cliff), then **hand-curate** `CHANGELOG.md`:
   promote `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`, open a fresh empty `## [Unreleased]`. Curate for
   readers (Added / Changed / Fixed / Development) — never ship raw git-cliff output.
3. **Build** the bundle: `uv run install/make_bundle.py` → `dist/saitenka-overlay-X.Y.Z.zip`.
4. **Checksums:** `cd dist && shasum -a 256 saitenka-overlay-X.Y.Z.zip > SHA256SUMS`.
   (Integrity only — GPG/Sigstore is intentionally skipped until a downstream packager needs it.)
5. **Smoke-test the artifact, not the tree** — extract the zip in a temp dir and run the *built wheel* in
   an isolated env:
   ```sh
   cd $(mktemp -d) && unzip -q <repo>/dist/saitenka-overlay-X.Y.Z.zip
   uvx --from ./saitenka_overlay-X.Y.Z-py3-none-any.whl saitenka-overlay --version   # → X.Y.Z
   ```
   This catches packaging breakage (missing data files, entry point, deps) that `poe all` can't.
6. **Gate:** `uv run poe all` green; update any version-referenced docs.
7. **Merge the PR** into the default branch.

`dist/` is git-ignored — the zip and `SHA256SUMS` are **not** committed; they ride on the Release.

## After merge (on the default branch)

8. **Annotated tag on the merge commit**, then push it:
   ```sh
   git tag -a vX.Y.Z -m "Release X.Y.Z" && git push origin vX.Y.Z
   ```
   Tag *after* merge so it points at the exact shipped commit.
9. **GitHub Release, draft-first** (so both assets are attached before it goes live):
   ```sh
   gh release create vX.Y.Z --draft --title "X.Y.Z" \
     --notes-file <(sed -n '/## \[X.Y.Z\]/,/## \[/p' CHANGELOG.md | sed '$d') \
     dist/saitenka-overlay-X.Y.Z.zip dist/SHA256SUMS
   ```
   Verify the draft (assets present, notes correct), then **publish** (`gh release edit vX.Y.Z --draft=false`).
10. **Post-release:** confirm `## [Unreleased]` is empty on top and tags == `pyproject.toml`.

## Notes

- **Compare-link footers** in `CHANGELOG.md` need tags to exist. No `v0.2.0` tag was ever cut; to make
  the links resolve, optionally create it retroactively on the 0.2.0 release commit
  (`git tag -a v0.2.0 c6d3dfb -m "Release 0.2.0" && git push origin v0.2.0`), then add
  `[X.Y.Z]: .../compare/v<prev>..vX.Y.Z` under the placeholder comment.
- **Release candidates:** tag `vX.Y.Z-rc1`, publish as a **pre-release**, smoke-test, then cut `vX.Y.Z`
  from the same commit.
- **PyPI (optional, deferred):** to make `uvx saitenka-overlay` work, publish only the Apache-2.0 core
  wheel (`uv publish` from `overlay/`), keeping the GPL `deinflect` add-on an opt-in extra so the license
  boundary stays clean. GitHub Releases remain the primary channel (bundle + installers for non-Python
  users).
- **Automate later:** once a runner builds the zip, a ~15-line `softprops/action-gh-release@v2` on
  `push: tags: v*` can attach assets automatically — a one-file change, tied to enabling CI. Not worth it
  while builds are local.
