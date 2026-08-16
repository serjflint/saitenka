#!/usr/bin/env python3
"""Archive the exact vcpkg source inputs and port patches used by a bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


def _files(vcpkg_root: Path, package: Path, triplet: str) -> list[tuple[Path, str]]:
    sources = json.loads((package / "NATIVE_SOURCES.json").read_text(encoding="utf-8"))
    package_names = sorted({item["name"] for item in sources["packages"]})
    entries = [
        (package / "NATIVE_SOURCES.json", "NATIVE_SOURCES.json"),
        (package / "vcpkg.json", "vcpkg.json"),
    ]
    triplet_file = package / "triplets" / f"{triplet}.cmake"
    if triplet_file.is_file():
        entries.append((triplet_file, f"triplets/{triplet_file.name}"))
    entries.extend(
        (path, f"downloads/{path.name}")
        for path in sorted((vcpkg_root / "downloads").iterdir())
        if path.is_file()
    )
    for name in package_names:
        overlay_port = package / "ports" / name
        port = overlay_port if overlay_port.is_dir() else vcpkg_root / "ports" / name
        if not port.is_dir():
            raise RuntimeError(f"missing vcpkg port recipe: {name}")
        entries.extend(
            (path, f"ports/{name}/{path.relative_to(port).as_posix()}")
            for path in sorted(port.rglob("*"))
            if path.is_file()
        )
    if not any(archive.startswith("downloads/") for _, archive in entries):
        raise RuntimeError("vcpkg downloads are empty; run install --only-downloads first")
    return entries


def _rebuild_script(triplet: str, has_overlay_triplet: bool) -> bytes:
    overlay = '["--overlay-triplets=triplets"]' if has_overlay_triplet else "[]"
    return f"""#!/usr/bin/env python3
import argparse
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--vcpkg-root", type=Path, required=True)
parser.add_argument("--install-root", type=Path, required=True)
args = parser.parse_args()
if {triplet.startswith("x64")} and shutil.which("nasm") is None:
    raise SystemExit("this x64 build requires nasm on PATH")
downloads = args.vcpkg_root / "downloads"
downloads.mkdir(parents=True, exist_ok=True)
for source in Path("downloads").iterdir():
    shutil.copy2(source, downloads / source.name)
command = [
    str(args.vcpkg_root / {('"vcpkg.exe"' if triplet.startswith("x64-windows") else '"vcpkg"')}),
    "install", "--x-manifest-root=.", "--overlay-ports=ports",
    "--triplet={triplet}", f"--x-install-root={{args.install_root}}",
] + {overlay}
subprocess.run(command, check=True)
""".encode()


def collect(vcpkg_root: Path, package: Path, triplet: str, output: Path) -> None:
    entries = _files(vcpkg_root, package, triplet)
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {archive}\n" for path, archive in entries
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        output.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for path, name in entries:
            info = archive.gettarinfo(str(path), name)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with path.open("rb") as source:
                archive.addfile(info, source)
        info = tarfile.TarInfo("SHA256SUMS")
        info.size = len(checksums)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(checksums))
        rebuild = _rebuild_script(triplet, any(name.startswith("triplets/") for _, name in entries))
        info = tarfile.TarInfo("rebuild.py")
        info.mode = 0o755
        info.size = len(rebuild)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(rebuild))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcpkg-root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--triplet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect(args.vcpkg_root, args.package, args.triplet, args.output)


if __name__ == "__main__":
    main()
