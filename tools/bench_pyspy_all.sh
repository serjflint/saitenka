#!/usr/bin/env bash
# Run every py-spy benchmark end-to-end, unattended: prepare the profiling venv, elevate to root ONCE
# (py-spy must attach as root on macOS), then for each benchmark record a py-spy CPU profile AND a
# per-span latency-percentile report (tooltip open delay, scroll frame, sub-seek, …).
#
# The *-pyspy poe tasks only print a sudo line for you to paste one at a time — this drives all of
# them in one pass. It wraps each run in a telemetry bootstrap so the CTF span trace exists to compute
# percentiles from.
#
#   tools/bench_pyspy_all.sh                 # all benchmarks (prompts for the password once)
#   tools/bench_pyspy_all.sh scroll-jank     # just one (or several) by name
#   tools/bench_pyspy_all.sh --list          # list the benchmark names
#
# Also correct when the whole script is already root (`sudo poe bench-pyspy-all` / `sudo …/*.sh`): it
# runs `uv sync` and reads ~ AS $SUDO_USER (no root-owned venv, no /var/root cache), elevates only
# py-spy, and chowns outputs back — so no password is asked mid-run.
#
# Outputs land in /tmp/saitenka-bench-<timestamp>/:
#   run.log            everything printed this run (bench reports + py-spy + span tables)
#   <name>/output.log  that one benchmark's full output, isolated for analysis
#   <name>/spans.txt   just its per-span percentile table
#   <name>.raw         its py-spy folded-stack profile (flamegraph.pl / imports into speedscope)
#   <name>.speedscope.json  the same profile, typed for one-click open at https://speedscope.app
# py-spy is pinned to CPython 3.13 — it cannot introspect the free-threaded 3.14t build
# (benfred/py-spy#460), so this is a separate GIL-build venv.
set -euo pipefail

VENV="${SAITENKA_PYSPY_VENV:-/tmp/venv-py-spy}"
OVERLAY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORTER="$OVERLAY/tools/span_percentiles.py"
SPEEDSCOPE="$OVERLAY/tools/folded_to_speedscope.py"
BENCH="examples/bench_responsiveness.py"  # relative to $OVERLAY (bench reads other relative paths)

# --- privilege model: work as the real user, elevate ONLY py-spy ------------------------------------
# Two entry styles must both be correct: run as the user (we `sudo` py-spy), or the whole script already
# root via `sudo poe` (we drop back to $SUDO_USER for venv/cache/HOME and run py-spy directly).
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
  REAL_USER="$SUDO_USER"
  USER_HOME="$(eval echo "~$SUDO_USER")"
  asuser() { sudo -u "$REAL_USER" -H "$@"; }  # run venv-sync / reporter as the real user
  ELEVATE=()                                   # already root — py-spy needs no sudo
  NEED_SUDO_CACHE=0
else
  REAL_USER="$(id -un)"
  USER_HOME="$HOME"
  asuser() { "$@"; }
  if [[ $EUID -eq 0 ]]; then ELEVATE=(); NEED_SUDO_CACHE=0; else ELEVATE=(sudo); NEED_SUDO_CACHE=1; fi
fi

# name|rate|bench-args  — mirrors the six *-pyspy poe tasks (see pyproject.toml).
BENCHES=(
  "stress|200|--stress --reps 3"
  "vocab|200|--vocab --reps 3"
  "vocab-mp|200|--vocab --parallel --reps 3"
  "timeline|500|--timeline --timeline-cues 120 --timeline-head-prefetch 1 --timeline-lookahead 2"
  "scroll-jank|200|--scroll-jank --reps 3"
  "clicks|500|--clicks --reps 200"  # sidebar_click / backlog_write / mined_store_write spans
  "trace|500|__TRACE__"  # args resolved at runtime from the newest saitenka report bundle
)

names() { for b in "${BENCHES[@]}"; do echo "${b%%|*}"; done; }
if [[ "${1:-}" == "--list" ]]; then names; exit 0; fi
WANT=("$@")  # empty ⇒ all

