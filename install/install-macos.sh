#!/usr/bin/env bash
# Saitenka overlay installer — macOS.
# Installs/updates the in-mpv overlay from THIS repo checkout and installs only the tools that are
# missing. Non-destructive: never upgrades or reinstalls what's already present, and never touches
# your Anki collection or mpv config (the steps that write user files — config, the mpv plugin —
# back up their own target right before changing it). Safe to re-run any time.
#   Usage:  bash install/install-macos.sh [--dry-run] [--dev] [--yes]
set -uo pipefail

DRY_RUN=false; DEV=false; YES=false
for a in "$@"; do case "$a" in
  --dry-run) DRY_RUN=true ;;
  --dev)     DEV=true ;;
  --yes|-y)  YES=true ;;
  -h|--help) echo "usage: install-macos.sh [--dry-run] [--dev] [--yes]"; exit 0 ;;
  *) echo "unknown arg: $a (see --help)"; exit 2 ;;
esac; done

c() { printf '\033[%sm' "$1"; }
log()  { printf '%s[saitenka]%s %s\n' "$(c '1;36')" "$(c 0)" "$*"; }
warn() { printf '%s[warn]%s %s\n'     "$(c '1;33')" "$(c 0)" "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
run()  { if $DRY_RUN; then printf '  DRY:'; printf ' %q' "$@"; echo; else "$@"; fi; }
# Y/n prompt (default yes). --yes answers yes to all (CI/non-interactive); --dry-run assumes yes so
# the preview shows the full plan.
confirm() { if $YES || $DRY_RUN; then return 0; fi; read -r -p "$1 [Y/n] " _a; [[ ! "$_a" =~ ^[Nn] ]]; }

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

brew_need() { # formula — install only if absent. Present ones are already listed in Discovery above,
  # so stay silent (only announce an actual install) instead of repeating "✓ present" per formula.
  if brew list --formula "$1" &>/dev/null || have "$1"; then :
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
  if have brew && ! brew list --cask anki &>/dev/null; then
    if confirm "Install Anki now (needed for mining + FSRS coloring)?"; then log "+ anki (cask)"; run brew install --cask anki
    else log "skipped Anki — install later from https://apps.ankiweb.net, then re-run"; fi
  else warn "Anki not found — install it from https://apps.ankiweb.net"; fi
fi

# uv is the one hard requirement for the overlay itself. Standalone-installer fallback per uv's guide:
# https://docs.astral.sh/uv/getting-started/installation/
if ! have uv; then
  log "Installing uv…"; $DRY_RUN || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
fi

# ── 2. Install / update the overlay from THIS checkout ──────────────────────
# uv puts tool binaries (saitenka-overlay) in ~/.local/bin; ensure THIS session sees it so the setup
# handoff below resolves even when uv was already on PATH (e.g. brew-installed) and didn't add it.
export PATH="$HOME/.local/bin:$PATH"
# FULL experience via the `[full]` extra (JMdict fallback + GPL-3.0 deinflect inflection chains) when
# the deinflect source is present in this checkout; else `[jmdict]` (full minus the GPL add-on).
# `[full]` is GPL-3.0 (see ../LICENSING.md); a wheel/bundle install without deinflect/ stays Apache-2.0.
if [ -d "$REPO/deinflect" ]; then extra=full; log "including GPL-3.0 deinflect add-on (inflection chains)"
else extra=jmdict; warn "no deinflect/ in this checkout — installing [jmdict] only (no inflection chains)"; fi
log "Installing/updating saitenka-overlay[$extra] from $REPO/overlay"
run uv tool install --reinstall --quiet "$REPO/overlay[$extra]"

# ── 3. Dev/authoring extras (--dev only): repo + vault tooling ──────────────
if $DEV && have brew; then
  log "Dev/authoring deps (--dev): repo + vault tooling"
  for f in git gh node p7zip unar; do brew_need "$f"; done
  brew list --cask obsidian &>/dev/null || { log "+ obsidian (cask)"; run brew install --cask obsidian; }
  have apy || { log "+ apy (apyanki)"; run uv tool install apyanki; }
fi

# ── 4. Guided setup: the overlay's own confirm-first wizard ─────────────────
# The wizard below runs its own health check (twice: an initial read and a final self-verify), so
# there's no separate doctor-macos.sh pass here — that would just print the same report a third time.
# Run `bash install/doctor-macos.sh` any time for a standalone check.
# Hand off to `setup` (prompts to install the mpv plugin, store the jimaku key, import Yomitan dicts,
# relocate protected dicts) instead of leaving them as manual chores. Confirm-first and resumable;
# --yes passes --yes. The summary below then reflects whatever setup configured.
if have saitenka-overlay; then
  log "Guided setup (mpv plugin / jimaku key / dictionaries)…"
  setup_args=(setup); $YES && setup_args+=(--yes); $DRY_RUN && setup_args+=(--dry-run)
  saitenka-overlay "${setup_args[@]}" || warn "setup reported issues — re-run any time: saitenka-overlay setup"
else
  warn "saitenka-overlay isn't on PATH this session — open a NEW terminal and run: saitenka-overlay setup"
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
  security find-generic-password -s saitenka-overlay -a jimaku -w >/dev/null 2>&1 && return 0
  # set-jimaku-key writes [jimaku] to the config (fetch=true) even when the key lives in the Keychain.
  [ -f "$CONFIG" ] && grep -qE '^[[:space:]]*\[jimaku\]' "$CONFIG"
}
# Dictionaries are imported ONCE into the consolidated database (dictionaries.sqlite); the config then
# lists their TITLES (not zip paths). "Present" = that DB exists AND the config references at least one
# dict/freq/pitch title — so we can tick step 3 instead of nudging an import that's already done.
CONFIG="${SAITENKA_CONFIG:-$HOME/.config/saitenka/overlay.toml}"
DICT_DB="${SAITENKA_DATA_DIR:-$HOME/.local/share/saitenka}/dictionaries.sqlite"
dicts_present() {
  [ -f "$DICT_DB" ] && [ -f "$CONFIG" ] || return 1
  grep -vE '^[[:space:]]*#' "$CONFIG" | grep -qE '^[[:space:]]*(dicts|freq|pitch)[[:space:]]*=[[:space:]]*\['
}

echo
echo "Next steps:"
if [ -f "$HOME/.config/mpv/scripts/saitenka.lua" ]; then
  printf '  1. mpv plugin:  \033[32m✓\033[0m installed (auto-starts the overlay on any mpv launch)\n'
else
  echo "  1. mpv plugin not installed — re-run  saitenka-overlay setup  (or:  saitenka-overlay install-plugin)"
fi
echo "  2. Anki add-ons (Tools → Add-ons → Get Add-ons):"
addon 2055492159 AnkiConnect    "mining + FSRS coloring"
addon 759844606  "FSRS Helper"  "better scheduling"
addon 1771074083 "Review Heatmap" "streak view"
if dicts_present; then
  printf '  3. Dictionaries:  \033[32m✓\033[0m imported into the database (see `saitenka-overlay doctor`)\n'
else
  echo "  3. Dictionaries: run  saitenka-overlay import <folder of your Yomitan .zip dicts>"
  echo "     (imports them once into the consolidated database and fills the config with their titles)."
  echo "       config: $CONFIG"
  echo "     Have a Yomitan settings export? saitenka-overlay import-settings <export.json> --scan-dir <folder>"
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
