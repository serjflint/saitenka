"""Build pinned patched libass and mpv from local upstream clones into an isolated directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def run(command: list[str], *, env: dict | None = None, cwd: Path | None = None) -> None:
    subprocess.run(command, check=True, env=env, cwd=cwd, timeout=900)


def materialize(name: str, repo: Path, output: Path, manifest: dict) -> Path:
    source = output / name
    source.mkdir()
    archive = output / f"{name}.tar"
    run(["git", "-C", str(repo), "archive", manifest[name]["base"], "-o", str(archive)])
    run(["tar", "-xf", str(archive), "-C", str(source)])
    for filename in manifest[name]["patches"]:
        patch = Path(__file__).parent / filename
        if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest["sha256"][filename]:
            raise ValueError(f"patch digest mismatch: {filename}")
        run(["git", "apply", "--check", str(patch.resolve())], cwd=source)
        run(["git", "apply", str(patch.resolve())], cwd=source)
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mpv-repo", type=Path, required=True)
    parser.add_argument("--libass-repo", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    manifest = json.loads(Path(__file__).with_name("producer.json").read_text(encoding="utf-8"))
    prefix = output / "prefix"
    env = dict(os.environ, PKG_CONFIG_PATH=str(prefix / "lib/pkgconfig"))
    ass = materialize("libass", args.libass_repo, output, manifest)
    run(
        [
            "meson",
            "setup",
            str(ass / "build"),
            str(ass),
            "--prefix",
            str(prefix),
            "--libdir=lib",
            "-Dtest=enabled",
            "-Ddefault_library=static",
        ],
        env=env,
    )
    run(["meson", "compile", "-C", str(ass / "build")], env=env)
    run(["meson", "test", "-C", str(ass / "build"), "--print-errorlogs"], env=env)
    run(["meson", "install", "-C", str(ass / "build")], env=env)
    mpv = materialize("mpv", args.mpv_repo, output, manifest)
    run(
        [
            "meson",
            "setup",
            str(mpv / "build"),
            str(mpv),
            "-Dtests=true",
            "-Dlibmpv=false",
            "-Dmanpage-build=disabled",
            f"-Dc_link_args=-Wl,-rpath,{prefix / 'lib'}",
        ],
        env=env,
    )
    run(["meson", "compile", "-C", str(mpv / "build")], env=env)
    run(["meson", "test", "-C", str(mpv / "build"), "--print-errorlogs"], env=env)
    binary = mpv / "build/mpv"
    manifest["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    manifest["binary"] = str(binary)
    library = (prefix / "lib/libass.a").resolve(strict=True)
    link = subprocess.check_output(
        ["ninja", "-C", str(mpv / "build"), "-t", "commands", "mpv"],
        text=True,
        encoding="utf-8",
        timeout=30,
    ).splitlines()[-1]
    if str(library) not in link:
        raise ValueError("mpv did not link the built static libass")
    manifest["libass_library"] = str(library)
    manifest["libass_sha256"] = hashlib.sha256(library.read_bytes()).hexdigest()
    manifest["libass_linkage"] = "static"
    manifest["link_command"] = link
    (output / "receipt.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
