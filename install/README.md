# Saitenka installers

Kickstart the toolchain on any machine. **Non-destructive:** each script *discovers* existing tooling,
**upgrades** what's present, **adds** what's missing, and **never removes** anything. It backs up your Anki
collection (excluding the large media folder) and mpv config to a timestamped folder under
`~/.local/state/saitenka/backups/` (macOS/Linux, honors `$XDG_STATE_HOME`) or `%LOCALAPPDATA%\Saitenka\backups\`
(Windows) first — override with `SAITENKA_STATE`. Safe to re-run any time. Add `--dry-run` / `-DryRun` to
preview without changing anything.

Prereq for the very first run on a fresh machine: **GitHub CLI**, authenticated (so it can clone this private
repo): install `gh` (`brew install gh` / `winget install GitHub.cli`), then `gh auth login`.

## macOS (Homebrew)
```sh
gh repo clone serjflint/saitenka ~/saitenka
bash ~/saitenka/install/install-macos.sh          # or: --dry-run / --dev
```

## Windows (Chocolatey-first, winget fallback)
```powershell
gh repo clone serjflint/saitenka $HOME\saitenka
powershell -ExecutionPolicy Bypass -File $HOME\saitenka\install\install-windows.ps1   # or: -DryRun / -Dev
```
Or just **double-click `install\install-windows.cmd`** → right-click → **Run as administrator**. It prefers
**Chocolatey** (admin-friendly, and dodges winget's source corruption `0x8a15000f`; scoop no longer needed) and
falls back to winget. Add `-Dev` for the authoring tools.

### Make SubMiner your default video player (Windows)
SubMiner only attaches its mining overlay when mpv is launched with **`--launch-mpv`** (the built-in
"SubMiner mpv" shortcut does this; a plain file association runs `SubMiner.exe "%1"` with no flag → bare mpv,
no overlay). To route double-click **and Taiga's "Play"** (which uses the default file association) through the
integrated launch, register the handler once — no admin, reversible:
```powershell
powershell -ExecutionPolicy Bypass -File .\install\set-subminer-default.ps1        # or: -Revert / -DryRun
```
Then pick **"SubMiner (mpv)"** in *Settings → Apps → Default apps → Choose defaults by file type* for `.mkv`/`.mp4`.
Keep the SubMiner tray app running (add `SubMiner.lnk` to `shell:startup`) so the overlay can attach. Taiga needs
no other change — it detects the mpv window SubMiner spawns and tracks automatically.

### Fix: Yomitan popup renders behind mpv until you press Alt (Windows)
Known SubMiner-on-Windows bug: the hover **highlight** shows (drawn by mpv), but the **Yomitan popup** (in the
Electron overlay) sits behind the video until you Alt-activate the window. Root cause: SubMiner reclaims overlay
focus on popup-show **only on macOS**. Patch the one line in `app.asar` (same-length in-place edit, asar-integrity
safe, reversible) — **close SubMiner and run as admin**:
```powershell
powershell -ExecutionPolicy Bypass -File .\install\patch-subminer-popup-focus.ps1        # -Revert / -DryRun
```
Re-apply after each SubMiner update (updates overwrite `app.asar`). This automates the Alt press; the proper fix is
upstream (report the macOS-only `reclaimFocus` gate on `YOMITAN_POPUP_SHOWN_EVENT`).

## What it installs / updates
Two tiers — **default** is everything you need to *use* Saitenka; **`--dev` / `-Dev`** adds tooling for
*maintaining the repo + editing the vault*. Add the flag only if you'll hack on the tools.

**Default (mine + study + immerse):**
- **Media + mining runtime:** mpv, ffmpeg, mecab (+ mecab-ipadic on macOS), **bun** (SubMiner's CLI launcher), yt-dlp
  — everything SubMiner needs.
- **uv** — the repo's Python standard; runs the `tools/*.py` engine (see `AGENTS.md`). The tools are pure Python, no node.
- **Anki**; downloads the latest **SubMiner** release (drag/EXE-install). *(SubMiner is a public repo — fetched via
  `gh` if present, else a plain `curl` of the release API, so a default install needs no `gh`.)*

**Dev / authoring (`--dev` / `-Dev`):**
- **git, gh** — clone/update the (private) KB repo & fetch releases · **node** — no current consumer, reserved for
  future JS tooling · **7-zip/unar** — extract manga/dict archives · **Obsidian** — vault editor · **`apy`** — Anki power-CLI.

- **This KB repo** into `~/saitenka` (override with `SAITENKA_DIR`).

## Where things go (conventions)
Each path matches what the thing *is*, not one catch-all dir:

| Thing | Location | Why |
|---|---|---|
| Binaries / apps | `/opt/homebrew` (brew), `/Applications` (SubMiner) | OS/package-manager convention |
| Tool caches | `~/.cache/saitenka/` (honors `$XDG_CACHE_HOME`) | regenerable — XDG cache (the chooser's MoeDB cache lives here) |
| Backups (transient state) | `~/.local/state/saitenka/backups/` (`$XDG_STATE_HOME`) · `%LOCALAPPDATA%\Saitenka\` | machine-local state, not user-facing — hidden by design |
| **Repo checkout** | **visible** — `~/saitenka` default, or `$SAITENKA_DIR` | it's *code you open daily*, **not** an app/cache → a hidden dir would fight editors' + Finder's file pickers |

Rule of thumb: caches/state/config → hidden XDG dirs; the checkout you actually edit → a visible project dir.
Set `SAITENKA_DIR` to point the installer at an existing checkout (e.g. `~/saitenka`)
instead of cloning a second copy.

## Yomitan dictionaries (recommended — extra, browser-side)
**Not installed by the installer** — Yomitan dicts import into the *browser extension's* storage, which a shell
installer can't script; and the monolinguals + pitch are copyrighted, so the repo doesn't bundle or direct-link
them. They're a manual browser step. Fastest ways to get them onto a machine:
- **Your own devices** (recommended): Yomitan → Settings → *Import Dictionary Collection* → your
  `yomitan-dictionaries-*.json[.zip]` backup — one action restores your whole set, including dicts you own.
- **Fresh / picking à la carte:** import the individual zips (in priority order `01…16`) or grab from the source
  sites below.
- **Personal FSRS dicts** (the only ones we generate): `uv run tools/anki_rank_dicts.py` → import the output zips.

Import your own Yomitan zips — the overlay works with any. A well-rounded set, by category:

- **Bilingual** (JP→EN): a primary definition dict, optionally one with example sentences
- **Grammar (EN):** high-value for a rusty learner
- **Names / kanji:** a name dictionary + a kanji dictionary
- **Monolingual** (J–J, add as you level up): start with the simplest, keep the rest low-priority
- **Frequency** (sort order): a general corpus → media/anime → written baseline (harmonic-blended)
- **Pitch:** a pitch dict with a `term_meta` build (renders pitch graphs), *not* a definition-style one
- **Personal** (generated locally, not downloaded): knowness / reactivate / learn-new — `uv run tools/anki_rank_dicts.py`

### Where to get them — source sites (not direct downloads)
These are the **sites the list was compiled from** — browse them and import into Yomitan yourself. We deliberately
**don't ship direct download links**: versions change, and the monolinguals + pitch are copyrighted (buy officially,
or use the community collections).

- Yomitan official list — https://yomitan.wiki/dictionaries/
- Jitendex — https://jitendex.org/
- JMnedict · KANJIDIC (yomidevs) — https://github.com/yomidevs/jmdict-yomitan
- MarvNC hub — https://github.com/MarvNC/yomitan-dictionaries
- Kuuuube (frequency + kanji dictionaries) — https://github.com/Kuuuube/yomitan-dictionaries
- TheMoeWay collection — https://learnjapanese.moe/yomichan/
- DoJG + grammar (aiko-tanaka) — https://github.com/aiko-tanaka/Grammar-Dictionaries · reference https://dojg.github.io/
- Jiten (anime frequency) — https://jiten.moe/
- Commercial (buy legally): Monokakido — https://www.monokakido.jp/ · Sanseido — https://www.sanseido-publ.co.jp/
- Android import guide — https://lazyguidejp.github.io/jp-lazy-guide/setupYomitanOnAndroid/

## Health check (doctor)
The installer finishes by running a **read-only** healthcheck; run it any time on its own:
```sh
bash ~/saitenka/install/doctor-macos.sh                                    # macOS
powershell -ExecutionPolicy Bypass -File .\install\doctor-windows.ps1      # Windows
```
It verifies the runtime (mpv/ffmpeg/mecab/bun/uv/yt-dlp), that **MeCab tokenizes**, the **bun GUI-PATH gap**
that blocks SubMiner, **AnkiConnect** on `:8765`, **SubMiner + its launcher**, the mpv rig, the bundled
frequency dicts, and backups — printing `✓ / ! / ✗` and exiting non-zero if anything critical fails.

It also **reads SubMiner's `config.jsonc`** and checks the settings behind the common overlay problems:
`mpv.launchMode` (warns if not `fullscreen`/`maximized` — windowed mpv puts subs mid-screen and breaks hover),
`auto_start_overlay`, `mpv.autoStartSubMiner`, `subtitlePosition.yPercent`, and the log level (so you can flip
it to `debug` before capturing logs). Config lives at `~/.config/SubMiner/config.jsonc` (macOS) /
`%APPDATA%\SubMiner\config.jsonc` (Windows).

## What it does NOT do (on purpose)
- Never uninstalls or overwrites your existing mpv/Anki configs — only backs them up.
- Anki **add-ons** are GUI-installed (codes printed at the end; see `notes/Tooling/Anki Add-ons`).
- Doesn't touch your Yomitan dictionaries or mined cards (those live in Anki/AnkiWeb).
