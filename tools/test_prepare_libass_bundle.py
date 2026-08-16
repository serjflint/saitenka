from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from prepare_libass_bundle import prepare

HOOK_PATH = Path(__file__).parents[1] / "libasslite-bundle" / "build_support.py"


def _install(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "installed"
    target = root / "x64-linux-dynamic"
    (target / "lib").mkdir(parents=True)
    (target / "share" / "libass").mkdir(parents=True)
    (target / "share" / "fribidi").mkdir(parents=True)
    (target / "lib" / "libass.so.9").write_bytes(b"ass")
    (target / "lib" / "libfribidi.so.0").write_bytes(b"fribidi")
    (target / "share" / "libass" / "copyright").write_text("ISC notice")
    (target / "share" / "fribidi" / "copyright").write_text("LGPL notice")
    (root / "vcpkg").mkdir()
    (root / "vcpkg" / "status").write_text(
        "Package: fribidi\nVersion: 1.0.16\nArchitecture: x64-linux-dynamic\n\n"
        "Package: libass\nVersion: 0.17.5\nArchitecture: x64-linux-dynamic\n"
    )
    package = tmp_path / "package"
    (package / "src" / "libasslite_bundle").mkdir(parents=True)
    return root, package


def test_prepare_copies_closure_and_verbatim_notices(tmp_path: Path) -> None:
    root, package = _install(tmp_path)

    manifest = prepare(root, "x64-linux-dynamic", package)

    assert manifest["library"] == ".libs/libass.so.9"
    assert manifest["files"] == ["libass.so.9", "libfribidi.so.0"]
    assert (
        json.loads((package / "src" / "libasslite_bundle" / "native-manifest.json").read_text())
        == manifest
    )
    notices = (package / "THIRD_PARTY_LICENSES").read_text()
    assert "ISC notice" in notices
    assert "LGPL notice" in notices
    sources = json.loads((package / "NATIVE_SOURCES.json").read_text())
    assert sources["packages"] == [
        {"name": "fribidi", "version": "1.0.16"},
        {"name": "libass", "version": "0.17.5"},
    ]


def test_prepare_rejects_static_only_install(tmp_path: Path) -> None:
    root, package = _install(tmp_path)
    (root / "x64-linux-dynamic" / "lib" / "libass.so.9").unlink()
    (root / "x64-linux-dynamic" / "lib" / "libfribidi.so.0").unlink()
    (root / "x64-linux-dynamic" / "lib" / "libass.a").write_bytes(b"static")

    with pytest.raises(RuntimeError, match="no dynamic libraries"):
        prepare(root, "x64-linux-dynamic", package)


def test_bundle_wheel_hook_rejects_source_only_payload() -> None:
    spec = importlib.util.spec_from_file_location("bundle_hatch_build", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(RuntimeError, match="staged native payload"):
        module.validate_payload(HOOK_PATH.parent)
