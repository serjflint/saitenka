#!/bin/sh
# Protocol specimen: deliberately knows only the transport smoke scenario.
set -eu
while IFS= read -r line; do
  case "$line" in
    *'"expect"'*)
      echo 'answer key leaked into adapter request' >&2
      exit 2
      ;;
    '{"protocol":"saitenka.session/v0","requires":["session-v0"],"type":"hello"}')
      printf '%s\n' '{"capabilities":["session-v0"],"protocol":"saitenka.session/v0","type":"hello"}'
      ;;
    *'"scenario_id":"protocol-smoke"'*)
      printf '%s\n' '{"observations":[{"kind":"checkpoint","name":"ready","source":"driver"}],"protocol":"saitenka.session/v0","scenario_id":"protocol-smoke","status":"ok","type":"result"}'
      ;;
    *)
      echo 'unsupported request' >&2
      exit 3
      ;;
  esac
done
