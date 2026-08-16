#!/usr/bin/env python3
"""Verify every bundled dylib honors the advertised macOS deployment target."""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

_MINOS = re.compile(r"^\s*minos\s+(\d+)\.(\d+)", re.MULTILINE)


def parse_minos(output: str) -> tuple[int, int]:
    match = _MINOS.search(output)
    if match is None:
        raise RuntimeError("vtool output has no minos field")
    return int(match.group(1)), int(match.group(2))


def verify(wheel: Path, target: tuple[int, int]) -> None:
    with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(wheel) as archive:
        dylibs = [name for name in archive.namelist() if name.endswith(".dylib")]
        if not dylibs:
            raise RuntimeError("wheel contains no dylibs")
        archive.extractall(directory)
        for name in dylibs:
            result = subprocess.run(
                ["vtool", "-show-build", str(Path(directory) / name)],
                check=True,
                capture_output=True,
                text=True,
            )
            actual = parse_minos(result.stdout)
            if actual > target:
                raise RuntimeError(f"{name} requires macOS {actual}, advertised {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    verify(args.wheel, tuple(int(part) for part in args.target.split(".", 1)))


if __name__ == "__main__":
    main()
