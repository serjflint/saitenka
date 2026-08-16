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
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SAITENKA_INSTALL_TEST") != "1",
    reason="set SAITENKA_INSTALL_TEST=1 to run the wheel build+install test (slow, disk-hungry)",
)

PROJECT = Path(__file__).resolve().parent.parent


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _assert_oracle_absent(members: list[str]) -> None:
    forbidden_names = {"oracle.py", "parity.py", "upstream-lock.json", "yomitan_oracle.mjs"}
    leaked = [
        member
        for member in members
        if "oracle" in PurePosixPath(member).parts or PurePosixPath(member).name in forbidden_names
    ]
    assert leaked == []


def test_wheel_installs_and_assets_load():
    if _free_bytes(PROJECT) < 2 * 1024**3:  # need headroom for the venv + deps
        pytest.skip("insufficient free disk for the install test")
    work = Path(tempfile.mkdtemp(prefix="saitenka-install-"))
    try:
        # 1. build the wheel into an isolated dir
        dist = work / "dist"
        dictionary_project = PROJECT / "saitenka-dict"
        for project in (
            dictionary_project,
            PROJECT / "ankiconnect-client",
            PROJECT / "libasslite",
            PROJECT,
        ):
            build_kind = [] if project == dictionary_project else ["--wheel"]
            subprocess.run(
                ["uv", "build", *build_kind, "--out-dir", str(dist)],
                cwd=project,
                check=True,
                capture_output=True,
                text=True,
            )
        dictionary_wheel = next(dist.glob("saitenka_dict-*.whl"))
        dictionary_sdist = next(dist.glob("saitenka_dict-*.tar.gz"))
        with zipfile.ZipFile(dictionary_wheel) as archive:
            _assert_oracle_absent(archive.namelist())
            assert "saitenka_dict/py.typed" in archive.namelist()
        with tarfile.open(dictionary_sdist, "r:gz") as archive:
            _assert_oracle_absent(archive.getnames())
        anki_wheel = next(dist.glob("ankiconnect_client-*.whl"))
        with zipfile.ZipFile(anki_wheel) as archive:
            assert "ankiconnect_client/py.typed" in archive.namelist()
        wheels = list(dist.glob("saitenka-*.whl"))
        assert wheels, "uv build produced no wheel"
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            assert "saitenka/py.typed" in archive.namelist()
            assert not any(
                name.casefold().endswith((".dll", ".dylib", ".so")) or ".so." in name.casefold()
                for name in archive.namelist()
            ), "the Apache-2.0 Saitenka wheel must not absorb the native bundle"

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
                f"saitenka[subtitle-geometry] @ {wheel.as_uri()}",
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
            "import importlib.util;"
            "from importlib.resources import files;"
            "from saitenka.resources import asset;"
            "assert asset('fonts','NotoSansJP.ttf').exists();"
            "assert asset('wordlists','jlpt.zip').exists();"
            "assert asset('saitenka.lua').exists();"
            "import saitenka.subtitles;"
            "import libasslite;"
            "assert files('saitenka').joinpath('py.typed').is_file();"
            "pkg=files('saitenka_dict');"
            "assert importlib.util.find_spec('oracle') is None;"
            "assert not pkg.joinpath('oracle.py').is_file();"
            "assert not pkg.joinpath('parity.py').is_file();"
            "assert not pkg.joinpath('yomitan_oracle.mjs').is_file();"
            "assert not pkg.joinpath('upstream-lock.json').is_file();"
            "print('assets-ok')"
        )
        out2 = subprocess.run(
            [str(py), "-c", smoke], cwd=work, capture_output=True, text=True, timeout=120
        )
        assert out2.returncode == 0, out2.stderr
        assert "assets-ok" in out2.stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)
