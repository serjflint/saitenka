#!/usr/bin/env python3
"""Install-bootstrap smoke: install saitenka the way a user does (`uv tool install`), then exercise
the first-run CLI surface (`--help`, `setup --dry-run`, `doctor --json`).

This is the one thing the unit / integration / GUI tiers never touch: the packaged console script on a
clean machine — assets loading from the INSTALLED wheel via importlib.resources, PATH handoff, and the
setup wizard's non-tty path. `test_install_wheel.py` installs into a throwaway venv; this drives the
real `uv tool install` entrypoint the installer script uses.

Two sources:
  --source wheel  (default): build a wheel from the repository root — validates the CURRENT tree's
                  packaging without a published release (PR / dispatch / weekly).
  --source pypi:  install `saitenka[full]` from PyPI — validates the PUBLISHED artifact (release tag).

Oracle (precise, not a flaky global exit code): doctor's `fonts` check must be `ok` — it proves the
bundled font assets shipped in the wheel and load from the installed package. `--expect-tools NAME…`
additionally asserts those doctor checks are `ok` (the Linux leg apt-installs mpv+ffmpeg, so it can
demand mpv/ffmpeg green). doctor's global exit code is ignored on purpose — a clean box legitimately
fails `mpv`/`ffmpeg` (the `setup` wizard installs them) — BUT the failures must be a subset of that
expected set (`ALLOWED_FAILS`), so a genuine packaging regression that produces a *new* fail is caught
rather than silently tolerated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

OVERLAY = Path(__file__).resolve().parent.parent
EXE_NAME = "saitenka.exe" if os.name == "nt" else "saitenka"
# The only doctor checks allowed to `fail` on a clean box: external runtime tools a bare install lacks
# (the `setup` wizard installs them). Any OTHER fail is a real regression, not an expected clean state.
ALLOWED_FAILS = {"mpv", "ffmpeg"}


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    # Force UTF-8: `text=True` alone decodes with the platform locale (cp1252 on Windows), which chokes
    # on the CLI's non-ASCII help/doctor output (UnicodeDecodeError → stdout=None). Same trap as the
    # repo's subprocess-utf8-encoding ast-grep rule.
    return subprocess.run(cmd, encoding="utf-8", errors="replace", capture_output=True, **kw)


def _build_wheel() -> Path:
    dist = OVERLAY / "dist"
    for project in (
        OVERLAY / "saitenka-dict",
        OVERLAY / "ankiconnect-client",
        OVERLAY,
    ):
        out = _run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=project)
        if out.returncode != 0:
            sys.exit(f"uv build failed for {project.name}:\n{out.stdout}\n{out.stderr}")
    wheels = sorted(dist.glob("saitenka-*.whl"))
    if not wheels:
        sys.exit("uv build produced no saitenka wheel")
    return wheels[-1]


def _install(source: str) -> None:
    if source == "pypi":
        spec = "saitenka[full]"
    else:
        wheel = _build_wheel()
        # PEP 508 direct reference: install the LOCAL wheel with the [full] extras (deinflect / jmdict /
        # telemetry resolve from the index as normal deps). `as_uri()` yields a valid file URL on Windows too.
        spec = f"saitenka[full] @ {wheel.as_uri()}"
    command = ["uv", "tool", "install", "--reinstall"]
    if source == "wheel":
        command.extend(("--find-links", str(wheel.parent)))
    out = _run([*command, spec])
    if out.returncode != 0:
        sys.exit(f"uv tool install failed:\n{out.stdout}\n{out.stderr}")


def _resolve_exe() -> Path:
    bindir = _run(["uv", "tool", "dir", "--bin"]).stdout.strip()
    if bindir and (exe := Path(bindir) / EXE_NAME).exists():
        return exe
    sys.exit(f"installed `{EXE_NAME}` not found under `uv tool dir --bin` ({bindir!r})")


def _check_help(exe: Path) -> None:
    out = _run([str(exe), "--help"], cwd=OVERLAY)
    if out.returncode != 0 or "saitenka" not in out.stdout.lower():
        sys.exit(f"`saitenka --help` failed (rc={out.returncode}):\n{out.stdout}\n{out.stderr}")


def _check_setup_dry_run(exe: Path) -> None:
    # The wizard the installer hands off to, driven non-interactively (--yes) and touching nothing
    # (--dry-run). Exercises the isatty()==False path in setup_wizard.
    out = _run([str(exe), "setup", "--yes", "--dry-run"], cwd=OVERLAY)
    if out.returncode != 0:
        sys.exit(
            f"`saitenka setup --yes --dry-run` failed (rc={out.returncode}):\n{out.stdout}\n{out.stderr}"
        )


def _check_doctor(exe: Path, expect_ok: list[str]) -> None:
    out = _run(
        [str(exe), "doctor", "--json"], cwd=OVERLAY.parent
    )  # exit code ignored (mpv-absent → fail)
    try:
        report = json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.exit(f"`doctor --json` did not emit valid JSON:\n{out.stdout}\n{out.stderr}")
    checks = report.get("checks", [])
    summary = report.get("summary", {})
    if not checks or set(summary) != {"ok", "warn", "fail"} or sum(summary.values()) != len(checks):
        sys.exit(f"doctor report malformed: summary={summary} n_checks={len(checks)}")
    status = {c["name"]: c["status"] for c in checks}
    # `fonts` ok ⇒ the bundled assets shipped in the wheel and load from the installed package.
    for name in ["fonts", *expect_ok]:
        if status.get(name) != "ok":
            sys.exit(f"doctor `{name}` expected ok, got {status.get(name)!r}\nfull: {status}")
    failed = sorted({c["name"] for c in checks if c["status"] == "fail"})
    unexpected = [n for n in failed if n not in ALLOWED_FAILS]
    if unexpected:
        sys.exit(
            f"doctor has unexpected failure(s) beyond the clean-box mpv/ffmpeg: {unexpected}\nfull: {status}"
        )
    print(
        f"doctor structural check PASSED — {summary}; asserted ok={['fonts', *expect_ok]}; "
        f"expected clean-box fails={failed or 'none'}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=("wheel", "pypi"), default="wheel")
    ap.add_argument(
        "--expect-tools",
        nargs="*",
        default=[],
        metavar="CHECK",
        help="doctor check names that must be ok (e.g. mpv ffmpeg on a leg that installs them)",
    )
    a = ap.parse_args()

    _install(a.source)
    exe = _resolve_exe()
    print(f"installed entrypoint: {exe}")
    _check_help(exe)
    _check_setup_dry_run(exe)
    _check_doctor(exe, a.expect_tools)
    print("install-smoke PASSED")


if __name__ == "__main__":
    main()
