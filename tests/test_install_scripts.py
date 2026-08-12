"""Install-script parse checks.

The installers (`install/overlay-install.{sh,ps1}`, served at serjflint.github.io/saitenka) bootstrap
`uv`, install `saitenka[full]` from PyPI, and hand off to `saitenka setup`. We parse-check them
(``bash -n`` / shellcheck / ``pwsh``) where the interpreter exists.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_DIR = REPO_ROOT / "install"
SH_STUB = INSTALL_DIR / "overlay-install.sh"
PS1_STUB = INSTALL_DIR / "overlay-install.ps1"


def test_stub_files_exist_and_reference_setup():
    assert SH_STUB.exists() and PS1_STUB.exists()
    sh = SH_STUB.read_text()
    ps1 = PS1_STUB.read_text()
    # both bootstrap uv, install saitenka[full] from PyPI, and hand off to the Python wizard
    assert "uv tool install" in sh and "saitenka setup" in sh
    assert "uv tool install" in ps1 and "saitenka setup" in ps1
    # dry-run path exists in both
    assert "--dry-run" in sh
    assert "DryRun" in ps1


def test_sh_stub_parses_with_bash():
    if os.name == "nt":
        # The .sh installer targets POSIX; whatever `bash` a Windows runner has (Git Bash / WSL shim)
        # mangles the stub's output encoding and mis-parses it. The .ps1 stub is the Windows path.
        pytest.skip("shell installer is POSIX-only; Windows uses overlay-install.ps1")
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
    # a clean parse + dry run exits 0; what we assert is that PowerShell could PARSE the file (no
    # ParserError token).
    assert "ParserError" not in (out.stdout + out.stderr), out.stdout + out.stderr
