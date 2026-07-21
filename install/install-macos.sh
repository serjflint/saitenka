#!/usr/bin/env bash
# Saitenka overlay installer — macOS.
# Installs/updates the in-mpv overlay from THIS repo checkout and installs only the tools that are
# missing. Non-destructive: never upgrades or reinstalls what's already present, and never touches
# your Anki collection or mpv config (the steps that write user files — config, the mpv plugin —
# back up their own target right before changing it). Safe to re-run any time.
#   Usage:  bash install/install-macos.sh [--dry-run] [--dev]
set -uo pipefail

DRY_RUN=false; DEV=false
for a in "$@"; do case "$a" in
  --dry-run) DRY_RUN=true ;;
  --dev)     DEV=true ;;
  -h|--help) echo "usage: install-macos.sh [--dry-run] [--dev]"; exit 0 ;;
  *) echo "unknown arg: $a (see --help)"; exit 2 ;;
esac; done

c() { printf '\033[%sm' "$1"; }
log()  { printf '%s[saitenka]%s %s\n' "$(c '1;36')" "$(c 0)" "$*"; }
warn() { printf '%s[warn]%s %s\n'     "$(c '1;33')" "$(c 0)" "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
run()  { if $DRY_RUN; then printf '  DRY:'; printf ' %q' "$@"; echo; else "$@"; fi; }

SELF_DIR="$(cd "$(dirname "$0")" && pwd 2>/dev/null || echo .)"
REPO="$(cd "$SELF_DIR/.." && pwd)"

# This installer runs FROM the repo checkout — that IS the source of the code we install.
if [ ! -d "$REPO/overlay" ]; then
  warn "no overlay/ next to this installer ($REPO) — run it from a repo checkout, or use install/overlay-install.sh (wheel bundle)."
  exit 1
fi

# ── 0. Discovery (never clobber what's here) ────────────────────────────────
log "Discovering existing tooling…"
for t in brew uv mpv ffmpeg yt-dlp; do
  if have "$t"; then printf '  \033[32m✓\033[0m %-9s %s\n' "$t" "$(command -v "$t")"
  else printf '  \033[31m✗\033[0m %-9s (missing)\n' "$t"; fi
done
[ -d /Applications/Anki.app ] && printf '  \033[32m✓\033[0m Anki.app\n' || printf '  \033[31m✗\033[0m Anki.app (missing)\n'
$DEV && for app in Obsidian; do [ -d "/Applications/$app.app" ] && printf '  \033[32m✓\033[0m %s.app\n' "$app"; done

# ── 1. Homebrew — install ONLY what's missing (no update, no upgrade) ───────
if ! have brew; then
  log "Installing Homebrew…"
  $DRY_RUN || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
fi

brew_need() { # formula — install only if absent; leave present ones untouched
  if brew list --formula "$1" &>/dev/null || have "$1"; then log "✓ $1 present (left as-is)"
  else log "+ $1"; run brew install "$1"; fi
}

# The overlay runtime needs mpv + ffmpeg; uv provides its own Python 3.14t + all deps (incl. the
# fugashi/unidic tokenizer — no system mecab). yt-dlp is only for fetching media.
if have brew; then
  for f in mpv ffmpeg; do brew_need "$f"; done
  have uv || brew_need uv
fi
# Anki: only offer the cask if the app isn't already installed (avoids a needless multi-GB download).
if [ ! -d /Applications/Anki.app ]; then
  if have brew && ! brew list --cask anki &>/dev/null; then log "+ anki (cask)"; run brew install --cask anki
  else warn "Anki not found — install it from https://apps.ankiweb.net"; fi
fi

# uv is the one hard requirement for the overlay itself.
if ! have uv; then
  log "Installing uv…"; $DRY_RUN || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
fi

# ── 2. Install / update the overlay from THIS checkout ──────────────────────
log "Installing/updating saitenka-overlay from $REPO/overlay"
run uv tool install --reinstall "$REPO/overlay"

# ── 3. Dev/authoring extras (--dev only): repo + vault tooling ──────────────
if $DEV && have brew; then
  log "Dev/authoring deps (--dev): repo + vault tooling"
  for f in git gh node p7zip unar; do brew_need "$f"; done
  brew list --cask obsidian &>/dev/null || { log "+ obsidian (cask)"; run brew install --cask obsidian; }
  have apy || { log "+ apy (apyanki)"; run uv tool install apyanki; }
fi

# ── 4. Health check (the overlay's own doctor) ──────────────────────────────
log "Running healthcheck (doctor-macos.sh)…"
if [ -f "$SELF_DIR/doctor-macos.sh" ]; then
  bash "$SELF_DIR/doctor-macos.sh" || warn "doctor reported issues — see ✗/! above."
else
  warn "doctor-macos.sh not found next to the installer — skipping healthcheck."
fi

log "Done."

# Anki add-ons install as a folder named by their AnkiWeb code under addons21/ — check on disk so
# we tick the ones already present and only nudge for the missing.
ADDONS="$HOME/Library/Application Support/Anki2/addons21"
addon() { # code  name  note
  if [ -d "$ADDONS/$1" ]; then printf '       \033[32m✓\033[0m %-14s installed\n' "$2"
  else printf '       \033[33m○\033[0m %-14s paste %-11s (%s)\n' "$2" "$1" "$3"; fi
}
# jimaku key is "present" if it resolves from the env or the Keychain (the recommended stores).
jimaku_present() {
  [ -n "${JIMAKU_API_KEY:-}" ] && return 0
  security find-generic-password -s saitenka-overlay -a jimaku -w >/dev/null 2>&1
}
# How many dictionary/freq/pitch zips the config points at actually exist on disk (echoes the count,
# non-zero exit when none) — so we can tick step 3 instead of nudging an import that's already done.
CONFIG="${SAITENKA_CONFIG:-$HOME/.config/saitenka/overlay.toml}"
dicts_present() {
  [ -f "$CONFIG" ] || return 1
  local n=0 p
  while IFS= read -r p; do
    p="${p/#\~/$HOME}"
    [ -f "$p" ] && n=$((n+1))
  done < <(grep -vE '^[[:space:]]*#' "$CONFIG" | grep -oE '"[^"]*\.zip"' | tr -d '"')
  [ "$n" -gt 0 ] && { echo "$n"; return 0; } || return 1
}

echo
echo "Next steps:"
if [ -f "$HOME/.config/mpv/scripts/saitenka.lua" ]; then
  printf '  1. mpv plugin:  \033[32m✓\033[0m installed (auto-starts the overlay on any mpv launch)\n'
else
  echo "  1. Install the mpv plugin (auto-starts on any mpv launch):  saitenka-overlay install-plugin"
  echo "     — or the full wizard (config, dict relocation, plugin):  saitenka-overlay setup"
fi
echo "  2. Anki add-ons (Tools → Add-ons → Get Add-ons):"
addon 2055492159 AnkiConnect    "mining + FSRS coloring"
addon 759844606  "FSRS Helper"  "better scheduling"
addon 1771074083 "Review Heatmap" "streak view"
if dcnt=$(dicts_present); then
  printf '  3. Dictionaries:  \033[32m✓\033[0m %s configured and present on disk\n' "$dcnt"
else
  echo "  3. Import your Yomitan dictionaries in the browser, then point overlay.toml at them"
  echo "     (or run \`saitenka-overlay import-yomitan\`)."
fi
if jimaku_present; then
  printf '  4. jimaku key for auto-subs:  \033[32m✓\033[0m already set\n'
else
  echo "  4. jimaku key for auto-subs (optional):  saitenka-overlay set-jimaku-key"
fi
$DEV && cat <<'EOF'

Dev/authoring:
  • Anki MCP for Claude Code: run /mcp.
  • A personal study vault (notes), if you keep one, is a separate repo.
EOF
exit 0
