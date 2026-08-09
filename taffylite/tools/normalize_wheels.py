#!/usr/bin/env python3
"""Rewrite each wheel's ZIP container to strip trailing data after the end-of-central-directory record.

maturin's Windows wheels (v1.14.1) carry bytes past the ZIP central directory; Warehouse (PyPI /
TestPyPI) rejects them with `400 Invalid distribution file. ZIP archive not accepted: Trailing data`
(Linux/macOS wheels are clean). Rewriting the archive member-by-member preserves every file's content
(so `.dist-info/RECORD` hashes stay valid) and per-member compression + attributes, and yields a clean
archive with no trailing bytes. Idempotent — a no-op on already-clean wheels; leaves sdists untouched.

    python3 tools/normalize_wheels.py <dir>   # default dir: dist
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path


def normalize(whl: Path) -> None:
    tmp = whl.with_name(whl.name + ".tmp")
    # writestr(info, data) keeps each member's name, timestamp, external attrs and compress_type, so
    # only the container is rebuilt — the stored bytes (and thus RECORD's sha256/size) are unchanged.
    with zipfile.ZipFile(whl) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info.filename))
    os.replace(tmp, whl)


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = sorted(root.glob("*.whl"))
    for whl in wheels:
        normalize(whl)
        print(f"normalized {whl.name}")
    print(f"done — {len(wheels)} wheel(s) in {root}")


if __name__ == "__main__":
    main()