want() {
  [[ ${#WANT[@]} -eq 0 ]] && return 0
  local n; for n in "${WANT[@]}"; do [[ "$n" == "$1" ]] && return 0; done; return 1
}

cd "$OVERLAY"
command -v uv >/dev/null || { echo "uv not found — this repo requires uv" >&2; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
OUT="/tmp/saitenka-bench-$TS"
asuser mkdir -p "$OUT"

run_one() {
  local name="$1" rate="$2" args="$3"
  local bench_out="$OUT/$name" tdir="$OUT/$name/telemetry" raw="$OUT/$name.raw"
  asuser mkdir -p "$tdir"
  chmod 777 "$bench_out" "$tdir"  # root py-spy writes the trace here; the reporter reads it back

  # One tee captures this benchmark's whole section to <name>/output.log; the passthrough still flows up
  # to the run-wide tee (run.log). $args is an intentional word list (SC2086); ${ELEVATE[@]+…} expands
  # safely when empty — bash 3.2 (macOS /usr/bin/bash) errors on "${arr[@]}" of an empty array under -u.
  {
    echo
    echo "======================================================================"
    echo "==> $name   (py-spy --rate $rate)   args: $args"
    echo "======================================================================"

    # shellcheck disable=SC2086
    ${ELEVATE[@]+"${ELEVATE[@]}"} env "BENCH_TRACE_DIR=$tdir" "BENCH_SCRIPT=$BENCH" \
      "$VENV/bin/py-spy" record --subprocesses -o "$raw" -f raw --rate "$rate" -- \
      "$VENV/bin/python" "$BOOT" $args \
      || echo "!! $name: py-spy/bench exited non-zero (profile may be partial)" >&2

    local trace
    trace="$(ls -t "$tdir"/trace-*.json 2>/dev/null | head -1 || true)"
    if [[ -n "$trace" ]]; then
      asuser "$VENV/bin/python" "$REPORTER" "$trace" --label "$name" | tee "$bench_out/spans.txt"
    else
      echo "   (no telemetry trace produced for $name — spans unavailable)" | tee "$bench_out/spans.txt"
    fi

    # py-spy raw IS folded stacks (imports into speedscope as-is); also emit a typed .speedscope.json.
    if [[ -s "$raw" ]]; then
      asuser "$VENV/bin/python" "$SPEEDSCOPE" "$raw" "$OUT/$name.speedscope.json" --name "$name" \
        && echo "   speedscope: $OUT/$name.speedscope.json  (open at https://speedscope.app)"
    fi
    echo "   profile: $raw"
  } 2>&1 | tee "$bench_out/output.log"
}

resolve_trace_args() {
  # The --trace mode replays the newest diagnostics bundle; skip cleanly if there's none. Uses the REAL
  # user's home (not /var/root) so it works under `sudo poe` too.
  local zip
  zip="$(ls -t "$USER_HOME/.local/share/saitenka/reports"/*.zip 2>/dev/null | head -1 || true)"
  [[ -z "$zip" ]] && return 1
  echo "--trace $zip --idle-scale 0.05 --trace-loops 5"
}

main() {
  echo "==> preparing profiling venv at $VENV (CPython 3.13 + full + profiling group)"
  asuser env "UV_PROJECT_ENVIRONMENT=$VENV" uv sync --python 3.13 --extra full --group profiling -q

  # Telemetry bootstrap: configure() spans BEFORE running the bench (the bench never enables telemetry
  # itself), pin the trace dir so we know where it lands, then run the bench's real __main__.
  asuser tee "$BOOT" >/dev/null <<'PY'
import os, runpy, sys
from saitenka.app.config import TelemetryOptions
from saitenka.app.telemetry import configure, shutdown

configure(TelemetryOptions(enabled=True, export_dir=os.environ["BENCH_TRACE_DIR"], sample_hot_path=1.0))
sys.argv = ["bench_responsiveness.py", *sys.argv[1:]]
try:
    runpy.run_path(os.environ["BENCH_SCRIPT"], run_name="__main__")
finally:
    shutdown()  # flush the CTF span writer before exit
PY

  if [[ $NEED_SUDO_CACHE -eq 1 ]]; then
    echo "==> caching sudo credentials (py-spy attaches as root)"
    sudo -v  # prompts on the tty; unaffected by stdout being tee'd
    # Keep the sudo timestamp warm for the whole run so no benchmark stalls waiting on a re-prompt.
    ( while kill -0 "$MAIN_PID" 2>/dev/null; do sudo -n true; sleep 50; done ) &
    trap 'kill %1 2>/dev/null || true' EXIT
  fi

  local spec name rate args
  for spec in "${BENCHES[@]}"; do
    IFS='|' read -r name rate args <<<"$spec"
    want "$name" || continue
    if [[ "$args" == "__TRACE__" ]]; then
      if ! args="$(resolve_trace_args)"; then
        echo "==> trace: skipped (no report bundle in $USER_HOME/.local/share/saitenka/reports)"
        continue
      fi
    fi
    run_one "$name" "$rate" "$args"
  done

  echo
  echo "==> done. Results in $OUT"
  echo "    combined log:          $OUT/run.log"
  echo "    per-span percentiles:  $OUT/<name>/spans.txt   (full: $OUT/<name>/output.log)"
  echo "    py-spy profiles:       $OUT/<name>.raw  +  $OUT/<name>.speedscope.json (speedscope.app)"
}

BOOT="$OUT/_boot.py"
MAIN_PID=$$  # keepalive checks the top-level pid (main runs in the tee pipeline's subshell)
main 2>&1 | tee "$OUT/run.log"

# Hand the whole run (root-written .raw / traces / logs) back to the real user in one pass.
${ELEVATE[@]+"${ELEVATE[@]}"} chown -R "$REAL_USER" "$OUT" 2>/dev/null || true
