"""Stage 17b: bundle builder + bootstrap-stub parse checks.

The stubs are dumb (~30 lines): get uv, install the wheel next to them, hand off to
`saitenka-overlay setup`. We parse-check them (``bash -n`` / shellcheck / ``pwsh``) where the
interpreter exists, and smoke the bundle builder's pure helpers. The full wheel build is gated behind
``SAITENKA_INSTALL_TEST=1`` (slow + disk-hungry), same as the install-test.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_DIR = REPO_ROOT / "install"
SH_STUB = INSTALL_DIR / "overlay-install.sh"
PS1_STUB = INSTALL_DIR / "overlay-install.ps1"


def _load_make_bundle():
    path = INSTALL_DIR / "make_bundle.py"
    spec = importlib.util.spec_from_file_location("make_bundle", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_stub_files_exist_and_reference_setup():
    assert SH_STUB.exists() and PS1_STUB.exists()
    sh = SH_STUB.read_text()
    ps1 = PS1_STUB.read_text()
    # the shell only bootstraps uv + installs the wheel + hands off to the Python wizard
    assert "uv tool install" in sh and "saitenka-overlay setup" in sh
    assert "uv tool install" in ps1 and "saitenka-overlay setup" in ps1
    # dry-run path exists in both
    assert "--dry-run" in sh
    assert "DryRun" in ps1


def test_sh_stub_parses_with_bash():
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    out = subprocess.run(["bash", "-n", str(SH_STUB)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_sh_stub_shellcheck_clean_if_available():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    out = subprocess.run(["shellcheck", str(SH_STUB)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_ps1_stub_parses_if_pwsh_available():
    if not shutil.which("pwsh"):
        pytest.skip("pwsh not available")
    # -DryRun means it must not perform installs; parse+dry-run in one shot
    cmd = [
        "pwsh",
        "-NoProfile",
        "-Command",
        f"& {{ . '{PS1_STUB}' -DryRun }}",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    # a clean parse + dry run exits 0 (no wheel present → it errors out, which is fine to tolerate);
    # what we assert is that PowerShell could PARSE the file (no ParserError token).
    assert "ParserError" not in (out.stdout + out.stderr), out.stdout + out.stderr


def test_bundle_helpers():
    mb = _load_make_bundle()
    assert mb._version()  # reads overlay/pyproject.toml
    assert "saitenka-overlay setup" in mb.INSTALL_TXT or "setup" in mb.INSTALL_TXT
    assert "bash overlay-install.sh" in mb.INSTALL_TXT


@pytest.mark.skipif(
    os.environ.get("SAITENKA_INSTALL_TEST") != "1",
    reason="set SAITENKA_INSTALL_TEST=1 to build the full bundle (slow, disk-hungry)",
)
def test_make_bundle_builds_zip(tmp_path):
    if shutil.disk_usage(REPO_ROOT).free < 1 * 1024**3:
        pytest.skip("insufficient free disk for the bundle build")
    mb = _load_make_bundle()
    zip_path = mb.make_bundle(out_dir=tmp_path)
    try:
        import zipfile

        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert any(n.endswith(".whl") for n in names)
        assert "overlay-install.sh" in names
        assert "overlay-install.ps1" in names
        assert "INSTALL.txt" in names
    finally:
        zip_path.unlink(missing_ok=True)
