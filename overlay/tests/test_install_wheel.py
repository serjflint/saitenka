"""Stage 17a install-test: the built wheel must carry its assets and run when installed standalone.

Builds a wheel with ``uv build``, installs it into a throwaway ``uv venv`` (isolated from the source
tree), and checks (a) ``saitenka --help`` works and (b) the bundled assets load via
``importlib.resources`` from the INSTALLED package — proving N3 packaging. Slow + disk-hungry, so it
is opt-in (``SAITENKA_INSTALL_TEST=1``) and always cleans up its wheel + venv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SAITENKA_INSTALL_TEST") != "1",
    reason="set SAITENKA_INSTALL_TEST=1 to run the wheel build+install test (slow, disk-hungry)",
)

PROJECT = Path(__file__).resolve().parent.parent


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def test_wheel_installs_and_assets_load():
    if _free_bytes(PROJECT) < 2 * 1024**3:  # need headroom for the venv + deps
        pytest.skip("insufficient free disk for the install test")
    work = Path(tempfile.mkdtemp(prefix="saitenka-install-"))
    try:
        # 1. build the wheel into an isolated dir
        dist = work / "dist"
        for project in (
            PROJECT.parent / "yomitanlite",
            PROJECT.parent / "ankiconnect-client",
            PROJECT,
        ):
            subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(dist)],
                cwd=project,
                check=True,
                capture_output=True,
                text=True,
            )
        wheels = list(dist.glob("saitenka-*.whl"))
        assert wheels, "uv build produced no wheel"
        wheel = wheels[0]

        # 2. install into a throwaway venv (isolated from the source checkout)
        venv = work / "venv"
        subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True, text=True)
        py = venv / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(py),
                "--find-links",
                str(dist),
                str(wheel),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        # 3. --help works from the installed console script (run OUTSIDE the source tree)
        script = venv / ("Scripts" if sys.platform == "win32" else "bin") / "saitenka"
        out = subprocess.run(
            [str(script), "--help"], cwd=work, capture_output=True, text=True, timeout=120
        )
        assert out.returncode == 0, out.stderr
        assert "saitenka" in out.stdout

        # 4. assets load from the INSTALLED package (importlib.resources), not the source tree
        smoke = (
            "import json;"
            "from importlib.resources import files;"
            "from overlay.resources import asset;"
            "from yomitanlite.parity import _assert_pinned_revision;"
            "assert asset('fonts','NotoSansJP.ttf').exists();"
            "assert asset('wordlists','jlpt.zip').exists();"
            "assert asset('saitenka.lua').exists();"
            "pkg=files('yomitanlite');"
            "assert pkg.joinpath('yomitan_oracle.mjs').is_file();"
            "lock=json.loads(pkg.joinpath('upstream-lock.json').read_text(encoding='utf-8'));"
            "_assert_pinned_revision(lock['yomitan']);"
            "print('assets-ok')"
        )
        out2 = subprocess.run(
            [str(py), "-c", smoke], cwd=work, capture_output=True, text=True, timeout=120
        )
        assert out2.returncode == 0, out2.stderr
        assert "assets-ok" in out2.stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)
