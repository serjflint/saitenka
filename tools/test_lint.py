"""Inspect test-lint findings without dumping the repository-wide ast-grep payload.

uv run python tools/test_lint.py --file tests/test_controller.py
uv run python tools/test_lint.py --rule test-sleep-polling --summary
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from tool_json import run_json


def select_hits(
    hits: list[dict], *, files: set[str] | None = None, rules: set[str] | None = None
) -> list[dict]:
    """Return findings matching every supplied exact filter."""
    return [
        hit
        for hit in hits
        if (not files or hit.get("file") in files) and (not rules or hit.get("ruleId") in rules)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file", action="append", dest="files", help="exact repo-relative test file"
    )
    parser.add_argument("--rule", action="append", dest="rules", help="exact ast-grep rule id")
    parser.add_argument(
        "--summary", action="store_true", help="print counts by rule instead of findings"
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    targets = args.files or ["tests"]
    hits = run_json(
        ["ast-grep", "scan", "-c", "sgconfig-tests.yml", "--json=compact", *targets],
        args.repo,
        list,
    )
    selected = select_hits(
        hits,
        files=set(args.files) if args.files else None,
        rules=set(args.rules) if args.rules else None,
    )
    output: object = (
        dict(sorted(Counter(hit["ruleId"] for hit in selected).items()))
        if args.summary
        else selected
    )
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
