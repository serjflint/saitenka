#!/usr/bin/env bash
# saitenka-overlay bootstrap (macOS / Linux) — Stage 17b.
# The ONLY job the shell does: get `uv`, install the overlay from the wheel next to this script, then
# hand off to the Python `setup` wizard (which owns all real logic). Non-destructive; --dry-run prints.
set -euo pipefail

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
have() { command -v "$1" >/dev/null 2>&1; }
run() { if $DRY_RUN; then printf 'DRY:'; printf ' %q' "$@"; echo; else "$@"; fi; }

# 1. uv — the only hard bootstrap (it then owns Python 3.14t + all deps).
if ! have uv; then
  echo "[saitenka] installing uv…"
  $DRY_RUN || curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. install the overlay from the wheel shipped next to this stub.
WHEEL="$(ls -t "$SELF_DIR"/saitenka_overlay-*.whl 2>/dev/null | head -1 || true)"
if [ -z "${WHEEL:-}" ]; then
  echo "[saitenka] no wheel found next to this installer — is the bundle intact?" >&2
  # under --dry-run this is just a preview outside a bundle; don't hard-fail
  $DRY_RUN || exit 1
else
  echo "[saitenka] installing $WHEEL"
  run uv tool install --reinstall "$WHEEL"
fi

# 3. hand off to the Python wizard (mpv/ffmpeg hints, doctor, init, import, plugin).
if $DRY_RUN; then
  echo "DRY: saitenka-overlay setup --dry-run"
else
  exec saitenka-overlay setup
fi
