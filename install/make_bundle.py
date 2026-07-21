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
DEINFLECT_DIR = REPO_ROOT / "deinflect"
INSTALL_DIR = REPO_ROOT / "install"

INSTALL_TXT = """Saitenka overlay — install

Unzip this archive, then run the installer for your OS:

  macOS / Linux:   bash overlay-install.sh
  Windows:         powershell -ExecutionPolicy Bypass -File overlay-install.ps1

The installer gets `uv` (and points you at mpv + ffmpeg if missing), installs the overlay from the
wheel next to it (with the JMdict English fallback, plus the GPL-3.0 deinflect add-on for inflection
chains — shipped as source in this bundle, so the install is GPL-3.0), and launches the setup wizard.
Then just:

  saitenka-overlay <video.mkv>

To preview without changing anything:  bash overlay-install.sh --dry-run
"""


def _build(project_dir: Path, out_dir: Path, fmt: str, glob: str) -> Path:
    """Run ``uv build <fmt>`` for ``project_dir`` into ``out_dir``; return the artifact it produced."""
    out_dir.mkdir(parents=True, exist_ok=True)
    before = set(out_dir.glob(glob))
    subprocess.run(["uv", "build", fmt, "--out-dir", str(out_dir)], cwd=project_dir, check=True)
    built = sorted(set(out_dir.glob(glob)) - before)
    if not built:
        raise RuntimeError(f"uv build {fmt} produced no {glob} for {project_dir}")
    return built[-1]


def build_wheel(project_dir: Path, out_dir: Path) -> Path:
    return _build(project_dir, out_dir, "--wheel", "*.whl")


def build_sdist(project_dir: Path, out_dir: Path) -> Path:
    return _build(project_dir, out_dir, "--sdist", "*.tar.gz")


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

    build_wheel(OVERLAY_DIR, staging)
    # Ship the GPL-3.0 deinflect add-on as an SDIST (source), not a wheel: GPLv3 §6 requires the
    # Corresponding Source when we convey the add-on, and the sdist IS that — every .py plus the
    # pyproject build scripts and LICENSE/NOTICE. The stub installs it via --with; the JMdict fallback
    # (jamdict) comes from PyPI via the stub's [jmdict] extra. Overlay stays a wheel (Apache-2.0 — no
    # source-provision duty). Together the bundle install mirrors the checkout installers' [full].
    if DEINFLECT_DIR.is_dir():
        build_sdist(DEINFLECT_DIR, staging)
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
