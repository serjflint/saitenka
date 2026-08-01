# Install

Saitenka draws its study overlay into a **real, local mpv** process, so mpv is the one hard
requirement. Everything else — the Python interpreter, ffmpeg, the mpv plugin — the installer and the
`setup` wizard can put in place for you.

## Prerequisites

| Tool | Need | Notes |
|------|------|-------|
| **mpv** ≥ 0.37 | required | `setup` installs it (Homebrew / winget) if it's missing; it need not be on `PATH` beforehand. |
| **ffmpeg** | required | Screenshot + clip capture for mining; `setup` installs it too. |
| **[uv](https://docs.astral.sh/uv/)** | required | Provides the Python interpreter (3.13+) and dependencies. The release-bundle installer bootstraps it for you. |
| **[Anki](https://apps.ankiweb.net/)** + **[AnkiConnect](https://ankiweb.net/shared/info/2055492159)** | optional | FSRS-aware coloring and one-key mining. Without it you can still color from a manual known-word set. |
| **[Yomitan](https://github.com/yomidevs/yomitan)** dictionaries | optional | Your own `.zip` dictionaries (or a full database export) drive the tooltip; [import them once](#dictionary-import). |

!!! note "You don't need a clone"
    The release bundle and the `uv tool` install both fetch a published package — no `git clone`, no
    build step. Cloning is only for contributors (see the [Architecture](../contributing/architecture.md)
    docs).

## Install

=== "Release bundle (recommended)"

    A self-contained zip that bootstraps `uv`, installs the overlay with the full feature set, and hands
    off to the `setup` wizard — no prerequisites of its own.

    1. Download **`saitenka-<version>.zip`** from the
       [latest release](https://github.com/serjflint/saitenka/releases/latest) and unzip it.
    2. From inside the unzipped folder, run the installer for your OS:

    ```bash
    # macOS / Linux — add --dry-run to preview without changing anything
    bash overlay-install.sh
    ```

    ```powershell
    # Windows (PowerShell)
    powershell -ExecutionPolicy Bypass -File overlay-install.ps1
    ```

=== "uv tool (package only)"

    Install just the Python package with `uv`, then run the wizard yourself:

    ```bash
    uv tool install "saitenka[full]"
    saitenka setup
    ```

    Pick a narrower [feature set](#feature-extras) by swapping the extra, e.g.
    `uv tool install "saitenka[jmdict]"`.

Both paths end at the same place: `saitenka setup` has installed mpv + ffmpeg, written your config, and
installed the mpv plugin so **every future mpv launch auto-starts the overlay**.

## Feature extras

The package ships a bare core; extras add optional features. `[full]` and `[deinflect]` pull the
**GPL-3.0** inflection add-on, which makes the *combined* install GPL-3.0 (the core alone is
Apache-2.0 — see [LICENSING.md](https://github.com/serjflint/saitenka/blob/main/LICENSING.md)).

| Extra | Adds | License |
|-------|------|---------|
| *(none)* / `minimal` | the bare overlay — bring your own Yomitan dictionaries | Apache-2.0 |
| `jmdict` | JMdict English fallback (hover + mined-card glosses when a word isn't in your dicts) | Apache-2.0 |
| `deinflect` | the 🧩 inflection-chain display (Yomitan-derived) | **GPL-3.0** |
| `linux-keyring` | Linux Secret Service storage for the jimaku key on Python 3.15+ | Apache-2.0 |
| `full` | all portable features above (`linux-keyring` stays explicit) | **GPL-3.0** |

Mining prefers *your* dictionaries, so `jmdict` is only a fallback. On Linux, Python 3.13/3.14 install
Secret Service support by default; Python 3.15+ uses `JIMAKU_API_KEY` or an owner-only
`$XDG_CONFIG_HOME/saitenka/jimaku.key` unless `linux-keyring` is installed.

## `saitenka setup`

The wizard is **confirm-first and resumable** — it shows what it will do, and re-running it after an
interruption picks up where it left off. In one pass it:

- installs **mpv + ffmpeg** (or prints your distro's install command);
- runs [`doctor`](../usage/cli.md) to check the environment;
- writes your `overlay.toml` config;
- imports any dictionaries it's pointed at;
- installs the **auto-start mpv plugin** (`saitenka.lua`), so opening any video in mpv attaches the
  overlay with no extra command.

!!! tip "Re-run it any time"
    `saitenka setup` is safe to run again to repair mpv/ffmpeg, refresh the config, or reinstall the
    plugin. To reinstall only the plugin, use `saitenka install-plugin`.

## Dictionary import

Do this **once**. Dictionaries are built into a single consolidated SQLite database and their **titles**
are registered in your config; `run`/`attach` then open the DB instantly with nothing rebuilt at play
time. The source zips are read in place — no copy is kept, so you can move or delete them afterward.

```bash
saitenka import <dir-of-zips>     # classify (definition/frequency/pitch), build the DB, register titles
```

Have a **Yomitan settings export** instead of loose zips? Import it and point it at the folder holding
the zips:

```bash
saitenka import-settings <export.json> --scan-dir <dir-of-zips>
```

`saitenka doctor` lists what's imported. Where the titles and colors live, and every overridable path,
is covered in [Configuration](../usage/configuration.md).

## Maintenance

Each of these has full detail in the [CLI reference](../usage/cli.md) — the essentials:

| Command | What it's for |
|---------|---------------|
| `saitenka update` | Pull the latest release, **keeping your current extras**. |
| `saitenka reinstall` | Reinstall to change the extras or the source (`--yes` skips the prompt). |
| `saitenka doctor` | Diagnose the whole environment: mpv/ffmpeg, config, AnkiConnect, imported dictionaries, recent errors. |
| `saitenka report` | Bundle a redacted bug report (logs + environment) to attach to an issue. |

## Troubleshooting

Start with **`saitenka doctor`** — it checks mpv/ffmpeg, config validity, AnkiConnect reachability, and
imported dictionaries, and surfaces recent errors from the log. If something still looks wrong, run
`saitenka report` and attach the bundle to a [GitHub issue](https://github.com/serjflint/saitenka/issues).
Per-command flags and diagnostics are in the [CLI reference](../usage/cli.md).

---

**Next:** [Quickstart](quickstart.md) — from a fresh install to your first mined card.
