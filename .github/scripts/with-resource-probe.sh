#!/usr/bin/env bash
# Run a command with a resource probe streaming into the same step's log.
#
# The free-threaded Linux jobs die as `##[error]The runner has received a shutdown signal.` — the
# runner agent, not our process. Nothing survives that: no summary, no post-step, no `if: always()`.
# So the evidence has to already be in the log when it happens, which is what streaming one line
# every INTERVAL buys: the last `[probe]` line is the machine's state at the moment it died.
#
# Linux only (reads /proc); callers gate on `runner.os == 'Linux'`.
set -uo pipefail

INTERVAL="${PROBE_INTERVAL_S:-15}"

probe() {
  while :; do
    printf '[probe] mem_avail_mb=%s swap_free_mb=%s load=%s procs=%s\n' \
      "$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)" \
      "$(awk '/^SwapFree:/{print int($2/1024)}' /proc/meminfo)" \
      "$(cut -d' ' -f1-3 /proc/loadavg)" \
      "$(awk '{print $4}' /proc/loadavg)"
    sleep "$INTERVAL"
  done
}

probe &
probe_pid=$!
trap 'kill "$probe_pid" 2>/dev/null || true' EXIT

"$@"
