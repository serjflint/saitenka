#!/usr/bin/env python3
"""Convert py-spy `-f raw` output (Brendan-Gregg folded/collapsed stacks) to a speedscope JSON file.

py-spy's raw format is one line per unique stack: ``root;…;leaf <count>`` (frame names contain spaces
and parens, so the count is the LAST whitespace token). speedscope already imports folded stacks, but a
typed ``.speedscope.json`` opens with one click instead of a format guess. Stdlib only.

    python tools/folded_to_speedscope.py <in.raw> <out.speedscope.json> [--name LABEL]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def fold_to_speedscope(raw: str, name: str) -> dict:
    frame_index: dict[str, int] = {}
    frames: list[dict] = []
    samples: list[list[int]] = []
    weights: list[int] = []

    def idx(frame: str) -> int:
        i = frame_index.get(frame)
        if i is None:
            i = len(frames)
            frame_index[frame] = i
            frames.append({"name": frame})
        return i

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        stack, sep, count = line.rpartition(" ")
        if not sep or not count.isdigit():
            continue  # not a folded line
        # Folded is already root-first / leaf-last, which is speedscope's sample order.
        samples.append([idx(f) for f in stack.split(";") if f])
        weights.append(int(count))

    total = sum(weights)
    return {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "name": name,
        "activeProfileIndex": 0,
        "exporter": "saitenka folded_to_speedscope",
        "shared": {"frames": frames},
        "profiles": [
            {
                "type": "sampled",
                "name": name,
                "unit": "none",
                "startValue": 0,
                "endValue": total,
                "samples": samples,
                "weights": weights,
            }
        ],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw", type=Path, help="py-spy -f raw (folded stacks) input")
    ap.add_argument("out", type=Path, help="speedscope JSON output path")
    ap.add_argument("--name", default="", help="profile label (defaults to the input stem)")
    args = ap.parse_args(argv)

    if not args.raw.exists():
        print(f"folded_to_speedscope: no input at {args.raw}", file=sys.stderr)
        return 1
    doc = fold_to_speedscope(args.raw.read_text(encoding="utf-8"), args.name or args.raw.stem)
    if not doc["profiles"][0]["samples"]:
        print(f"folded_to_speedscope: {args.raw} had no folded stacks", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(doc), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
