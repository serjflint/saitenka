from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
from collect_vcpkg_sources import collect
from verify_bundle_uninstall import verify
from verify_macos_bundle import parse_minos, parse_target

ROOT = Path(__file__).parents[1]


def test_collect_sources_includes_downloads_ports_and_checksums(tmp_path: Path) -> None:
    vcpkg = tmp_path / "vcpkg"
    (vcpkg / "downloads").mkdir(parents=True)
    (vcpkg / "downloads" / "fribidi-1.0.16.tar.xz").write_bytes(b"source")
    (vcpkg / "ports" / "fribidi").mkdir(parents=True)
    (vcpkg / "ports" / "fribidi" / "portfile.cmake").write_text("patch", encoding="utf-8")
    package = tmp_path / "package"
    package.mkdir()
    (package / "vcpkg.json").write_text("{}", encoding="utf-8")
    (package / "NATIVE_SOURCES.json").write_text(
        json.dumps({"packages": [{"name": "fribidi", "version": "1.0.16"}]}),
        encoding="utf-8",
    )

    output = tmp_path / "sources.tar.gz"
    triplets = package / "triplets"
    triplets.mkdir()
    (triplets / "x64-osx-saitenka.cmake").write_text("target", encoding="utf-8")
    collect(vcpkg, package, "x64-osx-saitenka", output)

    with tarfile.open(output) as archive:
        names = set(archive.getnames())
        assert "downloads/fribidi-1.0.16.tar.xz" in names
        assert "ports/fribidi/portfile.cmake" in names
        assert "triplets/x64-osx-saitenka.cmake" in names
        assert "rebuild.py" in names
        assert "SHA256SUMS" in names
        rebuild = archive.extractfile("rebuild.py")
        assert rebuild is not None
        rebuild_bytes = rebuild.read()
        assert b"--overlay-triplets=triplets" in rebuild_bytes
        assert b"this x64 build requires nasm on PATH" in rebuild_bytes

    windows_output = tmp_path / "windows-sources.tar.gz"
    collect(vcpkg, package, "x64-windows", windows_output)
    with tarfile.open(windows_output) as archive:
        rebuild = archive.extractfile("rebuild.py")
        assert rebuild is not None
        rebuild_bytes = rebuild.read()
        assert b"vcpkg.exe" in rebuild_bytes
        assert b"this x64 build requires nasm on PATH" not in rebuild_bytes


def test_collect_sources_rejects_binary_cache_without_downloads(tmp_path: Path) -> None:
    vcpkg = tmp_path / "vcpkg"
    (vcpkg / "downloads").mkdir(parents=True)
    (vcpkg / "ports" / "fribidi").mkdir(parents=True)
    package = tmp_path / "package"
    package.mkdir()
    (package / "vcpkg.json").write_text("{}", encoding="utf-8")
    (package / "NATIVE_SOURCES.json").write_text(
        json.dumps({"packages": [{"name": "fribidi", "version": "1"}]}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="downloads are empty"):
        collect(vcpkg, package, "x64-linux-dynamic", tmp_path / "sources.tar.gz")


def test_collect_sources_prefers_the_build_overlay_port(tmp_path: Path) -> None:
    vcpkg = tmp_path / "vcpkg"
    (vcpkg / "downloads").mkdir(parents=True)
    (vcpkg / "downloads" / "libass.tar.gz").write_bytes(b"source")
    (vcpkg / "ports" / "libass").mkdir(parents=True)
    (vcpkg / "ports" / "libass" / "portfile.cmake").write_text("ambient provider", encoding="utf-8")
    package = tmp_path / "package"
    (package / "ports" / "libass").mkdir(parents=True)
    (package / "ports" / "libass" / "portfile.cmake").write_text(
        "explicit provider", encoding="utf-8"
    )
    (package / "vcpkg.json").write_text("{}", encoding="utf-8")
    (package / "NATIVE_SOURCES.json").write_text(
        json.dumps({"packages": [{"name": "libass", "version": "0.17.5"}]}),
        encoding="utf-8",
    )

    output = tmp_path / "sources.tar.gz"
    collect(vcpkg, package, "x64-linux-dynamic", output)

    with tarfile.open(output) as archive:
        recipe = archive.extractfile("ports/libass/portfile.cmake")
        assert recipe is not None
        assert recipe.read() == b"explicit provider"


def test_bundle_build_locks_provider_and_linux_build_tools() -> None:
    workflow = (ROOT / ".github/workflows/libasslite-bundle-release.yml").read_text(
        encoding="utf-8"
    )
    port = (ROOT / "libasslite-bundle/ports/libass/portfile.cmake").read_text(encoding="utf-8")

    assert workflow.count("--overlay-ports=libasslite-bundle/ports") == 6
    assert "autoconf autoconf-archive automake libtool nasm" in workflow
    assert "-Dfontconfig=disabled -Dcoretext=enabled" in port
    assert "-Dfontconfig=disabled -Ddirectwrite=enabled" in port
    assert "-Dfontconfig=enabled" in port


def test_parse_macos_minimum_version() -> None:
    assert parse_minos("      cmd LC_BUILD_VERSION\n    minos 11.0\n") == (11, 0)


def test_parse_macos_minimum_version_requires_observation() -> None:
    with pytest.raises(RuntimeError, match="no minos"):
        parse_minos("cmd LC_VERSION_MIN_MACOSX")


def test_parse_macos_target_is_exact() -> None:
    assert parse_target("10.15") == (10, 15)
    with pytest.raises(ValueError, match="invalid macOS target"):
        parse_target("11")


def test_uninstall_verifier_rejects_owned_survivor(tmp_path: Path) -> None:
    survivor = tmp_path / "libasslite_bundle.libs" / "libass.so"
    survivor.parent.mkdir()
    survivor.write_bytes(b"native")
    manifest = tmp_path / "installed.json"
    manifest.write_text(
        json.dumps({"files": [str(survivor)], "roots": [str(survivor.parent)]}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="left owned paths"):
        verify(manifest)
