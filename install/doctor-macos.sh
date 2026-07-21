#!/usr/bin/env bash
# Saitenka doctor — read-only health check for the overlay on macOS. Changes NOTHING.
# The overlay's own `saitenka-overlay doctor` is the source of truth (mpv/ffmpeg, config, dicts,
# AnkiConnect, plugin, jimaku key, sub-auto); this wrapper adds the shell-level toolchain check and
# hands off to it. Exit 0 = healthy, 1 = failures.
#   Usage:  bash doctor-macos.sh
set -uo pipefail

pass=0; warn=0; fail=0
ok(){ printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
wn(){ printf '  \033[33m!\033[0m %s\n' "$*"; warn=$((warn+1)); }
er(){ printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }
hdr(){ printf '\n\033[1m%s\033[0m\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }

printf '\033[1;36m[saitenka doctor]\033[0m %s\n' "$(uname -srm)"

hdr "Toolchain"
for t in mpv ffmpeg uv; do
  if have "$t"; then ok "$t  $(command -v "$t")"; else er "$t missing (brew install $t)"; fi
done

printf '\n\033[1mSummary (toolchain):\033[0m \033[32m%d ok\033[0m · \033[33m%d warn\033[0m · \033[31m%d fail\033[0m\n' "$pass" "$warn" "$fail"

# Hand off to the overlay's own doctor (the authoritative, unit-tested checks).
if have saitenka-overlay; then
  hdr "Overlay (saitenka-overlay doctor)"
  saitenka-overlay doctor; ov=$?
else
  er "saitenka-overlay not installed — run install/install-macos.sh"; ov=1
fi

if [ "$fail" -eq 0 ] && [ "${ov:-0}" -eq 0 ]; then echo "Healthy ✅"; exit 0; else echo "Problems found — see ✗ above ❌"; exit 1; fi
