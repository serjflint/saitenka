"""Bounded local ASS/SRT reduction. A checker must reserve exit 1 for the same diagnosed defect."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

import pysubs2
from character_masks import clusters

from saitenka.app.report_reader import read_file


def minimize(source, suffix, check, *, max_checks=128):
    if not 2 <= max_checks <= 1000:
        raise ValueError("max_checks must be between 2 and 1000")
    calls = 0

    def fails(candidate):
        nonlocal calls
        if calls >= max_checks:
            return False
        calls += 1
        verdict = check(candidate)
        if verdict not in {"passed", "failed"}:
            raise ValueError("checker is inconclusive; reduction cannot certify the failure")
        return verdict == "failed"

    if not fails(source):
        raise ValueError("input does not reproduce the required failure")
    document = pysubs2.SSAFile.from_string(source, format_=suffix)
    normalized = document.to_string(suffix)
    if not fails(normalized):
        raise ValueError("subtitle serialization changes the failure; preserve original input")
    # Greedy, budgeted reduction: no claim of a globally minimal counterexample.
    index = 0
    while index < len(document.events) and calls < max_checks:
        candidate = deepcopy(document)
        del candidate.events[index]
        if candidate.events and fails(candidate.to_string(suffix)):
            document = candidate
        else:
            index += 1
    for index, event in enumerate(document.events):
        # Override syntax requires its own grammar; never cut through an ASS tag or escape.
        if "{" in event.text or "\\" in event.text:
            continue
        position = 0
        while position < len(event.text) and calls < max_checks:
            start, end = next(pair for pair in clusters(event.text) if pair[0] >= position)
            candidate = deepcopy(document)
            candidate.events[index].text = event.text[:start] + event.text[end:]
            if candidate.events[index].text and fails(candidate.to_string(suffix)):
                document = candidate
                event = document.events[index]
            else:
                position = end
    return document.to_string(suffix), {
        "checks": calls,
        "budget_exhausted": calls >= max_checks,
        "scope": "greedy failure-preserving reduction; not globally minimal or safe to share",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output", type=Path, required=True, help="new local directory; output remains sensitive"
    )
    parser.add_argument("--max-checks", type=int, default=128)
    parser.add_argument(
        "--checker",
        nargs=argparse.REMAINDER,
        required=True,
        help="command plus arguments; {input} is replaced by candidate path; 0=passed, 1=same defect, other=inconclusive",
    )
    args = parser.parse_args()
    suffix = args.source.suffix.lstrip(".").lower()
    if suffix not in {"ass", "srt"} or not any("{input}" in part for part in args.checker):
        parser.error("ASS/SRT input and checker with {input} required")
    source = read_file(args.source, limit=8 * 1024 * 1024)
    if args.output.exists():
        parser.error("output directory already exists; choose a new export destination")
    with tempfile.TemporaryDirectory(prefix="saitenka-minimize-") as scratch:
        path = Path(scratch) / f"candidate.{suffix}"

        def check(candidate):
            path.write_text(candidate, encoding="utf-8")
            result = subprocess.run(
                [part.replace("{input}", str(path)) for part in args.checker],
                check=False,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {0: "passed", 1: "failed"}.get(result.returncode, "inconclusive")

        reduced, result = minimize(source, suffix, check, max_checks=args.max_checks)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / f"reduced.{suffix}").write_text(reduced, encoding="utf-8")
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
