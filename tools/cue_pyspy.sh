#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_PROJECT_ENVIRONMENT=/tmp/venv-py-spy
uv sync --python 3.13 --extra full --group profiling
exec uv run --python 3.13 --no-sync python examples/bench_cue_replay.py --profile "$@"
