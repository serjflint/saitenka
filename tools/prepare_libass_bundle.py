"""Stage a vcpkg-built dynamic libass closure into the bundle package."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

BASELINE = "94a541197763a4f449a1b91478df48c0584a6256"


def _dynamic_library(path: Path) -> bool:
    name = path.name.casefold()
    return path.is_file() and (name.endswith((".dll", ".dylib", ".so")) or ".so." in name)


def _primary(paths: list[Path]) -> Path:
    by_name = {path.name.casefold(): path for path in paths}
    for name in ("libass.so.9", "libass.dylib", "ass.dll", "libass.dll", "libass-9.dll"):
        if name in by_name:
            return by_name[name]
    raise RuntimeError(f"could not identify canonical libass library in {paths}")


def _installed_versions(status: Path, triplet: str) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for paragraph in status.read_text(encoding="utf-8").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line)
        if (
            fields.get("Architecture") == triplet
            and "Package" in fields
            and "Version" in fields
            and "Feature" not in fields
        ):
            packages.append({"name": fields["Package"], "version": fields["Version"]})
    if not packages:
        raise RuntimeError("vcpkg status contains no target packages")
    return sorted(packages, key=lambda item: item["name"])


def prepare(install_root: Path, triplet: str, package: Path) -> dict[str, object]:
    installed = install_root / triplet
    search = installed / ("bin" if "windows" in triplet else "lib")
    libraries = sorted(path for path in search.iterdir() if _dynamic_library(path))
    if not libraries:
        raise RuntimeError(f"no dynamic libraries found under {search}")
    primary = _primary(libraries)
    payload = package / "src" / "libasslite_bundle" / ".libs"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    for library in libraries:
        shutil.copy2(library, payload / library.name, follow_symlinks=True)

    notices: list[str] = []
    for copyright_file in sorted((installed / "share").glob("*/copyright")):
        port = copyright_file.parent.name
        notices.append(
            f"===== {port} =====\n{copyright_file.read_text(errors='replace').rstrip()}\n"
        )
    if not notices:
        raise RuntimeError("vcpkg installation contains no dependency notices")
    (package / "THIRD_PARTY_LICENSES").write_text("\n".join(notices), encoding="utf-8")
    packages = _installed_versions(install_root / "vcpkg" / "status", triplet)
    sources = {
        "vcpkg_baseline": BASELINE,
        "registry": f"https://github.com/microsoft/vcpkg/tree/{BASELINE}/ports",
        "packages": packages,
        "rebuild": "vcpkg install --triplet <triplet> --x-install-root <install-root>",
    }
    (package / "NATIVE_SOURCES.json").write_text(
        json.dumps(sources, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest: dict[str, object] = {
        "library": f".libs/{primary.name}",
        "libass_version": "0.17.5",
        "vcpkg_baseline": BASELINE,
        "triplet": triplet,
        "files": [path.name for path in libraries],
        "notice_ports": [
            path.parent.name for path in sorted((installed / "share").glob("*/copyright"))
        ],
        "packages": packages,
    }
    manifest_path = package / "src" / "libasslite_bundle" / "native-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--triplet", required=True)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.install_root, args.triplet, args.package), indent=2))


if __name__ == "__main__":
    main()
