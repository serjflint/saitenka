#!/usr/bin/env sh
# saitenka installer (macOS / Linux). Bootstraps `uv`, installs `saitenka[full]` from PyPI, then hands
# off to the `setup` wizard (mpv/ffmpeg, config, the auto-start mpv plugin). Non-destructive; `--dry-run`
# previews. The whole body is wrapped in main() and invoked on the last line, so a truncated `curl | sh`
# download runs NOTHING rather than a half-script.
#
#   curl --proto '=https' --tlsv1.2 -LsSf https://serjflint.github.io/saitenka/install.sh | sh
#
# Prefer to read it first:
#   curl --proto '=https' --tlsv1.2 -LsSf https://serjflint.github.io/saitenka/install.sh -o install.sh
#   less install.sh && sh install.sh
set -eu

main() {
    dry_run=false
    [ "${1:-}" = "--dry-run" ] && dry_run=true
    have() { command -v "$1" >/dev/null 2>&1; }
    run() { if $dry_run; then printf 'DRY:'; printf ' %s' "$@"; echo; else "$@"; fi; }

    # 1. uv — the only bootstrap. It then owns Python 3.13+ and every dependency, verifying PyPI hashes.
    #    (uv's own installer is hardened + checksummed; see https://docs.astral.sh/uv/.)
    if ! have uv; then
        echo "[saitenka] installing uv…"
        $dry_run || curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh | sh
        # uv installs to ~/.local/bin, not on PATH in this shell — add it for the setup handoff below.
        export PATH="$HOME/.local/bin:$PATH"
    fi

    # 2. install saitenka with every portable feature (deinflect + jmdict + telemetry) from PyPI.
    #    --reinstall makes re-running this installer an in-place upgrade.
    echo "[saitenka] installing saitenka[full] from PyPI…"
    run uv tool install --reinstall "saitenka[full]"

    # 3. hand off to the setup wizard (installs mpv+ffmpeg or prints your distro's command; config; plugin).
    if $dry_run; then
        echo "DRY: saitenka setup --dry-run"
    else
        exec saitenka setup
    fi
}

main "$@"
