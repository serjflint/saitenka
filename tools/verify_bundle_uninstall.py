#!/usr/bin/env python3
"""Snapshot and verify ownership of an installed libasslite-bundle wheel."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path


def snapshot(output: Path) -> None:
    distribution = importlib.metadata.distribution("libasslite-bundle")
    files = distribution.files
    if not files:
        raise RuntimeError("installed bundle has no RECORD files")
    owned = [Path(str(distribution.locate_file(file))).resolve() for file in files]
    native = [
        path
        for path in owned
        if path.suffix.casefold() in {".dll", ".dylib", ".so"} or ".so." in path.name.casefold()
    ]
    if not native:
        raise RuntimeError("installed bundle RECORD has no native payload")
    roots = sorted(
        {
            parent
            for path in owned
            for parent in path.parents
            if parent.name.startswith("libasslite_bundle")
        }
    )
    output.write_text(
        json.dumps(
            {"files": [str(path) for path in owned], "roots": [str(path) for path in roots]}
        ),
        encoding="utf-8",
    )


def verify(output: Path) -> None:
    manifest = json.loads(output.read_text(encoding="utf-8"))
    survivors = [
        path for value in (*manifest["files"], *manifest["roots"]) if (path := Path(value)).exists()
    ]
    if survivors:
        raise RuntimeError(f"bundle uninstall left owned paths: {survivors}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("snapshot", "verify"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    (snapshot if args.mode == "snapshot" else verify)(args.output)


if __name__ == "__main__":
    main()
