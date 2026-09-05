#!/bin/sh
set -eu
skill_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo_dir=$(CDPATH= cd -- "$skill_dir/../../.." && pwd)
bash "$repo_dir/.agents/grow/scripts/smoke.sh"
