from __future__ import annotations

import json
import tarfile
from typing import TYPE_CHECKING

import pytest
from collect_vcpkg_sources import collect
from verify_macos_bundle import parse_minos

if TYPE_CHECKING:
    from pathlib import Path


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
    collect(vcpkg, package, output)

    with tarfile.open(output) as archive:
        names = set(archive.getnames())
        assert "downloads/fribidi-1.0.16.tar.xz" in names
        assert "ports/fribidi/portfile.cmake" in names
        assert "SHA256SUMS" in names


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
        collect(vcpkg, package, tmp_path / "sources.tar.gz")


def test_parse_macos_minimum_version() -> None:
    assert parse_minos("      cmd LC_BUILD_VERSION\n    minos 11.0\n") == (11, 0)


def test_parse_macos_minimum_version_requires_observation() -> None:
    with pytest.raises(RuntimeError, match="no minos"):
        parse_minos("cmd LC_VERSION_MIN_MACOSX")
