#!/usr/bin/env python3
"""Build ONE shareable self-contained bundle: ``saitenka-overlay-<ver>.zip`` (Stage 17b).

The zip contains the wheel (built with ``uv build`` — assets ride inside it via importlib.resources),
both bootstrap installers, and a short INSTALL.txt. A friend unzips it and runs the installer for
their OS; the installer finds the wheel NEXT TO ITSELF and does ``uv tool install ./<wheel>``.
PyPI later = the same installers with a package name instead of a local wheel (optional, not blocking).

Run from anywhere:  uv run install/make_bundle.py [--out DIR]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OVERLAY_DIR = REPO_ROOT / "overlay"
INSTALL_DIR = REPO_ROOT / "install"

INSTALL_TXT = """Saitenka overlay — install

Unzip this archive, then run the installer for your OS:

  macOS / Linux:   bash overlay-install.sh
  Windows:         powershell -ExecutionPolicy Bypass -File overlay-install.ps1

The installer gets `uv` (and points you at mpv + ffmpeg if missing), installs the overlay from the
wheel next to it, and launches the setup wizard. Then just:

  saitenka-overlay <video.mkv>

To preview without changing anything:  bash overlay-install.sh --dry-run
"""


def build_wheel(out_dir: Path) -> Path:
    """Build the overlay wheel into ``out_dir`` and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=OVERLAY_DIR,
        check=True,
    )
    wheels = sorted(out_dir.glob("*.whl"))
    if not wheels:
        raise RuntimeError("uv build produced no wheel")
    return wheels[-1]


def _version() -> str:
    import tomllib

    data = tomllib.loads((OVERLAY_DIR / "pyproject.toml").read_text())
    return data["project"]["version"]


def make_bundle(out_dir: Path | None = None) -> Path:
    """Build the wheel and assemble the shareable zip. Returns the zip path."""
    out_dir = out_dir or (REPO_ROOT / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "_bundle"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    wheel = build_wheel(staging)
    for stub in ("overlay-install.sh", "overlay-install.ps1"):
        shutil.copy2(INSTALL_DIR / stub, staging / stub)
    (staging / "INSTALL.txt").write_text(INSTALL_TXT, encoding="utf-8")

    zip_path = out_dir / f"saitenka-overlay-{_version()}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(staging.iterdir()):
            if f.name.startswith("."):  # drop uv build's stray dist/.gitignore etc.
                continue
            zf.write(f, f.name)
    shutil.rmtree(staging)
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser(description="build the shareable saitenka-overlay bundle")
    ap.add_argument("--out", type=Path, default=None, help="output dir (default: <repo>/dist)")
    args = ap.parse_args()
    zip_path = make_bundle(args.out)
    print(f"bundle: {zip_path} ({zip_path.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
